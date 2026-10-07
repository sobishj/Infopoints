import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app.api import auth, folders, library, system
from app.auth.provider import hash_password
from app.config import get_settings
from app.db.models import LLMModel, User
from app.db.session import SessionLocal
from app.embed.embedder import EmbeddingModelMissing, embedder
from app.logging_setup import setup_logging
from app.store import encrypt_secret

log = logging.getLogger("infopoint.api")


def bootstrap() -> None:
    """First start: create the initial admin and seed the local Qwen model from .env."""
    s = get_settings()
    with SessionLocal() as db:
        if db.scalar(select(User).limit(1)) is None:
            db.add(User(username=s.initial_admin_user, display_name="Administrator", role="admin",
                        password_hash=hash_password(s.initial_admin_password)))
            log.info("created initial admin", extra={"event": "bootstrap_admin", "username": s.initial_admin_user})
        if db.scalar(select(LLMModel).limit(1)) is None:
            db.add(LLMModel(display_name="Qwen 1.5B (local, Bionic)", base_url=s.qwen_base_url,
                            model_name=s.qwen_model_name, api_key_enc=encrypt_secret(s.qwen_api_key),
                            context_length=s.qwen_context_length, temperature=0.1, max_output_tokens=700,
                            is_default=True, enabled=True))
            log.info("seeded default model", extra={"event": "bootstrap_model", "model": s.qwen_model_name})
        db.commit()


def _warm_embedder() -> None:
    try:
        embedder.encode(["warm-up"])
        log.info("embedding model ready", extra={"event": "embed_ready"})
    except EmbeddingModelMissing as e:
        log.error(str(e), extra={"event": "embed_missing"})
    except Exception:
        log.exception("embedding model failed to load")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging()
    bootstrap()
    threading.Thread(target=_warm_embedder, daemon=True).start()
    yield


app = FastAPI(title="InfoPoint", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
for r in (auth.router, folders.router, library.router, system.router):
    app.include_router(r)

static = get_settings().static_dir
if (static / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    """Serve the single-page app (client-side routing): real files first, index.html otherwise."""
    if path.startswith(("api/", "internal/")):
        raise HTTPException(404)
    candidate = (static / path).resolve()
    if path and candidate.is_file() and static.resolve() in candidate.parents:
        return FileResponse(candidate)
    index = static / "index.html"
    if not index.exists():
        raise HTTPException(404, "Frontend not built.")
    return FileResponse(index, headers={"Cache-Control": "no-cache"})
