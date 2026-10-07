"""InfoPoint worker: job lanes (doc ×N, media ×1, system ×1), folder watcher, rescan scheduler, heartbeat."""
import json
import logging
import os
import signal
import socket
import threading
import time

from sqlalchemy import text

from app import jobs
from app.config import get_settings
from app.db.session import SessionLocal, session_scope
from app.embed.embedder import EmbeddingModelMissing, get_embedder
from app.logging_setup import setup_logging
from app.store import all_settings, get_setting
from worker.jobs import Requeue, index_file, scan_folder
from worker.watcher import WatcherManager

log = logging.getLogger("infopoint.worker")

HANDLERS = {"index_file": index_file.run, "scan_folder": scan_folder.run}
SHUTDOWN_GRACE = 50  # seconds; docker stop_grace_period is 60


class Ctx:
    """Passed to handlers. A ticker thread keeps the heartbeat alive during long silent steps (OCR, LibreOffice)."""

    MIN_INTERVAL = 1.0  # seconds between progress writes (per-page updates on big files would flood the DB)

    def __init__(self, job_id: int):
        self.job_id = job_id
        self._last = 0.0
        self._done = threading.Event()
        self._ticker = threading.Thread(target=self._tick, daemon=True)
        self._ticker.start()

    def _tick(self):
        while not self._done.wait(20):
            self.progress(None, force=True)

    def progress(self, fraction: float | None, message: str | None = None, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last < self.MIN_INTERVAL:
            return
        self._last = now
        try:
            with SessionLocal() as db:
                jobs.heartbeat(db, self.job_id, fraction, message)
        except Exception:
            log.exception("heartbeat failed", extra={"job_id": self.job_id})

    def close(self):
        self._done.set()


class Worker:
    def __init__(self):
        self.s = get_settings()
        self.id = f"{socket.gethostname()}:{os.getpid()}"
        self.stop = threading.Event()
        self.current: dict[str, int] = {}

    # ---------------------------------------------------------------- job lanes
    def lane_loop(self, lane: str, slot: int) -> None:
        name = f"{lane}-{slot}"
        while not self.stop.is_set():
            try:
                with SessionLocal() as db:
                    job = jobs.claim(db, lane, self.id)
            except Exception:
                log.exception("claim failed", extra={"lane": lane})
                self.stop.wait(5)
                continue
            if job is None:
                self.stop.wait(1.0)
                continue
            self.current[name] = job["id"]
            try:
                self.run_job(job)
            finally:
                self.current.pop(name, None)

    def run_job(self, job: dict) -> None:
        ctx = Ctx(job["id"])
        started = time.time()
        try:
            HANDLERS[job["type"]](ctx, job["payload"])
            with SessionLocal() as db:
                jobs.complete(db, job["id"])
            log.info("job done", extra={"event": "job_done", "job_id": job["id"], "type": job["type"],
                                        "seconds": round(time.time() - started, 2)})
        except Requeue as r:
            with SessionLocal() as db:
                jobs.requeue(db, job["id"], r.payload, r.delay_seconds)
                jobs.reset_orphaned_processing(db)
        except Exception as e:
            log.exception("job failed", extra={"event": "job_failed", "job_id": job["id"], "type": job["type"]})
            with SessionLocal() as db:
                jobs.fail(db, job["id"], f"{type(e).__name__}: {e}", retry=True, attempts=job["attempts"],
                          max_attempts=job["max_attempts"])
                if job.get("file_id") and job["attempts"] >= job["max_attempts"]:
                    db.execute(text("UPDATE files SET status = 'failed', error = :e WHERE id = :id"),
                               {"e": f"{type(e).__name__}: {e}"[:2000], "id": job["file_id"]})
                    db.commit()
        finally:
            ctx.close()

    # ---------------------------------------------------------------- housekeeping
    def scheduler_loop(self) -> None:
        """Periodic full rescans (network shares miss events), stale-job reaping, heartbeat."""
        last_reap = 0.0
        while not self.stop.is_set():
            try:
                with session_scope() as db:
                    interval = int(all_settings(db)["rescan_interval_seconds"])
                    due = db.execute(text(
                        "SELECT id FROM folders WHERE enabled AND (last_scan_at IS NULL OR "
                        "last_scan_at < now() - make_interval(secs => :i))"), {"i": interval}).scalars().all()
                    for fid in due:
                        jobs.enqueue_scan(db, fid, priority=1)
                    db.execute(text("""
                        INSERT INTO service_heartbeats (service, instance, last_seen, info)
                        VALUES ('worker', :inst, now(), CAST(:info AS jsonb))
                        ON CONFLICT (service) DO UPDATE SET instance = EXCLUDED.instance, last_seen = now(),
                                                            info = EXCLUDED.info"""),
                        {"inst": self.id, "info": json.dumps({"running": self.current})})
                if time.time() - last_reap > 30:
                    with SessionLocal() as db:
                        n = jobs.reap_stale(db)
                    if n:
                        log.warning("re-queued stale jobs", extra={"event": "reaped", "count": n})
                    last_reap = time.time()
            except Exception:
                log.exception("scheduler tick failed")
            self.stop.wait(10)

    def release_running(self) -> None:
        """On shutdown, hand unfinished jobs straight back to the queue (resume on next start)."""
        with SessionLocal() as db:
            n = db.execute(text("""
                UPDATE jobs SET status = 'queued', attempts = GREATEST(attempts - 1, 0), locked_by = NULL,
                                progress = NULL, progress_msg = NULL
                WHERE status = 'running' AND locked_by = :w
                  AND NOT EXISTS (SELECT 1 FROM jobs q WHERE q.status = 'queued' AND q.dedupe_key = jobs.dedupe_key)
            """), {"w": self.id}).rowcount
            db.commit()
            jobs.reset_orphaned_processing(db)
        if n:
            log.info("released unfinished jobs", extra={"event": "jobs_released", "count": n})

    def wait_until_ready(self) -> None:
        """Wait for migrations, the index preparation for the configured embedding model, and the model files."""
        while not self.stop.is_set():
            try:
                with SessionLocal() as db:
                    if get_setting(db, "index_embedding_model") != self.s.embedding_model:
                        raise RuntimeError("index not prepared for the configured embedding model yet")
                get_embedder()
                return
            except EmbeddingModelMissing as e:
                log.error(str(e), extra={"event": "embed_missing"})
                self.stop.wait(30)
            except Exception as e:
                log.info("waiting for database: %s", e)
                self.stop.wait(3)

    def run(self) -> None:
        signal.signal(signal.SIGTERM, lambda *_: self.stop.set())
        signal.signal(signal.SIGINT, lambda *_: self.stop.set())
        self.wait_until_ready()
        with SessionLocal() as db:
            jobs.reap_stale(db, stale_seconds=60)
            jobs.reset_orphaned_processing(db)
        with session_scope() as db:  # catch up on anything that changed while we were down
            for (fid,) in db.execute(text("SELECT id FROM folders WHERE enabled")).all():
                jobs.enqueue_scan(db, fid)

        threads = [threading.Thread(target=self.lane_loop, args=("doc", i), name=f"doc-{i}")
                   for i in range(self.s.doc_lane_slots)]
        threads += [threading.Thread(target=self.lane_loop, args=("media", 0), name="media-0"),
                    threading.Thread(target=self.lane_loop, args=("system", 0), name="system-0"),
                    threading.Thread(target=self.scheduler_loop, name="scheduler"),
                    threading.Thread(target=WatcherManager(self.stop).run, name="watcher")]
        for t in threads:
            t.start()
        log.info("worker started", extra={"event": "worker_start", "worker": self.id,
                                          "doc_slots": self.s.doc_lane_slots})
        self.stop.wait()
        log.info("worker stopping: finishing current jobs", extra={"event": "worker_stopping"})
        deadline = time.time() + SHUTDOWN_GRACE
        for t in threads:
            t.join(max(0.1, deadline - time.time()))
        self.release_running()
        log.info("worker stopped", extra={"event": "worker_stop"})
        os._exit(0)


if __name__ == "__main__":
    setup_logging()
    Worker().run()
