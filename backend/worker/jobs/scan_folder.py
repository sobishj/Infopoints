"""Diff a folder against the files table: queue new/changed files, drop deleted ones."""
import logging
import os
import time

from sqlalchemy import select, text, update

from app.config import get_settings
from app.db.models import File, Folder
from app.db.session import session_scope
from app.filetypes import allowed_exts, is_ignored_name, kind_for
from app.jobs import enqueue_index
from worker.jobs import JobContext
from worker.jobs.index_file import mark_deleted

log = logging.getLogger("infopoint.ingest")

# Priority: files found after the first full scan (i.e. just added) jump ahead of the initial backlog.
PRIORITY_NEW, PRIORITY_BACKLOG = 5, 0
KIND_PRIORITY = {"text": 2, "pdf": 1, "office": 0}


def walk(base: str, include_subfolders: bool, exts: set[str]) -> dict[str, tuple[int, float, str]]:
    """Return {rel_path (posix): (size, mtime, kind)} for supported files under base."""
    found: dict[str, tuple[int, float, str]] = {}
    for dirpath, dirnames, filenames in os.walk(base):
        if include_subfolders:
            dirnames[:] = [d for d in dirnames if not d.startswith((".", "$", "~"))]
        else:
            dirnames[:] = []
        for name in filenames:
            ext = os.path.splitext(name)[1].lower()
            if is_ignored_name(name) or ext not in exts:
                continue
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            rel = os.path.relpath(full, base).replace(os.sep, "/")
            found[rel] = (st.st_size, st.st_mtime, kind_for(name))
    return found


def run(ctx: JobContext, payload: dict) -> None:
    folder_id = payload["folder_id"]
    with session_scope() as db:
        folder = db.get(Folder, folder_id)
        if folder is None or not folder.enabled:
            return
        base, include_sub, filters = folder.container_path, folder.include_subfolders, folder.file_type_filter
        first_scan = folder.last_scan_at is None
        project_id = db.execute(text("SELECT id FROM projects WHERE folder_id = :f AND parent_id IS NULL"),
                                {"f": folder_id}).scalar_one()

    if not os.path.isdir(base):
        with session_scope() as db:
            db.execute(update(Folder).where(Folder.id == folder_id).values(
                last_scan_at=text("now()"), last_scan_status="Folder not reachable"))
        log.warning("folder not reachable", extra={"event": "scan_failed", "folder_id": folder_id, "path": base})
        return

    started = time.time()
    found = walk(base, include_sub, allowed_exts(filters))
    model = get_settings().embedding_model
    added = changed = removed = 0
    with session_scope() as db:
        existing = {r.rel_path: r for r in db.execute(
            select(File.id, File.rel_path, File.size_bytes, File.mtime, File.status, File.embedding_model)
            .where(File.folder_id == folder_id)).all()}
        active = {fid for (fid,) in db.execute(
            text("SELECT j.file_id FROM jobs j JOIN files f ON f.id = j.file_id "
                 "WHERE f.folder_id = :f AND j.status IN ('queued', 'running')"), {"f": folder_id}).all()}
        now = time.time()
        for rel, (size, mtime, kind) in found.items():
            priority = (PRIORITY_BACKLOG if first_scan else PRIORITY_NEW) + KIND_PRIORITY.get(kind, 0)
            seen = {"size": size, "mtime": mtime, "at": now}
            r = existing.get(rel)
            if r is None:
                fid = db.execute(text("""
                    INSERT INTO files (folder_id, project_id, rel_path, file_name, ext, kind, size_bytes, mtime, status)
                    VALUES (:folder, :project, :rel, :name, :ext, :kind, :size, :mtime, 'queued') RETURNING id"""),
                    dict(folder=folder_id, project=project_id, rel=rel, name=os.path.basename(rel),
                         ext=os.path.splitext(rel)[1].lower(), kind=kind, size=size, mtime=mtime)).scalar_one()
                enqueue_index(db, fid, kind, seen, priority=priority)
                added += 1
            elif r.id in active:
                continue  # already queued or being indexed; the job re-reads the file when it runs
            elif r.status == "deleted" or r.size_bytes != size or r.mtime != mtime:
                if r.status == "deleted":
                    db.execute(update(File).where(File.id == r.id).values(status="queued"))
                enqueue_index(db, r.id, kind, seen, priority=priority)
                changed += 1
            elif r.status == "indexed" and r.embedding_model != model:
                enqueue_index(db, r.id, kind, {**seen, "at": 0}, priority=PRIORITY_BACKLOG)
            elif r.status in ("queued", "processing") and r.id not in active:
                enqueue_index(db, r.id, kind, {**seen, "at": 0}, priority=priority)  # lost job (e.g. crash)
        for rel, r in existing.items():
            if rel not in found and r.status != "deleted":
                mark_deleted(db, r.id)
                removed += 1
        db.execute(update(Folder).where(Folder.id == folder_id).values(
            last_scan_at=text("now()"), last_scan_status=f"OK · {len(found)} files"))
    log.info("folder scanned", extra={"event": "scan", "folder_id": folder_id, "files": len(found), "added": added,
                                      "changed": changed, "removed": removed,
                                      "seconds": round(time.time() - started, 2)})
