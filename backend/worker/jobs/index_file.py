"""Index one file: stability gate → hash → extract → chunk → embed → atomic chunk swap."""
import hashlib
import logging
import os
import shutil
import tempfile
import time

from sqlalchemy import delete, insert, select, text, update

from app.config import get_settings
from app.db.models import Chunk, File, Folder
from app.db.session import session_scope
from app.embed.client import embed_passages
from app.filetypes import SLIDE_EXTS
from app.paths import safe_join
from worker.chunker import chunk_segments
from worker.extract import Extracted, ExtractionError
from worker.extract.office import convert_to_pdf
from worker.extract.pdf import extract_pdf
from worker.extract.text import extract_markdown, extract_text
from worker.jobs import JobContext, Requeue

log = logging.getLogger("infopoint.ingest")


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


def _set_status(file_id: int, **values) -> None:
    with session_scope() as db:
        db.execute(update(File).where(File.id == file_id).values(**values, updated_at=text("now()")))


def extract(path: str, kind: str, ext: str, file_id: int, progress) -> Extracted:
    s = get_settings()
    if kind == "pdf":
        return extract_pdf(path, s.ocr_langs, progress=progress)
    if kind == "office":
        progress(0.05, "Converting to PDF")
        pdf = convert_to_pdf(path, str(s.data_dir / "derived" / str(file_id)))
        result = extract_pdf(pdf, s.ocr_langs, loc_type="slide" if ext in SLIDE_EXTS else "page", progress=progress)
        result.derived_pdf_path = pdf
        return result
    if kind == "text":
        return extract_markdown(path) if ext in (".md", ".markdown") else extract_text(path)
    raise ExtractionError(f"No extractor for {kind} files yet.")


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


def run(ctx: JobContext, payload: dict) -> None:
    s = get_settings()
    file_id = payload["file_id"]
    with session_scope() as db:
        row = db.execute(select(File, Folder).join(Folder, Folder.id == File.folder_id)
                         .where(File.id == file_id)).first()
        if row is None or not row.Folder.enabled:
            return
        f, folder = row
        path = safe_join(folder.container_path, f.rel_path)
        meta = dict(name=f.file_name, kind=f.kind, ext=f.ext, sha=f.sha256, status=f.status,
                    model=f.embedding_model, project_id=f.project_id)

    if not os.path.isfile(path):
        with session_scope() as db:
            mark_deleted(db, file_id)
        log.info("file gone", extra={"event": "file_deleted", "file_id": file_id})
        return

    st = os.stat(path)
    stability_gate(payload, st.st_size, st.st_mtime, time.time(), s.stability_seconds)

    started = time.time()
    # Work on a private snapshot: the original is open only for the copy, so users can still save,
    # move or delete their file while it is being indexed (Windows refuses that for open files).
    snapshot_dir = tempfile.mkdtemp(prefix="ingest_")
    snapshot = os.path.join(snapshot_dir, "file" + meta["ext"])
    try:
        shutil.copyfile(path, snapshot)
        digest = sha256_file(snapshot)
        if (digest == meta["sha"] and meta["status"] == "indexed" and meta["model"] == s.embedding_model
                and not payload.get("force")):
            _set_status(file_id, size_bytes=st.st_size, mtime=st.st_mtime)
            return

        _set_status(file_id, status="processing", error=None)
        try:
            result = extract(snapshot, meta["kind"], meta["ext"], file_id, ctx.progress)
        except ExtractionError as e:
            with session_scope() as db:  # the old chunks describe a version that no longer exists
                db.execute(delete(Chunk).where(Chunk.file_id == file_id))
            _set_status(file_id, status="failed", error=str(e), sha256=digest, size_bytes=st.st_size,
                        mtime=st.st_mtime)
            log.warning("extraction failed", extra={"event": "index_failed", "file_id": file_id, "error": str(e)})
            return
    finally:
        shutil.rmtree(snapshot_dir, ignore_errors=True)

    drafts = chunk_segments(result.segments, meta["name"])
    ctx.progress(0.5, f"Embedding {len(drafts)} passages")
    vectors = embed_passages(
        [d.header + "\n" + d.text for d in drafts],
        progress=lambda done, total: ctx.progress(0.5 + 0.45 * done / max(total, 1),
                                                  f"Embedding {done} of {total} passages"))

    if not os.path.isfile(path):  # deleted while we worked
        with session_scope() as db:
            mark_deleted(db, file_id)
        return
    st2 = os.stat(path)
    if (st2.st_size, st2.st_mtime) != (st.st_size, st.st_mtime):  # edited while we worked: start over
        raise Requeue({"file_id": file_id, "seen": {"size": st2.st_size, "mtime": st2.st_mtime,
                                                    "at": time.time()}}, s.stability_seconds)

    rows = [dict(file_id=file_id, project_id=meta["project_id"], ordinal=d.ordinal, loc_type=d.segment.loc_type,
                 page=d.segment.page, line_start=d.segment.line_start, line_end=d.segment.line_end,
                 heading=d.segment.heading, source_header=d.header, text=d.text, token_count=d.token_count,
                 embedding=v) for d, v in zip(drafts, vectors)]
    with session_scope() as db:  # one transaction: search never sees a half-indexed file
        db.execute(delete(Chunk).where(Chunk.file_id == file_id))
        if rows:
            db.execute(insert(Chunk), rows)
        db.execute(update(File).where(File.id == file_id).values(
            status="indexed", sha256=digest, size_bytes=st.st_size, mtime=st.st_mtime,
            page_count=result.page_count, derived_pdf_path=result.derived_pdf_path,
            embedding_model=s.embedding_model, indexed_at=text("now()"), updated_at=text("now()"),
            error=None if rows else "No readable text found in this file."))
    log.info("indexed", extra={"event": "indexed", "file_id": file_id, "file": meta["name"], "chunks": len(rows),
                               "pages": result.page_count, "seconds": round(time.time() - started, 1)})
