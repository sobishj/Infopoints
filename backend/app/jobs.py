"""Postgres job queue: enqueue (deduplicated), claim (SKIP LOCKED), complete, fail, requeue."""
import json
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

LANE_FOR_KIND = {"pdf": "doc", "office": "doc", "text": "doc", "sheet": "doc", "media": "media"}


def enqueue(db: Session, lane: str, type_: str, payload: dict | None = None, *, dedupe_key: str | None = None,
            file_id: int | None = None, folder_id: int | None = None, priority: int = 0,
            delay_seconds: float = 0) -> int | None:
    """Insert a queued job. Returns the job id, or None if an identical job is already queued."""
    row = db.execute(
        text("""
            INSERT INTO jobs (lane, type, payload, dedupe_key, file_id, folder_id, priority, run_after)
            VALUES (:lane, :type, CAST(:payload AS jsonb), :dedupe, :file_id, :folder_id, :priority,
                    now() + make_interval(secs => :delay))
            ON CONFLICT (dedupe_key) WHERE status = 'queued' DO NOTHING
            RETURNING id
        """),
        dict(lane=lane, type=type_, payload=json.dumps(payload or {}), dedupe=dedupe_key, file_id=file_id,
             folder_id=folder_id, priority=priority, delay=delay_seconds),
    ).first()
    return row[0] if row else None


def enqueue_index(db: Session, file_id: int, kind: str, seen: dict | None = None, delay_seconds: float = 0,
                  priority: int = 0) -> int | None:
    return enqueue(db, LANE_FOR_KIND.get(kind, "doc"), "index_file", {"file_id": file_id, "seen": seen},
                   dedupe_key=f"index:{file_id}", file_id=file_id, priority=priority, delay_seconds=delay_seconds)


def enqueue_scan(db: Session, folder_id: int, delay_seconds: float = 0, priority: int = 10) -> int | None:
    return enqueue(db, "system", "scan_folder", {"folder_id": folder_id}, dedupe_key=f"scan:{folder_id}",
                   folder_id=folder_id, priority=priority, delay_seconds=delay_seconds)


def claim(db: Session, lane: str, worker_id: str) -> dict | None:
    row = db.execute(
        text("""
            UPDATE jobs SET status = 'running', locked_by = :w, started_at = now(), heartbeat_at = now(),
                            attempts = attempts + 1, progress = 0, progress_msg = NULL
            WHERE id = (
                SELECT id FROM jobs
                WHERE lane = :lane AND status = 'queued' AND run_after <= now()
                ORDER BY priority DESC, id
                FOR UPDATE SKIP LOCKED
                LIMIT 1)
            RETURNING id, type, payload, attempts, max_attempts, file_id, folder_id
        """),
        dict(lane=lane, w=worker_id),
    ).mappings().first()
    db.commit()
    return dict(row) if row else None


def heartbeat(db: Session, job_id: int, progress: float | None = None, msg: str | None = None) -> None:
    db.execute(
        text("""UPDATE jobs SET heartbeat_at = now(), progress = COALESCE(:p, progress),
                                progress_msg = COALESCE(:m, progress_msg) WHERE id = :id"""),
        dict(id=job_id, p=progress, m=msg),
    )
    db.commit()


def complete(db: Session, job_id: int) -> None:
    db.execute(text("UPDATE jobs SET status = 'done', progress = 1, finished_at = now() WHERE id = :id"),
               dict(id=job_id))
    db.commit()


def fail(db: Session, job_id: int, error: str, retry: bool, attempts: int, max_attempts: int) -> None:
    if retry and attempts < max_attempts:
        db.execute(
            text("""UPDATE jobs SET status = 'queued', error = :e, locked_by = NULL,
                                    run_after = now() + make_interval(secs => :delay) WHERE id = :id"""),
            dict(id=job_id, e=error[:2000], delay=30 * attempts),
        )
    else:
        db.execute(text("UPDATE jobs SET status = 'failed', error = :e, finished_at = now() WHERE id = :id"),
                   dict(id=job_id, e=error[:2000]))
    db.commit()


def requeue(db: Session, job_id: int, payload: dict, delay_seconds: float) -> None:
    """Put a running job back in the queue (used by the file-stability gate). Doesn't count as an attempt.

    If an identical job was queued meanwhile, this one is dropped instead (the queued one covers it)."""
    dup = db.execute(
        text("SELECT 1 FROM jobs WHERE dedupe_key = (SELECT dedupe_key FROM jobs WHERE id = :id) "
             "AND status = 'queued'"), dict(id=job_id)).first()
    if dup:
        db.execute(text("UPDATE jobs SET status = 'cancelled', finished_at = now() WHERE id = :id"), dict(id=job_id))
    else:
        db.execute(
            text("""UPDATE jobs SET status = 'queued', payload = CAST(:p AS jsonb), attempts = attempts - 1,
                                    locked_by = NULL, run_after = now() + make_interval(secs => :d) WHERE id = :id"""),
            dict(id=job_id, p=json.dumps(payload), d=delay_seconds),
        )
    db.commit()


def reap_stale(db: Session, stale_seconds: int = 120) -> int:
    """Re-queue jobs whose worker stopped heart-beating (crash or shutdown mid-job)."""
    result = db.execute(
        text("""
            UPDATE jobs SET status = CASE WHEN attempts < max_attempts THEN 'queued' ELSE 'failed' END,
                            locked_by = NULL, error = 'worker stopped while running this job'
            WHERE status = 'running' AND heartbeat_at < now() - make_interval(secs => :s)
              AND NOT EXISTS (SELECT 1 FROM jobs q WHERE q.status = 'queued' AND q.dedupe_key = jobs.dedupe_key)
        """),
        dict(s=stale_seconds),
    )
    db.execute(
        text("""UPDATE jobs SET status = 'cancelled', finished_at = now()
                WHERE status = 'running' AND heartbeat_at < now() - make_interval(secs => :s)"""),
        dict(s=stale_seconds),
    )
    db.commit()
    return result.rowcount


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def seconds_ago(ts: datetime | None) -> float | None:
    return None if ts is None else (utcnow() - ts).total_seconds()
