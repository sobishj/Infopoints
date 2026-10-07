from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.auth.deps import current_user, require_admin
from app.config import get_settings
from app.db.models import LLMModel, User
from app.db.session import get_db
from app.embed.embedder import is_installed
from app.llm.provider import provider_for, resolve_model
from app.store import all_settings, set_setting, audit

router = APIRouter(tags=["system"])


@router.get("/api/app/status")
def app_status(db: Session = Depends(get_db), user: User = Depends(current_user)):
    s = get_settings()
    folder_count = db.execute(text("SELECT count(*) FROM folders")).scalar_one()
    return {"mode": s.app_mode, "first_run": folder_count == 0, "folder_count": folder_count,
            "default_folder": s.default_doc_folder or None, "embedding_model": s.embedding_model,
            "is_admin": user.role == "admin"}


@router.get("/api/models")
def models(db: Session = Depends(get_db), _: User = Depends(current_user)):
    rows = db.execute(select(LLMModel).where(LLMModel.enabled).order_by(LLMModel.is_default.desc(),
                                                                        LLMModel.display_name)).scalars().all()
    return {"models": [{"id": m.id, "display_name": m.display_name, "is_default": m.is_default} for m in rows]}


class SettingsIn(BaseModel):
    rescan_interval_seconds: int | None = None
    whisper_model: str | None = None


@router.get("/api/settings")
def get_app_settings(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    values = all_settings(db)
    values["embedding_model"] = get_settings().embedding_model
    return values


@router.put("/api/settings")
def put_app_settings(body: SettingsIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    before = all_settings(db)
    changes = body.model_dump(exclude_none=True)
    if "rescan_interval_seconds" in changes and not 30 <= changes["rescan_interval_seconds"] <= 86400:
        raise HTTPException(400, "Rescan interval must be between 30 seconds and 24 hours.")
    if "whisper_model" in changes and changes["whisper_model"] not in ("tiny", "base", "small", "medium"):
        raise HTTPException(400, "Unknown Whisper model size.")
    for k, v in changes.items():
        set_setting(db, k, v, user.id)
    audit(db, user.id, "update", "settings", None, before={k: before.get(k) for k in changes}, after=changes)
    db.commit()
    return all_settings(db)


@router.get("/api/health")
async def health(db: Session = Depends(get_db)):
    out: dict = {"db": False}
    try:
        db.execute(text("SELECT 1"))
        out["db"] = True
        hb = db.execute(text("SELECT last_seen, now() - last_seen < interval '45 seconds' AS alive, info "
                             "FROM service_heartbeats WHERE service = 'worker'")).mappings().first()
        out["worker"] = {"alive": bool(hb and hb["alive"]), "last_seen": hb["last_seen"].isoformat() if hb else None}
        out["queue"] = dict(db.execute(text(
            "SELECT lane, count(*) FROM jobs WHERE status IN ('queued', 'running') GROUP BY lane")).all())
        out["folders"] = [dict(r) | {"last_scan_at": r["last_scan_at"].isoformat() if r["last_scan_at"] else None}
                          for r in db.execute(text("SELECT id, display_name, enabled, last_scan_at, last_scan_status "
                                                   "FROM folders ORDER BY id")).mappings()]
        cfg = resolve_model(db, None)
        ok, detail = await provider_for(cfg).health()
        out["llm"] = {"ok": ok, "detail": detail, "model": cfg.model_name, "base_url": cfg.base_url}
    except Exception as e:  # health must always answer
        out["error"] = f"{type(e).__name__}: {e}"
    out["embedding"] = {"model": get_settings().embedding_model, "installed": is_installed()}
    return out

