"""Make the vector index match the configured embedding model (runs at api start, after migrations).

When EMBEDDING_MODEL changes, vectors from the old model are meaningless: the chunks are dropped, the
vector column is resized to the new dimension, and every file is queued for re-indexing."""
import logging

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection

from app.config import get_settings
from app.db.session import engine
from app.embed.models import get_spec
from app.jobs import LANE_FOR_KIND
from app.logging_setup import setup_logging

log = logging.getLogger("infopoint.index")

# Bump when extraction/chunking changes in a way that existing indexes should pick up.
# 2: section headings on PDF passages
# 3: section-path embeddings
# 4: section titles embedded without numbers, camelCase split, one at a time
INDEX_VERSION = 5


def current_dimension(conn: Connection) -> int | None:
    return conn.execute(text("SELECT atttypmod FROM pg_attribute "
                             "WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'")).scalar()


def _set(conn: Connection, key: str, value) -> None:
    conn.execute(text("INSERT INTO settings (key, value) VALUES (:k, :v) "
                      "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()")
                 .bindparams(bindparam("v", type_=JSONB)), {"k": key, "v": value})


def _requeue_all(conn: Connection) -> int:
    """Queue every file for a forced re-index. Existing chunks stay searchable until each file is replaced."""
    conn.execute(text("DELETE FROM jobs WHERE type = 'index_file' AND status = 'queued'"))
    rows = conn.execute(text("SELECT id, kind FROM files WHERE status <> 'deleted'")).all()
    for fid, kind in rows:
        conn.execute(text("INSERT INTO jobs (lane, type, payload, dedupe_key, file_id) "
                          "VALUES (:lane, 'index_file', jsonb_build_object('file_id', :f, 'force', true), :d, :f) "
                          "ON CONFLICT (dedupe_key) WHERE status = 'queued' DO NOTHING"),
                     {"lane": LANE_FOR_KIND.get(kind, "doc"), "f": fid, "d": f"index:{fid}"})
    return len(rows)


def prepare(conn: Connection, model: str) -> bool:
    """Returns True when the index was reset for a new model. Also re-queues files after pipeline upgrades."""
    version = conn.execute(text("SELECT (value #>> '{}')::int FROM settings WHERE key = 'index_version'")).scalar()
    if prepare_model(conn, model):
        _set(conn, "index_version", INDEX_VERSION)
        return True
    if version != INDEX_VERSION:
        n = _requeue_all(conn)
        _set(conn, "index_version", INDEX_VERSION)
        if n:
            log.warning("indexing pipeline upgraded: re-indexing all files",
                        extra={"event": "reindex_upgrade", "from": version, "to": INDEX_VERSION, "files": n})
    return False


def prepare_model(conn: Connection, model: str) -> bool:
    """Returns True when the index was reset for a new model."""
    spec = get_spec(model)
    indexed_with = conn.execute(text("SELECT value #>> '{}' FROM settings WHERE key = 'index_embedding_model'")).scalar()
    if current_dimension(conn) == spec.dim and indexed_with in (None, model):
        if indexed_with is None:
            _set(conn, "index_embedding_model", model)
        return False

    log.warning("embedding model changed: re-indexing all files",
                extra={"event": "reindex_all", "from": indexed_with, "to": model, "dim": spec.dim})
    conn.execute(text("DROP INDEX IF EXISTS chunks_embedding_hnsw"))
    conn.execute(text("DELETE FROM chunks"))
    conn.execute(text(f"ALTER TABLE chunks ALTER COLUMN embedding TYPE vector({spec.dim})"))
    conn.execute(text("CREATE INDEX chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops)"))
    conn.execute(text("UPDATE files SET status = 'queued', sha256 = NULL, embedding_model = NULL "
                      "WHERE status <> 'deleted'"))
    conn.execute(text("DELETE FROM jobs WHERE type = 'index_file' AND status = 'running'"))
    _requeue_all(conn)
    _set(conn, "index_embedding_model", model)
    return True


def main() -> None:
    setup_logging()
    with engine.begin() as conn:
        prepare(conn, get_settings().embedding_model)


if __name__ == "__main__":
    main()
