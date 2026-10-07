"""Initial schema.

Revision ID: 0001
Revises:
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;

-- identity & access
CREATE TABLE users (
    id              SERIAL PRIMARY KEY,
    username        TEXT NOT NULL UNIQUE,
    display_name    TEXT,
    password_hash   TEXT,
    auth_source     TEXT NOT NULL DEFAULT 'local',
    role            TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at   TIMESTAMPTZ
);

CREATE TABLE sessions (
    id          TEXT PRIMARY KEY,
    user_id     INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    ip          TEXT,
    user_agent  TEXT
);
CREATE INDEX sessions_user ON sessions(user_id);

-- folders & projects
CREATE TABLE folders (
    id                          SERIAL PRIMARY KEY,
    display_name                TEXT NOT NULL,
    host_path                   TEXT NOT NULL,
    root_alias                  TEXT NOT NULL,
    container_path              TEXT NOT NULL,
    enabled                     BOOLEAN NOT NULL DEFAULT true,
    include_subfolders          BOOLEAN NOT NULL DEFAULT true,
    subfolders_as_subprojects   BOOLEAN NOT NULL DEFAULT false,
    file_type_filter            TEXT[],
    last_scan_at                TIMESTAMPTZ,
    last_scan_status            TEXT,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE projects (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    parent_id   INT REFERENCES projects(id) ON DELETE CASCADE,
    folder_id   INT NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
    rel_path    TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX projects_folder ON projects(folder_id);

CREATE TABLE user_projects (
    user_id     INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id  INT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, project_id)
);

-- files & content
CREATE TABLE files (
    id                  SERIAL PRIMARY KEY,
    folder_id           INT NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
    project_id          INT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    rel_path            TEXT NOT NULL,
    file_name           TEXT NOT NULL,
    ext                 TEXT NOT NULL,
    kind                TEXT NOT NULL,
    sha256              TEXT,
    size_bytes          BIGINT NOT NULL DEFAULT 0,
    mtime               DOUBLE PRECISION,
    status              TEXT NOT NULL DEFAULT 'queued'
                        CHECK (status IN ('queued', 'processing', 'indexed', 'failed', 'deleted')),
    error               TEXT,
    page_count          INT,
    duration_s          DOUBLE PRECISION,
    derived_pdf_path    TEXT,
    media_proxy_path    TEXT,
    embedding_model     TEXT,
    indexed_at          TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (folder_id, rel_path)
);
CREATE INDEX files_project_status ON files(project_id, status);

CREATE TABLE chunks (
    id              BIGSERIAL PRIMARY KEY,
    file_id         INT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    project_id      INT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    ordinal         INT NOT NULL,
    loc_type        TEXT NOT NULL CHECK (loc_type IN ('page', 'slide', 'sheet_rows', 'lines', 'heading', 'time')),
    page            INT,
    sheet           TEXT,
    row_start       INT,
    row_end         INT,
    line_start      INT,
    line_end        INT,
    heading         TEXT,
    t_start         DOUBLE PRECISION,
    t_end           DOUBLE PRECISION,
    source_header   TEXT NOT NULL,
    text            TEXT NOT NULL,
    token_count     INT NOT NULL,
    tsv             tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    embedding       vector(1024) NOT NULL
);
CREATE INDEX chunks_file ON chunks(file_id);
CREATE INDEX chunks_project ON chunks(project_id);
CREATE INDEX chunks_tsv ON chunks USING gin (tsv);
CREATE INDEX chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE transcript_segments (
    id          BIGSERIAL PRIMARY KEY,
    file_id     INT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    idx         INT NOT NULL,
    t_start     DOUBLE PRECISION NOT NULL,
    t_end       DOUBLE PRECISION NOT NULL,
    text        TEXT NOT NULL
);
CREATE INDEX transcript_segments_file ON transcript_segments(file_id, idx);

-- job queue
CREATE TABLE jobs (
    id              BIGSERIAL PRIMARY KEY,
    lane            TEXT NOT NULL CHECK (lane IN ('doc', 'media', 'system')),
    type            TEXT NOT NULL,
    payload         JSONB NOT NULL DEFAULT '{}',
    dedupe_key      TEXT,
    file_id         INT REFERENCES files(id) ON DELETE CASCADE,
    folder_id       INT REFERENCES folders(id) ON DELETE CASCADE,
    status          TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled')),
    priority        INT NOT NULL DEFAULT 0,
    attempts        INT NOT NULL DEFAULT 0,
    max_attempts    INT NOT NULL DEFAULT 3,
    progress        DOUBLE PRECISION,
    progress_msg    TEXT,
    locked_by       TEXT,
    heartbeat_at    TIMESTAMPTZ,
    run_after       TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    error           TEXT
);
CREATE INDEX jobs_claim ON jobs(lane, priority DESC, id) WHERE status = 'queued';
CREATE UNIQUE INDEX jobs_dedupe ON jobs(dedupe_key) WHERE status = 'queued';
CREATE INDEX jobs_running ON jobs(status, heartbeat_at) WHERE status = 'running';
CREATE INDEX jobs_file ON jobs(file_id);

-- models
CREATE TABLE models (
    id                  SERIAL PRIMARY KEY,
    display_name        TEXT NOT NULL,
    provider_type       TEXT NOT NULL DEFAULT 'openai_compatible',
    base_url            TEXT NOT NULL,
    model_name          TEXT NOT NULL,
    api_key_enc         BYTEA,
    context_length      INT NOT NULL DEFAULT 8192,
    temperature         REAL NOT NULL DEFAULT 0.1,
    max_output_tokens   INT NOT NULL DEFAULT 700,
    is_default          BOOLEAN NOT NULL DEFAULT false,
    enabled             BOOLEAN NOT NULL DEFAULT true,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX models_one_default ON models((true)) WHERE is_default;

-- conversations
CREATE TABLE conversations (
    id          SERIAL PRIMARY KEY,
    user_id     INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    model_id    INT REFERENCES models(id) ON DELETE SET NULL,
    project_ids INT[] NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX conversations_user ON conversations(user_id, updated_at DESC);

CREATE TABLE messages (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id INT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content         TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'ok',
    model_id        INT REFERENCES models(id) ON DELETE SET NULL,
    latency_ms      INT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX messages_conversation ON messages(conversation_id, id);

-- Frozen snapshot of each source shown with an answer (survives re-indexing).
CREATE TABLE message_citations (
    message_id  BIGINT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    marker      INT NOT NULL,
    chunk_id    BIGINT,
    file_id     INT,
    file_name   TEXT NOT NULL,
    loc_label   TEXT NOT NULL,
    page        INT,
    t_start     DOUBLE PRECISION,
    snippet     TEXT NOT NULL,
    cited       BOOLEAN NOT NULL DEFAULT true,
    file_sha256 TEXT,
    PRIMARY KEY (message_id, marker)
);

-- settings, audit, ops
CREATE TABLE settings (
    key         TEXT PRIMARY KEY,
    value       JSONB,
    updated_by  INT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE audit_log (
    id          BIGSERIAL PRIMARY KEY,
    user_id     INT,
    action      TEXT NOT NULL,
    entity      TEXT NOT NULL,
    entity_id   TEXT,
    before      JSONB,
    after       JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE service_heartbeats (
    service     TEXT PRIMARY KEY,
    instance    TEXT NOT NULL,
    last_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
    info        JSONB
);

CREATE TABLE bionic_events (
    id          BIGSERIAL PRIMARY KEY,
    action      TEXT NOT NULL,
    result      TEXT NOT NULL,
    detail      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

TABLES = [
    "bionic_events", "service_heartbeats", "audit_log", "settings", "message_citations", "messages",
    "conversations", "models", "jobs", "transcript_segments", "chunks", "files", "user_projects", "projects",
    "folders", "sessions", "users",
]


def upgrade() -> None:
    for statement in SCHEMA.split(";\n"):
        if statement.strip() and not all(l.strip().startswith("--") or not l.strip() for l in statement.splitlines()):
            op.execute(statement)


def downgrade() -> None:
    for t in TABLES:
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
