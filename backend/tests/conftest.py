"""Test setup: a separate database (infopoint_test), a temporary document root and a deterministic fake embedder.

Run inside the worker image (it has Tesseract and LibreOffice):
    docker compose run --rm --no-deps -v ./backend:/app worker pytest -q
"""
import hashlib
import math
import os
import re
import shutil

TEST_ROOTS_BASE = "/tmp/ipt-roots"
os.environ.update({
    "DATABASE_URL": os.environ.get("TEST_DATABASE_URL",
                                   "postgresql+psycopg://infopoint:infopoint@db:5432/infopoint_test"),
    "ALLOWED_DOC_ROOTS": "/docs",
    "ROOTS_MOUNT_BASE": TEST_ROOTS_BASE,
    "DATA_DIR": "/tmp/ipt-data",
    "STABILITY_SECONDS": "0",
    "AUTO_LOGIN": "false",
    "MIN_VECTOR_SIMILARITY": "0.45",
})

import pytest  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()
DOCS = os.path.join(TEST_ROOTS_BASE, "docs")  # container path of host root "/docs"


def _ensure_database():
    url = get_settings().database_url
    admin = create_engine(url.rsplit("/", 1)[0] + "/infopoint", isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        if not c.execute(text("SELECT 1 FROM pg_database WHERE datname = 'infopoint_test'")).first():
            c.execute(text("CREATE DATABASE infopoint_test"))
    admin.dispose()
    eng = create_engine(url, isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    eng.dispose()
    from alembic import command
    from alembic.config import Config
    cfg = Config(os.path.join(os.path.dirname(os.path.dirname(__file__)), "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(os.path.dirname(__file__)), "alembic"))
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session", autouse=True)
def database():
    _ensure_database()
    yield


TABLES = ("bionic_events, service_heartbeats, audit_log, settings, message_citations, messages, conversations, "
          "models, jobs, transcript_segments, chunks, files, user_projects, projects, folders, sessions, users")


@pytest.fixture(autouse=True)
def clean(database):
    from app.db.session import engine
    with engine.begin() as c:
        c.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    shutil.rmtree(DOCS, ignore_errors=True)
    os.makedirs(DOCS, exist_ok=True)
    yield


@pytest.fixture
def db():
    from app.db.session import SessionLocal
    s = SessionLocal()
    yield s
    s.close()


# ---------------------------------------------------------------- fake embeddings
def fake_vector(text_: str) -> list[float]:
    """Bag-of-words hashed into 1024 dims: texts sharing words are similar, unrelated texts are not."""
    v = [0.0] * 1024
    for w in re.findall(r"[a-z0-9]+", text_.lower()):
        if len(w) < 3:
            continue
        h = int(hashlib.md5(w.encode()).hexdigest(), 16)
        v[h % 1024] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    if n == 1.0 and not any(v):
        v[0] = 1.0
    return [x / n for x in v]


@pytest.fixture(autouse=True)
def fake_embeddings(monkeypatch):
    import worker.jobs.index_file as idx
    monkeypatch.setattr(idx, "embed_passages", lambda texts, progress=None: [fake_vector(t) for t in texts])
    from app.embed import embedder as emb
    monkeypatch.setattr(emb.embedder, "encode", lambda texts: [fake_vector(t) for t in texts])


# ---------------------------------------------------------------- helpers
class NullCtx:
    job_id = 0

    def progress(self, fraction, message=None):
        pass


def run_jobs(max_rounds: int = 50) -> int:
    """Run queued jobs synchronously (system, doc, media lanes) until the queue is empty."""
    from app import jobs
    from app.db.session import SessionLocal
    from worker.jobs import Requeue
    from worker.main import HANDLERS
    ran = 0
    for _ in range(max_rounds):
        progressed = False
        for lane in ("system", "doc", "media"):
            with SessionLocal() as s:
                job = jobs.claim(s, lane, "test")
            if not job:
                continue
            progressed = True
            try:
                HANDLERS[job["type"]](NullCtx(), job["payload"])
                with SessionLocal() as s:
                    jobs.complete(s, job["id"])
            except Requeue as r:
                with SessionLocal() as s:
                    jobs.requeue(s, job["id"], r.payload, 0)
            ran += 1
        if not progressed:
            return ran
    raise AssertionError("job queue did not drain")


def make_pdf(path: str, pages: list[str]) -> None:
    import pymupdf as fitz
    doc = fitz.open()
    for body in pages:
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(50, 50, 550, 800), body, fontsize=10)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    doc.save(path)
    doc.close()


@pytest.fixture
def admin(db):
    from app.auth.provider import hash_password
    from app.db.models import User
    u = User(username="admin", role="admin", password_hash=hash_password("pw"))
    db.add(u)
    db.commit()
    return u
