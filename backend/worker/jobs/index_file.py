"""Index one file.

    locate → stability gate → snapshot + hash → extract (0–40%) → chunk → embed with cache (40–95%)
    → verify the file didn't change meanwhile → swap chunks in one transaction (100%)

Large files: progress is reported per page and per embedding batch, every embedded batch is cached, so an
interrupted file resumes where it stopped, and the original file is only open while it is copied.
"""
import hashlib
import logging
import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from sqlalchemy import delete, insert, select, text, update

from app.config import get_settings
from app.db.models import Chunk, File, Folder
from app.db.session import session_scope
from app.embed.cache import embed_cached
from app.embed.embedder import get_embedder
from app.filetypes import SLIDE_EXTS
from app.locations import section_title_text
from app.paths import safe_join
from worker.chunker import ChunkDraft, chunk_segments
from worker.extract import Extracted, ExtractionError
from worker.extract.office import convert_to_pdf
from worker.extract.pdf import extract_pdf
from worker.extract.text import extract_markdown, extract_text
from worker.jobs import JobContext, Requeue

log = logging.getLogger("infopoint.ingest")

EXTRACT_SHARE = 0.4   # progress share of extraction; embedding takes 0.4–0.95


@dataclass
class Target:
    file_id: int
    path: str
    name: str
    kind: str
    ext: str
    project_id: int
    sha256: str | None
    status: str
    embedding_model: str | None


# ---------------------------------------------------------------- small helpers
def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def mark_deleted(db, file_id: int) -> None:
    db.execute(delete(Chunk).where(Chunk.file_id == file_id))
    db.execute(update(File).where(File.id == file_id).values(status="deleted", sha256=None, error=None,
                                                              updated_at=text("now()")))


def set_file(file_id: int, **values) -> None:
    with session_scope() as db:
        db.execute(update(File).where(File.id == file_id).values(**values, updated_at=text("now()")))


def stability_gate(payload: dict, size: int, mtime: float, now: float, stability_seconds: float) -> None:
    """Raise Requeue until the file's size and mtime have been unchanged for `stability_seconds`."""
    if payload.get("force"):
        return
    seen = payload.get("seen") or {}
    if seen.get("size") == size and seen.get("mtime") == mtime and "at" in seen:
        wait = stability_seconds - (now - seen["at"])
        if wait > 0:
            raise Requeue(payload, wait)
        return
    raise Requeue({**payload, "seen": {"size": size, "mtime": mtime, "at": now}}, stability_seconds)


@contextmanager
def snapshot(path: str, ext: str) -> Iterator[str]:
    """Private copy of the file: users can save, move or delete the original while it is being indexed."""
    tmp = tempfile.mkdtemp(prefix="ingest_")
    try:
        copy = os.path.join(tmp, "file" + ext)
        shutil.copyfile(path, copy)
        yield copy
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- pipeline steps
def locate(file_id: int) -> Target | None:
    with session_scope() as db:
        row = db.execute(select(File, Folder).join(Folder, Folder.id == File.folder_id)
                         .where(File.id == file_id)).first()
        if row is None or not row.Folder.enabled:
            return None
        f, folder = row
        return Target(file_id=f.id, path=safe_join(folder.container_path, f.rel_path), name=f.file_name,
                      kind=f.kind, ext=f.ext, project_id=f.project_id, sha256=f.sha256, status=f.status,
                      embedding_model=f.embedding_model)


def extract(path: str, t: Target, ctx: JobContext) -> Extracted:
    s = get_settings()

    def page_progress(fraction: float, message: str) -> None:
        ctx.progress(EXTRACT_SHARE * fraction, message)

    if t.kind == "pdf":
        return extract_pdf(path, s.ocr_langs, progress=page_progress)
    if t.kind == "office":
        ctx.progress(0.02, "Converting to PDF")
        pdf = convert_to_pdf(path, str(s.data_dir / "derived" / str(t.file_id)))
        result = extract_pdf(pdf, s.ocr_langs, loc_type="slide" if t.ext in SLIDE_EXTS else "page",
                             progress=page_progress)
        result.derived_pdf_path = pdf
        return result
    if t.kind == "text":
        return extract_markdown(path) if t.ext in (".md", ".markdown") else extract_text(path)
    raise ExtractionError(f"No extractor for {t.kind} files yet.")


@dataclass
class Embedded:
    passages: list[list[float]]
    headings: dict[str, list[float]]   # section path → vector, for matching questions to sections
    reused: int


