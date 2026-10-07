"""Ask pipeline: retrieve → gate → pack → stream from the LLM → validate citations → persist.
Yields (event, data) pairs that the API turns into Server-Sent Events."""
import logging
import re
import time
from typing import AsyncIterator

from sqlalchemy import func
from starlette.concurrency import run_in_threadpool

from app.access import allowed_project_ids
from app.db.models import Conversation, Message, MessageCitation, User
from app.db.session import SessionLocal
from app.embed.embedder import EmbeddingModelMissing, get_embedder
from app.llm.provider import LLMError, provider_for, resolve_model
from app.rag.citations import validate
from app.rag.code_guard import guard_code
from app.rag.prompt import NOT_FOUND, build_messages, pack_sources
from app.rag.retrieve import Retrieved, retrieve

log = logging.getLogger("infopoint.query")


def open_url(file_id: int, kind: str, page: int | None, t_start: float | None) -> str:
    if kind in ("pdf", "office"):
        return f"/api/files/{file_id}/pdf" + (f"#page={page}" if page else "")
    return f"/api/files/{file_id}/content"


def _snippet(text: str, n: int = 260) -> str:
    t = re.sub(r"\[Screenshot text\]\s*", "", text)
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + "…"


def source_card(n: int, c: Retrieved, cited: bool) -> dict:
    return {"n": n, "chunk_id": c.chunk_id, "file_id": c.file_id, "file_name": c.file_name, "kind": c.kind,
            "ext": c.ext, "label": c.label, "section": c.section, "page": c.page, "t_start": c.t_start, "snippet": _snippet(c.text),
            "open_url": open_url(c.file_id, c.kind, c.page, c.t_start), "cited": cited}


def _prepare(user_id: int, question: str, project_ids: list[int] | None, conversation_id: int | None,
             model_id: int | None):
    with SessionLocal() as db:
        user = db.get(User, user_id)
        projects = allowed_project_ids(db, user, project_ids)
        conv = db.get(Conversation, conversation_id) if conversation_id else None
        if conv is None or conv.user_id != user_id:
            conv = Conversation(user_id=user_id, title=question.strip()[:80], project_ids=projects,
                                model_id=model_id)
            db.add(conv)
            db.flush()
        cfg = resolve_model(db, model_id or conv.model_id)
        db.add(Message(conversation_id=conv.id, role="user", content=question))
        db.commit()
        if not projects:
            return conv.id, cfg, projects, [], False
        qvec = get_embedder().query(question)
        chunks, relevant = retrieve(db, question, qvec, projects)
        db.commit()
        return conv.id, cfg, projects, chunks, relevant


def _save(conv_id: int, text: str, status: str, model_id: int | None, latency_ms: int,
          cards: list[dict], sources: list[Retrieved]) -> int:
    sha = {c.chunk_id: c.file_sha256 for c in sources}
    with SessionLocal() as db:
        if db.get(Conversation, conv_id) is None:  # the chat was deleted while the answer was being written
            return 0
        msg = Message(conversation_id=conv_id, role="assistant", content=text, status=status, model_id=model_id,
                      latency_ms=latency_ms)
        db.add(msg)
        db.flush()
        for c in cards:
            db.add(MessageCitation(message_id=msg.id, marker=c["n"], chunk_id=c["chunk_id"], file_id=c["file_id"],
                                   file_name=c["file_name"], loc_label=c["label"], page=c["page"],
                                   t_start=c["t_start"], snippet=c["snippet"], cited=c["cited"],
                                   file_sha256=sha.get(c["chunk_id"])))
        db.execute(Conversation.__table__.update().where(Conversation.id == conv_id).values(updated_at=func.now()))
        db.commit()
        return msg.id


async def ask(user: User, question: str, project_ids: list[int] | None, conversation_id: int | None,
              model_id: int | None) -> AsyncIterator[tuple[str, dict]]:
    started = time.time()
    try:
        conv_id, cfg, projects, chunks, relevant = await run_in_threadpool(
            _prepare, user.id, question, project_ids, conversation_id, model_id)
    except LLMError as e:
        yield "error", {"message": str(e)}
        return
    except EmbeddingModelMissing as e:
        yield "error", {"message": f"Search isn't available yet: {e}"}
        return
    except Exception:
        log.exception("ask failed before generation")
        yield "error", {"message": "Something went wrong while searching the documents. See the server log."}
        return
    yield "meta", {"conversation_id": conv_id, "model": cfg.display_name}

    if not projects:
        text = "No document folders are available to you yet. An administrator can add one under Settings."
        msg_id = await run_in_threadpool(_save, conv_id, text, "no_projects", cfg.id, 0, [], [])
        yield "final", {"status": "no_projects", "text": text, "cited": [], "message_id": msg_id}
        return

    if not relevant:  # gate: don't let a small model improvise an answer
        related = [source_card(i, c, False) for i, c in enumerate(chunks[:3], 1) if (c.similarity or 0) >= 0.3]
        yield "sources", {"sources": related}
        msg_id = await run_in_threadpool(_save, conv_id, NOT_FOUND, "not_found", cfg.id,
                                         int((time.time() - started) * 1000), related, chunks)
        log.info("answer", extra={"event": "ask", "status": "not_found", "gate": True, "projects": projects,
                                  "ms": int((time.time() - started) * 1000)})
        yield "final", {"status": "not_found", "text": NOT_FOUND, "cited": [], "message_id": msg_id}
        return

    sources = pack_sources(chunks, question, cfg.context_length)
    yield "sources", {"sources": [source_card(i, c, False) for i, c in enumerate(sources, 1)]}

    parts: list[str] = []
    try:
        async for delta in provider_for(cfg).stream_chat(build_messages(question, sources)):
            parts.append(delta)
            yield "token", {"t": delta}
    except LLMError as e:
        log.warning("llm failed", extra={"event": "ask_error", "error": str(e)})
        await run_in_threadpool(_save, conv_id, str(e), "error", cfg.id, int((time.time() - started) * 1000), [], [])
        yield "error", {"message": f"The AI model isn't responding. {e}"}
        return

    # Which source continues which on the next page (same file and section), so samples aren't cut at page breaks.
    position = {(c.file_id, c.section, c.page): i for i, c in enumerate(sources) if c.page}
    next_of = {i: position[(c.file_id, c.section, c.page + 1)] for i, c in enumerate(sources)
               if c.page and (c.file_id, c.section, c.page + 1) in position}
    answer, code_replaced = guard_code("".join(parts), [c.text for c in sources], question, next_of)
    v = validate(answer, len(sources), [c.text for c in sources], question)
    status = "not_found" if v.not_found else "ok"
    cards = [source_card(i, c, i in v.cited) for i, c in enumerate(sources, 1)]
    if status == "not_found":
        cards = cards[:3]
    latency = int((time.time() - started) * 1000)
    msg_id = await run_in_threadpool(_save, conv_id, v.text, status, cfg.id, latency, cards, sources)
    log.info("answer", extra={"event": "ask", "status": status, "cited": v.cited, "invalid_markers": v.invalid, "regrounded": v.regrounded, "code_replaced": code_replaced, "supported": round(v.supported, 2),
                              "sources": len(sources), "ms": latency, "model": cfg.model_name})
    yield "final", {"status": status, "text": v.text, "cited": v.cited, "uncited": status == "ok" and not v.cited,
                    "sources": cards, "message_id": msg_id}
