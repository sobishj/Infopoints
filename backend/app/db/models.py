"""ORM models. The schema itself is owned by the Alembic migrations (alembic/versions)."""
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text,
                        func)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _now():
    return mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------- identity & access
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True)
    display_name: Mapped[str | None]
    password_hash: Mapped[str | None]
    auth_source: Mapped[str] = mapped_column(default="local")
    role: Mapped[str] = mapped_column(default="user")
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = _now()
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserSession(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str | None]
    user_agent: Mapped[str | None]


class UserProject(Base):
    __tablename__ = "user_projects"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)


# ---------------------------------------------------------------- folders & projects
class Folder(Base):
    __tablename__ = "folders"
    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str]
    host_path: Mapped[str]
    root_alias: Mapped[str]
    container_path: Mapped[str]
    enabled: Mapped[bool] = mapped_column(default=True)
    include_subfolders: Mapped[bool] = mapped_column(default=True)
    subfolders_as_subprojects: Mapped[bool] = mapped_column(default=False)
    file_type_filter: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_scan_status: Mapped[str | None]
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    folder_id: Mapped[int] = mapped_column(ForeignKey("folders.id", ondelete="CASCADE"))
    rel_path: Mapped[str | None]
    created_at: Mapped[datetime] = _now()


# ---------------------------------------------------------------- files & content
class File(Base):
    __tablename__ = "files"
    id: Mapped[int] = mapped_column(primary_key=True)
    folder_id: Mapped[int] = mapped_column(ForeignKey("folders.id", ondelete="CASCADE"))
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    rel_path: Mapped[str]
    file_name: Mapped[str]
    ext: Mapped[str]
    kind: Mapped[str]
    sha256: Mapped[str | None]
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    mtime: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(default="queued")
    error: Mapped[str | None]
    page_count: Mapped[int | None]
    duration_s: Mapped[float | None] = mapped_column(Float)
    derived_pdf_path: Mapped[str | None]
    media_proxy_path: Mapped[str | None]
    embedding_model: Mapped[str | None]
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = _now()


class Chunk(Base):
    __tablename__ = "chunks"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"))
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    ordinal: Mapped[int]
    loc_type: Mapped[str]
    page: Mapped[int | None]
    sheet: Mapped[str | None]
    row_start: Mapped[int | None]
    row_end: Mapped[int | None]
    line_start: Mapped[int | None]
    line_end: Mapped[int | None]
    heading: Mapped[str | None]
    t_start: Mapped[float | None] = mapped_column(Float)
    t_end: Mapped[float | None] = mapped_column(Float)
    source_header: Mapped[str]
    text: Mapped[str]
    token_count: Mapped[int]
    embedding = mapped_column(Vector())  # dimension follows the embedding model (app/tools/prepare_index.py)
    heading_embedding = mapped_column(Vector(), nullable=True)  # section path, for question → section matching


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"))
    idx: Mapped[int]
    t_start: Mapped[float] = mapped_column(Float)
    t_end: Mapped[float] = mapped_column(Float)
    text: Mapped[str]


# ---------------------------------------------------------------- jobs
class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    lane: Mapped[str]
    type: Mapped[str]
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    dedupe_key: Mapped[str | None]
    file_id: Mapped[int | None] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"))
    folder_id: Mapped[int | None] = mapped_column(ForeignKey("folders.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(default="queued")
    priority: Mapped[int] = mapped_column(default=0)
    attempts: Mapped[int] = mapped_column(default=0)
    max_attempts: Mapped[int] = mapped_column(default=3)
    progress: Mapped[float | None] = mapped_column(Float)
    progress_msg: Mapped[str | None]
    locked_by: Mapped[str | None]
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    run_after: Mapped[datetime] = _now()
    created_at: Mapped[datetime] = _now()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None]


# ---------------------------------------------------------------- models
class LLMModel(Base):
    __tablename__ = "models"
    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str]
    provider_type: Mapped[str] = mapped_column(default="openai_compatible")
    base_url: Mapped[str]
    model_name: Mapped[str]
    api_key_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    context_length: Mapped[int] = mapped_column(default=8192)
    temperature: Mapped[float] = mapped_column(Float, default=0.1)
    max_output_tokens: Mapped[int] = mapped_column(default=700)
    is_default: Mapped[bool] = mapped_column(default=False)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


# ---------------------------------------------------------------- conversations
class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str]
    model_id: Mapped[int | None] = mapped_column(ForeignKey("models.id", ondelete="SET NULL"))
    project_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    role: Mapped[str]
    content: Mapped[str]
    status: Mapped[str] = mapped_column(default="ok")
    model_id: Mapped[int | None] = mapped_column(ForeignKey("models.id", ondelete="SET NULL"))
    latency_ms: Mapped[int | None]
    created_at: Mapped[datetime] = _now()


class MessageCitation(Base):
    __tablename__ = "message_citations"
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True)
    marker: Mapped[int] = mapped_column(primary_key=True)
    chunk_id: Mapped[int | None] = mapped_column(BigInteger)
    file_id: Mapped[int | None]
    file_name: Mapped[str]
    loc_label: Mapped[str]
    page: Mapped[int | None]
    t_start: Mapped[float | None] = mapped_column(Float)
    snippet: Mapped[str]
    cited: Mapped[bool] = mapped_column(default=True)
    file_sha256: Mapped[str | None]


# ---------------------------------------------------------------- settings, audit, ops
class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    updated_by: Mapped[int | None]
    updated_at: Mapped[datetime] = _now()


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None]
    action: Mapped[str]
    entity: Mapped[str]
    entity_id: Mapped[str | None]
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _now()


class ServiceHeartbeat(Base):
    __tablename__ = "service_heartbeats"
    service: Mapped[str] = mapped_column(String, primary_key=True)
    instance: Mapped[str]
    last_seen: Mapped[datetime] = _now()
    info: Mapped[dict | None] = mapped_column(JSONB)


class BionicEvent(Base):
    __tablename__ = "bionic_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    action: Mapped[str]
    result: Mapped[str]
    detail: Mapped[str | None]
    created_at: Mapped[datetime] = _now()
