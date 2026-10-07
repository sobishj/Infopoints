import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.access import allowed_project_ids, can_open_file, visible_project_ids
from app.auth.deps import current_user, require_admin
from app.db.models import Conversation, File, Folder, Message, MessageCitation, User
from app.db.session import get_db
from app.jobs import enqueue_index
from app.paths import PathNotAllowed, safe_join
from app.rag.answer import ask

router = APIRouter(prefix="/api", tags=["library"])


@router.get("/projects")
def projects(db: Session = Depends(get_db), user: User = Depends(current_user)):
    ids = visible_project_ids(db, user)
    rows = db.execute(text("""
        SELECT p.id, p.name, count(f.id) FILTER (WHERE f.status = 'indexed') AS indexed,
               count(f.id) FILTER (WHERE f.status IN ('queued', 'processing')) AS pending
        FROM projects p LEFT JOIN files f ON f.project_id = p.id
        WHERE p.id = ANY(:ids) GROUP BY p.id ORDER BY p.name"""), {"ids": ids}).mappings().all()
    return {"projects": [dict(r) for r in rows]}


@router.get("/library")
def library(project_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)):
    ids = allowed_project_ids(db, user, [project_id] if project_id else None)
    rows = db.execute(text("""
        SELECT f.id, f.project_id, f.rel_path, f.file_name, f.ext, f.kind, f.size_bytes, f.status, f.error,
               f.page_count, f.indexed_at, j.progress, j.progress_msg, j.status AS job_status
        FROM files f
        LEFT JOIN LATERAL (SELECT progress, progress_msg, status FROM jobs
                           WHERE jobs.file_id = f.id AND jobs.status IN ('queued', 'running')
                           ORDER BY (jobs.status = 'running') DESC, id DESC LIMIT 1) j ON true
        WHERE f.project_id = ANY(:ids) AND f.status <> 'deleted'
        ORDER BY CASE f.status WHEN 'processing' THEN 0 WHEN 'failed' THEN 1 WHEN 'queued' THEN 2 ELSE 3 END,
                 f.rel_path"""), {"ids": ids}).mappings().all()
    files = [{**dict(r), "indexed_at": r["indexed_at"].isoformat() if r["indexed_at"] else None} for r in rows]
    summary = {s: sum(1 for f in files if f["status"] == s) for s in ("indexed", "queued", "processing", "failed")}
    return {"files": files, "summary": summary}


@router.post("/files/{file_id}/reindex")
def reindex(file_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    f = db.get(File, file_id)
    if f is None:
        raise HTTPException(404, "File not found.")
    f.status = "queued"
    enqueue_index(db, f.id, f.kind, None, priority=20)
    db.execute(text("UPDATE jobs SET payload = payload || '{\"force\": true}' WHERE file_id = :id AND status = 'queued'"),
               {"id": f.id})
    db.commit()
    return {"ok": True}


def _file_for_user(db: Session, user: User, file_id: int) -> tuple[File, Folder]:
    if not can_open_file(db, user, file_id):
        raise HTTPException(404, "File not found.")
    f = db.get(File, file_id)
    folder = db.get(Folder, f.folder_id)
    return f, folder


MIME = {".pdf": "application/pdf", ".txt": "text/plain; charset=utf-8", ".md": "text/plain; charset=utf-8",
        ".markdown": "text/plain; charset=utf-8"}


@router.get("/files/{file_id}/content")
def file_content(file_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Original file, streamed with HTTP range support (Starlette FileResponse handles Range)."""
    f, folder = _file_for_user(db, user, file_id)
    try:
        path = safe_join(folder.container_path, f.rel_path)
    except PathNotAllowed:
        raise HTTPException(404, "File not found.")
    media = MIME.get(f.ext, "application/octet-stream")
    inline = f.ext in MIME
    return FileResponse(path, media_type=media, filename=f.file_name,
                        content_disposition_type="inline" if inline else "attachment")


@router.get("/files/{file_id}/pdf")
def file_pdf(file_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """PDF rendition: the file itself, or the LibreOffice conversion for Office documents."""
    f, folder = _file_for_user(db, user, file_id)
    if f.kind == "pdf":
        return file_content(file_id, db, user)
    if not f.derived_pdf_path:
        raise HTTPException(404, "No PDF version of this file yet.")
    name = f.file_name.rsplit(".", 1)[0] + ".pdf"
    return FileResponse(f.derived_pdf_path, media_type="application/pdf", filename=name,
                        content_disposition_type="inline")


# ---------------------------------------------------------------- ask & conversations
class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    project_ids: list[int] | None = None
    conversation_id: int | None = None
    model_id: int | None = None


@router.post("/ask")
async def ask_endpoint(body: AskIn, user: User = Depends(current_user)):
    async def events():
        async for event, data in ask(user, body.question.strip(), body.project_ids, body.conversation_id,
                                     body.model_id):
            yield f"event: {event}\ndata: {json.dumps(data)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/conversations")
def conversations(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.execute(select(Conversation.id, Conversation.title, Conversation.updated_at)
                      .where(Conversation.user_id == user.id).order_by(Conversation.updated_at.desc())
                      .limit(30)).all()
    return {"conversations": [{"id": r.id, "title": r.title, "updated_at": r.updated_at.isoformat()} for r in rows]}


@router.get("/conversations/{conv_id}")
def conversation(conv_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    conv = db.get(Conversation, conv_id)
    if conv is None or conv.user_id != user.id:
        raise HTTPException(404, "Conversation not found.")
    msgs = db.execute(select(Message).where(Message.conversation_id == conv_id).order_by(Message.id)).scalars().all()
    cites = db.execute(select(MessageCitation).where(MessageCitation.message_id.in_([m.id for m in msgs]))
                       .order_by(MessageCitation.marker)).scalars().all()
    current = {r.id: r.sha256 for r in db.execute(
        select(File.id, File.sha256).where(File.id.in_({c.file_id for c in cites if c.file_id}))).all()}
    by_msg: dict[int, list] = {}
    for c in cites:
        changed = c.file_id not in current or current[c.file_id] != c.file_sha256
        by_msg.setdefault(c.message_id, []).append({
            "n": c.marker, "chunk_id": c.chunk_id, "file_id": c.file_id, "file_name": c.file_name,
            "label": c.loc_label, "page": c.page, "t_start": c.t_start, "snippet": c.snippet, "cited": c.cited,
            "source_changed": changed,
            "open_url": f"/api/files/{c.file_id}/pdf#page={c.page}" if c.page else f"/api/files/{c.file_id}/content"})
    return {"id": conv.id, "title": conv.title, "project_ids": conv.project_ids,
            "messages": [{"id": m.id, "role": m.role, "content": m.content, "status": m.status,
                          "sources": by_msg.get(m.id, [])} for m in msgs]}
