import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app import folders as svc
from app import rootmap
from app.auth.deps import require_admin
from app.config import get_settings
from app.db.models import File, Folder, User
from app.db.session import get_db
from app.filetypes import TYPE_GROUPS
from app.jobs import enqueue_scan
from app.paths import PathNotAllowed, resolve_host_path, roots

router = APIRouter(prefix="/api", tags=["folders"])


class FolderIn(BaseModel):
    display_name: str = ""
    host_path: str
    enabled: bool = True
    include_subfolders: bool = True
    file_type_filter: list[str] | None = None


class FolderPatch(BaseModel):
    display_name: str | None = None
    host_path: str | None = None
    enabled: bool | None = None
    include_subfolders: bool | None = None
    file_type_filter: list[str] | None = None


class ValidateIn(BaseModel):
    host_path: str
    include_subfolders: bool = True
    file_type_filter: list[str] | None = None


STATUSES = ("indexed", "queued", "processing", "failed")


@router.get("/folders")
def list_folders(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    folders = db.execute(select(Folder).order_by(Folder.display_name)).scalars().all()
    counts = {row[0]: row[1:] for row in db.execute(
        select(File.folder_id, *[func.count().filter(File.status == s) for s in STATUSES])
        .group_by(File.folder_id)).all()}
    out = []
    for f in folders:
        d = svc.folder_dict(f)
        d["files"] = dict(zip(STATUSES, counts.get(f.id, (0, 0, 0, 0))))
        out.append(d)
    return {"folders": out, "type_groups": list(TYPE_GROUPS), "allowed_roots": [r.host for r in roots()],
            "default_folder": get_settings().default_doc_folder or None}


@router.post("/folders/validate")
async def validate_folder(body: ValidateIn, _: User = Depends(require_admin)):
    try:
        p = await run_in_threadpool(svc.preview, body.host_path, body.include_subfolders, body.file_type_filter)
    except PathNotAllowed as e:
        raise HTTPException(400, str(e))
    return {"host_path": p.host_path, "counts": p.counts, "total_files": p.total_files,
            "total_bytes": p.total_bytes, "estimated_seconds": p.estimated_seconds, "partial": p.partial}


@router.post("/folders")
def create_folder(body: FolderIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    try:
        f = svc.create_folder(db, user.id, body.display_name, body.host_path, body.enabled, body.include_subfolders,
                              body.file_type_filter)
    except PathNotAllowed as e:
        raise HTTPException(400, str(e))
    return svc.folder_dict(f)


@router.patch("/folders/{folder_id}")
def update_folder(folder_id: int, body: FolderPatch, db: Session = Depends(get_db),
                  user: User = Depends(require_admin)):
    f = db.get(Folder, folder_id)
    if f is None:
        raise HTTPException(404, "Folder not found.")
    try:
        f = svc.update_folder(db, user.id, f, body.model_dump(exclude_unset=True))
    except PathNotAllowed as e:
        raise HTTPException(400, str(e))
    return svc.folder_dict(f)


@router.delete("/folders/{folder_id}")
def delete_folder(folder_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    f = db.get(Folder, folder_id)
    if f is None:
        raise HTTPException(404, "Folder not found.")
    svc.delete_folder(db, user.id, f)
    return {"ok": True}


@router.post("/folders/{folder_id}/rescan")
def rescan(folder_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    if db.get(Folder, folder_id) is None:
        raise HTTPException(404, "Folder not found.")
    enqueue_scan(db, folder_id, priority=20)
    db.commit()
    return {"ok": True}


@router.post("/setup/default-folder")
def use_default_folder(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """First run, user didn't choose a folder: index the system Downloads folder."""
    path = get_settings().default_doc_folder
    if not path:
        raise HTTPException(400, "No default folder is configured (DEFAULT_DOC_FOLDER in .env).")
    try:
        _host, _alias, container = resolve_host_path(path)
    except PathNotAllowed as e:
        raise HTTPException(400, str(e))
    existing = db.scalar(select(Folder).where(Folder.container_path == container))
    if existing:
        return svc.folder_dict(existing)
    try:
        f = svc.create_folder(db, user.id, "Downloads", path)
    except PathNotAllowed as e:
        raise HTTPException(400, str(e))
    return svc.folder_dict(f)


# ---------------------------------------------------------------- server-side folder browser
@router.get("/fs/list")
def fs_list(path: str | None = None, _: User = Depends(require_admin)):
    """List sub-folders of a host path inside the allowed roots (no path = the roots themselves)."""
    all_roots = roots()
    if not path:
        return {"path": None, "breadcrumbs": [], "parent": None,
                "entries": [{"name": r.host, "path": r.host} for r in all_roots]}
    try:
        host, _alias, container = resolve_host_path(path)
    except PathNotAllowed as e:
        raise HTTPException(400, str(e))
    if not os.path.isdir(container):
        raise HTTPException(404, "Folder not found.")
    names = []
    try:
        with os.scandir(container) as it:
            for e in it:
                try:
                    if e.is_dir() and not e.name.startswith((".", "$")):
                        names.append(e.name)
                except OSError:
                    continue
    except PermissionError:
        raise HTTPException(403, "Permission denied.")
    names.sort(key=str.lower)

    root, rest = rootmap.find_root(host, all_roots)
    sep = "/" if root.kind == "posix" else "\\"

    def join(base: str, name: str) -> str:
        return base.rstrip(sep) + sep + name

    crumbs = [{"name": root.host, "path": root.host}]
    for comp in rest:
        crumbs.append({"name": comp, "path": join(crumbs[-1]["path"], comp)})
    return {"path": host, "breadcrumbs": crumbs, "parent": crumbs[-2]["path"] if len(crumbs) > 1 else None,
            "entries": [{"name": n, "path": join(host, n)} for n in names]}