def embed(drafts: list[ChunkDraft], file_name: str, ctx: JobContext) -> Embedded:
    # File name and section path (not the page number) go into the embedded text: they tell search what the
    # passage is about, and passages that merely moved to another page after an edit still hit the cache.
    passages = ["\n".join(filter(None, (file_name, d.segment.heading, d.text))) for d in drafts]
    headings = sorted({d.segment.heading for d in drafts if d.segment.heading})
    model = get_settings().embedding_model
    total = len(passages) + len(headings)

    def report(done: int, _total: int) -> None:
        ctx.progress(EXTRACT_SHARE + (0.95 - EXTRACT_SHARE) * done / max(total, 1),
                     f"Embedding passage {done} of {total}")

    vectors, reused = embed_cached(model, passages, get_embedder().passages, report)
    # Titles are embedded one at a time, like questions: the int8 model's output shifts slightly with batch
    # composition, and title-vs-question margins are small.
    heading_vectors, _ = embed_cached(model, [section_title_text(h) for h in headings],
                                      lambda batch: [get_embedder().passages([t])[0] for t in batch],
                                      lambda done, _t: report(len(passages) + done, total))
    return Embedded(passages=vectors, headings=dict(zip(headings, heading_vectors)), reused=reused)


def swap_chunks(t: Target, drafts: list[ChunkDraft], emb: Embedded, digest: str, size: int,
                mtime: float, result: Extracted) -> None:
    rows = [dict(file_id=t.file_id, project_id=t.project_id, ordinal=d.ordinal, loc_type=d.segment.loc_type,
                 page=d.segment.page, line_start=d.segment.line_start, line_end=d.segment.line_end,
                 heading=d.segment.heading, source_header=d.header, text=d.text, token_count=d.token_count,
                 embedding=v, heading_embedding=emb.headings.get(d.segment.heading))
            for d, v in zip(drafts, emb.passages)]
    with session_scope() as db:  # one transaction: search never sees a half-indexed file
        db.execute(delete(Chunk).where(Chunk.file_id == t.file_id))
        for i in range(0, len(rows), 500):
            db.execute(insert(Chunk), rows[i:i + 500])
        db.execute(update(File).where(File.id == t.file_id).values(
            status="indexed", sha256=digest, size_bytes=size, mtime=mtime, page_count=result.page_count,
            derived_pdf_path=result.derived_pdf_path, embedding_model=get_settings().embedding_model,
            indexed_at=text("now()"), updated_at=text("now()"),
            error=None if rows else "No readable text found in this file."))


# ---------------------------------------------------------------- job entry point
def run(ctx: JobContext, payload: dict) -> None:
    s = get_settings()
    t = locate(payload["file_id"])
    if t is None:
        return
    if not os.path.isfile(t.path):
        with session_scope() as db:
            mark_deleted(db, t.file_id)
        log.info("file gone", extra={"event": "file_deleted", "file_id": t.file_id})
        return

    st = os.stat(t.path)
    stability_gate(payload, st.st_size, st.st_mtime, time.time(), s.stability_seconds)
    started = time.time()

    with snapshot(t.path, t.ext) as copy:
        digest = sha256_file(copy)
        unchanged = digest == t.sha256 and t.status == "indexed" and t.embedding_model == s.embedding_model
        if unchanged and not payload.get("force"):
            set_file(t.file_id, size_bytes=st.st_size, mtime=st.st_mtime)
            return
        set_file(t.file_id, status="processing", error=None)
        ctx.progress(0.0, "Reading file")
        try:
            result = extract(copy, t, ctx)
        except ExtractionError as e:
            with session_scope() as db:  # the old chunks describe a version that no longer exists
                db.execute(delete(Chunk).where(Chunk.file_id == t.file_id))
            set_file(t.file_id, status="failed", error=str(e), sha256=digest, size_bytes=st.st_size,
                     mtime=st.st_mtime)
            log.warning("extraction failed", extra={"event": "index_failed", "file_id": t.file_id, "error": str(e)})
            return

    drafts = chunk_segments(result.segments, t.name)
    embedded = embed(drafts, t.name, ctx)

    if not os.path.isfile(t.path):  # deleted while we worked
        with session_scope() as db:
            mark_deleted(db, t.file_id)
        return
    st2 = os.stat(t.path)
    if (st2.st_size, st2.st_mtime) != (st.st_size, st.st_mtime):  # edited meanwhile: index the new version
        raise Requeue({"file_id": t.file_id, "seen": {"size": st2.st_size, "mtime": st2.st_mtime,
                                                      "at": time.time()}}, s.stability_seconds)

    ctx.progress(0.97, "Saving")
    swap_chunks(t, drafts, embedded, digest, st.st_size, st.st_mtime, result)
    log.info("indexed", extra={"event": "indexed", "file_id": t.file_id, "file": t.name, "chunks": len(drafts),
                               "reused_embeddings": embedded.reused, "sections": len(embedded.headings), "pages": result.page_count,
                               "seconds": round(time.time() - started, 1)})
