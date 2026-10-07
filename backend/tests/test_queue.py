"""Postgres job queue: dedupe, SKIP LOCKED claiming, lane isolation, stale-job recovery."""
from sqlalchemy import text

from app import jobs
from app.db.session import SessionLocal


def test_identical_queued_jobs_are_deduplicated(db):
    assert jobs.enqueue(db, "doc", "x", {}, dedupe_key="k") is not None
    assert jobs.enqueue(db, "doc", "x", {}, dedupe_key="k") is None
    db.commit()
    assert db.execute(text("SELECT count(*) FROM jobs")).scalar() == 1


def test_concurrent_claims_get_different_jobs(db):
    for i in range(2):
        jobs.enqueue(db, "doc", "x", {"i": i})
    db.commit()
    s1, s2 = SessionLocal(), SessionLocal()
    try:
        s1.begin()
        first = s1.execute(text("SELECT id FROM jobs WHERE status = 'queued' ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1")).scalar()
        j2 = jobs.claim(s2, "doc", "w2")  # must skip the row locked by s1
        assert j2 is not None and j2["id"] != first
    finally:
        s1.rollback()
        s1.close()
        s2.close()


def test_lanes_are_isolated(db):
    jobs.enqueue(db, "media", "transcribe", {})
    jobs.enqueue(db, "doc", "index_file", {})
    db.commit()
    with SessionLocal() as s:
        media = jobs.claim(s, "media", "w")  # a long media job is running…
    with SessionLocal() as s:
        doc = jobs.claim(s, "doc", "w")      # …and documents are still processed
    assert media["type"] == "transcribe" and doc["type"] == "index_file"


def test_stale_running_job_is_requeued(db):
    jobs.enqueue(db, "doc", "x", {}, dedupe_key="s")
    db.commit()
    with SessionLocal() as s:
        j = jobs.claim(s, "doc", "crashed-worker")
    db.execute(text("UPDATE jobs SET heartbeat_at = now() - interval '10 minutes' WHERE id = :id"), {"id": j["id"]})
    db.commit()
    with SessionLocal() as s:
        assert jobs.reap_stale(s) == 1
    assert db.execute(text("SELECT status FROM jobs WHERE id = :id"), {"id": j["id"]}).scalar() == "queued"
