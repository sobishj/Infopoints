"""Small helpers shared by api and worker: settings key/value store, audit log, secret encryption."""
import base64
import hashlib
from typing import Any

from cryptography.fernet import Fernet
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.config import get_settings

DEFAULT_SETTINGS: dict[str, Any] = {
    "setup_completed": False,
    "rescan_interval_seconds": None,  # None = use RESCAN_INTERVAL_SECONDS from .env
    "whisper_model": "small",
    "index_embedding_model": None,
}


def get_setting(db: Session, key: str) -> Any:
    row = db.execute(text("SELECT value FROM settings WHERE key = :k"), {"k": key}).first()
    return row[0] if row else DEFAULT_SETTINGS.get(key)


def all_settings(db: Session) -> dict[str, Any]:
    values = dict(DEFAULT_SETTINGS)
    values.update({k: v for k, v in db.execute(text("SELECT key, value FROM settings")).all()})
    if values["rescan_interval_seconds"] is None:
        values["rescan_interval_seconds"] = get_settings().rescan_interval_seconds
    return values


def set_setting(db: Session, key: str, value: Any, user_id: int | None = None) -> None:
    db.execute(
        text("""INSERT INTO settings (key, value, updated_by, updated_at) VALUES (:k, :v, :u, now())
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by,
                                                updated_at = now()""").bindparams(bindparam("v", type_=JSONB)),
        {"k": key, "v": value, "u": user_id},
    )


def audit(db: Session, user_id: int | None, action: str, entity: str, entity_id: Any = None,
          before: dict | None = None, after: dict | None = None) -> None:
    db.execute(
        text("""INSERT INTO audit_log (user_id, action, entity, entity_id, before, after)
                VALUES (:u, :a, :e, :id, :b, :af)""").bindparams(
            bindparam("b", type_=JSONB), bindparam("af", type_=JSONB)),
        {"u": user_id, "a": action, "e": entity, "id": None if entity_id is None else str(entity_id),
         "b": before, "af": after},
    )


def _fernet() -> Fernet:
    key = hashlib.sha256(("infopoint:" + get_settings().app_secret_key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(value: str | None) -> bytes | None:
    return _fernet().encrypt(value.encode()) if value else None


def decrypt_secret(value: bytes | None) -> str | None:
    return _fernet().decrypt(bytes(value)).decode() if value else None
