"""Embedding cache keyed by (model, passage text).

Makes interrupted large files resume instead of restarting, and makes edited or duplicate documents
cheap to re-index: only passages whose text actually changed are embedded again."""
import hashlib
from typing import Callable

from pgvector.sqlalchemy import Vector
from sqlalchemy import bindparam, text

from app.db.session import SessionLocal

BATCH = 16


def text_key(model: str, passage: str) -> str:
    return hashlib.sha256(f"{model}\x00{passage}".encode()).hexdigest()


def embed_cached(model: str, passages: list[str], encode: Callable[[list[str]], list[list[float]]],
                 progress: Callable[[int, int], None] | None = None) -> tuple[list[list[float]], int]:
    """Return (vectors in input order, number reused from cache). New vectors are committed per batch."""
    keys = [text_key(model, p) for p in passages]
    vectors: dict[str, list[float]] = {}
    with SessionLocal() as db:
        for i in range(0, len(keys), 1000):
            rows = db.execute(text("SELECT text_hash, embedding FROM embedding_cache "
                                   "WHERE model = :m AND text_hash = ANY(:k)").columns(embedding=Vector()),
                              {"m": model, "k": keys[i:i + 1000]}).all()
            vectors.update({h: [float(x) for x in v] for h, v in rows})
    reused = sum(1 for k in keys if k in vectors)

    todo = [(k, p) for k, p in dict(zip(keys, passages)).items() if k not in vectors]
    done = reused
    if progress:
        progress(done, len(keys))
    insert = text("INSERT INTO embedding_cache (model, text_hash, embedding) VALUES (:m, :k, :v) "
                  "ON CONFLICT DO NOTHING").bindparams(bindparam("v", type_=Vector()))
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        new = encode([p for _, p in batch])
        with SessionLocal() as db:
            db.execute(insert, [{"m": model, "k": k, "v": v} for (k, _), v in zip(batch, new)])
            db.commit()
        for (k, _), v in zip(batch, new):
            vectors[k] = v
        done = sum(1 for k in keys if k in vectors)
        if progress:
            progress(done, len(keys))
    return [vectors[k] for k in keys], reused
