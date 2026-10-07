"""Document folder management: validation preview, create, update (incl. path change), delete."""
import os
import time
from dataclasses import dataclass

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.db.models import File, Folder, Project
from app.filetypes import allowed_exts, estimate_seconds, is_ignored_name, kind_for
from app.jobs import enqueue_scan
from app.paths import PathNotAllowed, resolve_host_path
from app.store import audit, set_setting

PREVIEW_MAX_SECONDS = 20
PREVIEW_MAX_ENTRIES = 200_000


@dataclass
class Preview:
    host_path: str
    container_path: str
    counts: dict[str, int]
    total_files: int
    total_bytes: int
    estimated_seconds: float
    partial: bool


def preview(host_path: str, include_subfolders: bool, file_types: list[str] | None) -> Preview:
    host, _alias, container = resolve_host_path(host_path)
    if not os.path.exists(container):
        raise PathNotAllowed("This folder doesn't exist (or isn't visible to InfoPoint).")
    if not os.path.isdir(container):
        raise PathNotAllowed("This path is a file, not a folder.")
    if not os.access(container, os.R_OK | os.X_OK):
        raise PathNotAllowed("InfoPoint can't read this folder (permission denied).")
    exts = allowed_exts(file_types)
    counts: dict[str, int] = {}
    total_bytes = 0
    est = 0.0
    seen = 0
    partial = False
    deadline = time.time() + PREVIEW_MAX_SECONDS
    for dirpath, dirnames, filenames in os.walk(container):
        dirnames[:] = [d for d in dirnames if not d.startswith((".", "$", "~"))] if include_subfolders else []
        for name in filenames:
            seen += 1
            ext = os.path.splitext(name)[1].lower()
            if is_ignored_name(name) or ext not in exts:
                continue
            try:
                size = os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                continue
            kind = kind_for(name)
            label = ext.lstrip(".")
            counts[label] = counts.get(label, 0) + 1
            total_bytes += size
            est += estimate_seconds(kind, size)
        if seen > PREVIEW_MAX_ENTRIES or time.time() > deadline:
            partial = True
            break
    return Preview(host_path=host, container_path=container, counts=dict(sorted(counts.items())),
                   total_files=sum(counts.values()), total_bytes=total_bytes, estimated_seconds=round(est),
                   partial=partial)


def folder_dict(f: Folder) -> dict:
    return {"id": f.id, "display_name": f.display_name, "host_path": f.host_path, "enabled": f.enabled,
            "include_subfolders": f.include_subfolders, "file_type_filter": f.file_type_filter or [],
            "last_scan_at": f.last_scan_at.isoformat() if f.last_scan_at else None,
            "last_scan_status": f.last_scan_status}


def create_folder(db: Session, user_id: int | None, display_name: str, host_path: str, enabled: bool = True,
                  include_subfolders: bool = True, file_types: list[str] | None = None) -> Folder:
    host, alias, container = resolve_host_path(host_path)
    if not os.path.isdir(container):
        raise PathNotAllowed("This folder doesn't exist (or isn't visible to InfoPoint).")
    dup = db.scalar(select(Folder).where(Folder.container_path == container))
    if dup:
        raise PathNotAllowed(f"This folder is already configured as “{dup.display_name}”.")
    folder = Folder(display_name=display_name.strip() or os.path.basename(host.rstrip("\\/")) or host,
                    host_path=host, root_alias=alias, container_path=container, enabled=enabled,
                    include_subfolders=include_subfolders, file_type_filter=file_types or None)
    db.add(folder)
    db.flush()
    db.add(Project(name=folder.display_name, folder_id=folder.id))
    audit(db, user_id, "create", "folder", folder.id, after=folder_dict(folder))
    set_setting(db, "setup_completed", True, user_id)
    if enabled:
        enqueue_scan(db, folder.id)
    db.commit()
    return folder


def update_folder(db: Session, user_id: int | None, folder: Folder, changes: dict) -> Folder:
    before = folder_dict(folder)
    path_changed = False
    if "host_path" in changes and changes["host_path"] is not None:
        host, alias, container = resolve_host_path(changes["host_path"])
        if container != folder.container_path:
            if not os.path.isdir(container):
                raise PathNotAllowed("This folder doesn't exist (or isn't visible to InfoPoint).")
            folder.host_path, folder.root_alias, folder.container_path = host, alias, container
            path_changed = True
    for key in ("display_name", "enabled", "include_subfolders"):
        if changes.get(key) is not None:
            setattr(folder, key, changes[key])
    if "file_type_filter" in changes:
        folder.file_type_filter = changes["file_type_filter"] or None
    if changes.get("display_name"):
        db.execute(text("UPDATE projects SET name = :n WHERE folder_id = :f AND parent_id IS NULL"),
                   {"n": folder.display_name, "f": folder.id})
    if path_changed:
        # The old location's index is no longer valid: drop it and index the new location from scratch.
        db.execute(delete(File).where(File.folder_id == folder.id))
        folder.last_scan_at = None
        folder.last_scan_status = None
    folder.updated_at = text("now()")
    db.flush()
    audit(db, user_id, "update", "folder", folder.id, before=before, after=folder_dict(folder))
    if folder.enabled:
        enqueue_scan(db, folder.id)
    db.commit()
    db.refresh(folder)
    return folder


def delete_folder(db: Session, user_id: int | None, folder: Folder) -> None:
    audit(db, user_id, "delete", "folder", folder.id, before=folder_dict(folder))
    db.delete(folder)  # cascades to projects, files, chunks and jobs
    db.commit()
