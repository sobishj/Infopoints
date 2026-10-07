"""Big files: steady progress, embedding cache (resume + cheap re-index), interrupted jobs, model switches."""
import os
import time

import pytest
from sqlalchemy import text

from app import folders as svc
from app import jobs
from app.config import get_settings
from app.embed.cache import embed_cached
from app.embed.models import get_spec
from tests.conftest import DOCS, NullCtx, make_pdf, run_jobs
from worker.jobs import index_file, scan_folder

PARAGRAPH = ("The settlement service reconciles merchant payouts every night and exposes a REST endpoint "
             "for querying the status of each transfer. ")


class RecordingCtx:
    job_id = 0

    def __init__(self):
        self.updates: list[tuple[float, str]] = []

    def progress(self, fraction, message=None):
        if fraction is not None:
            self.updates.append((fraction, message))


@pytest.fixture
def folder(db, admin):
    os.makedirs(os.path.join(DOCS, "big"))
    return svc.create_folder(db, admin.id, "Big", "/docs/big")


def _file_id(db, name):
    return db.execute(text("SELECT id FROM files WHERE file_name = :n"), {"n": name}).scalar()


def test_large_pdf_indexes_with_steady_progress(db, folder):
    pages = [f"Section {i}. " + PARAGRAPH * 12 for i in range(1, 151)]
    make_pdf(os.path.join(DOCS, "big", "api_reference.pdf"), pages)
    scan_folder.run(NullCtx(), {"folder_id": folder.id})  # register the file; index it with a recording context
    ctx = RecordingCtx()
    index_file.run(ctx, {"file_id": _file_id(db, "api_reference.pdf"), "force": True})

    assert db.execute(text("SELECT status FROM files WHERE file_name = 'api_reference.pdf'")).scalar() == "indexed"
    assert db.execute(text("SELECT count(DISTINCT page) FROM chunks")).scalar() == 150
    fractions = [f for f, _ in ctx.updates]
    assert fractions == sorted(fractions), "progress must never go backwards"
    assert len(ctx.updates) > 150, "progress is reported per page and per embedding batch"
    assert any("Reading page 150 of 150" in (m or "") for _, m in ctx.updates)
    assert any((m or "").startswith("Embedding passage") for _, m in ctx.updates)


def test_edit_only_re_embeds_changed_passages(db, folder, fake_embedder):
    path = os.path.join(DOCS, "big", "guide.pdf")
    make_pdf(path, [f"Chapter {i}. " + PARAGRAPH * 3 for i in range(1, 21)])
    run_jobs()
    first = fake_embedder.passage_calls
    assert first >= 20

    pages = [f"Chapter {i}. " + PARAGRAPH * 3 for i in range(1, 21)]
    pages[4] = "Chapter 5. The payout cut-off time moved to 18:00 IST."
    make_pdf(path, pages)
    os.utime(path, (time.time() + 5, time.time() + 5))
    jobs.enqueue_scan(db, folder.id)
    db.commit()
    run_jobs()
    assert fake_embedder.passage_calls - first == 1, "only the edited passage is embedded again"
    assert db.execute(text("SELECT count(*) FROM chunks WHERE text LIKE '%18:00 IST%'")).scalar() == 1


def test_interrupted_embedding_resumes_from_cache():
    model = get_settings().embedding_model
    dim = get_spec(model).dim
    passages = [f"passage number {i}" for i in range(40)]
    calls = []

    def flaky(batch):
        calls.append(len(batch))
        if len(calls) == 2:
            raise RuntimeError("worker stopped")
        return [[1.0] + [0.0] * (dim - 1) for _ in batch]

    with pytest.raises(RuntimeError):
        embed_cached(model, passages, flaky)
    vectors, reused = embed_cached(model, passages, lambda b: [[0.0, 1.0] + [0.0] * (dim - 2) for _ in b])
    assert reused == 16, "the batch finished before the interruption is reused"
    assert len(vectors) == 40


def test_interrupted_job_shows_waiting_not_stuck(db, folder):
    make_pdf(os.path.join(DOCS, "big", "a.pdf"), ["hello"])
    scan_folder.run(NullCtx(), {"folder_id": folder.id})
    fid = _file_id(db, "a.pdf")
    db.execute(text("UPDATE files SET status = 'processing' WHERE id = :f"), {"f": fid})
    db.execute(text("INSERT INTO jobs (lane, type, payload, file_id, status, heartbeat_at, progress, attempts) "
                    "VALUES ('doc', 'index_file', '{}', :f, 'running', now() - interval '10 minutes', 0.68, 1)"),
               {"f": fid})
    db.commit()
    jobs.reap_stale(db)
    row = db.execute(text("SELECT f.status, j.status, j.progress FROM files f JOIN jobs j ON j.file_id = f.id "
                          "WHERE f.id = :f AND j.type = 'index_file' ORDER BY j.id DESC LIMIT 1"), {"f": fid}).one()
    assert tuple(row) == ("queued", "queued", None)


def test_switching_embedding_model_resizes_index_and_requeues(db, folder):
    from app.db.session import engine
    from app.tools.prepare_index import current_dimension, prepare
    make_pdf(os.path.join(DOCS, "big", "a.pdf"), ["hello world"])
    run_jobs()
    configured = get_settings().embedding_model
    other = "bge-m3" if configured != "bge-m3" else "multilingual-e5-small"
    try:
        db.commit()
        with engine.begin() as conn:
            assert prepare(conn, other) is True
            assert current_dimension(conn) == get_spec(other).dim
        assert db.execute(text("SELECT count(*) FROM chunks")).scalar() == 0
        db.commit()
        assert db.execute(text("SELECT status FROM files")).scalar() == "queued"
        assert db.execute(text("SELECT count(*) FROM jobs WHERE type = 'index_file' AND status = 'queued'")).scalar() == 1
        with engine.begin() as conn:
            assert prepare(conn, other) is False  # idempotent
    finally:
        db.rollback()  # release the test session's read locks before the schema change
        with engine.begin() as conn:
            prepare(conn, configured)
