"""Watch every enabled folder; any change schedules a (deduplicated) folder scan.

Uses watchdog's PollingObserver: Docker Desktop bind mounts and SMB shares don't deliver native
file-system events, so polling is the only reliable option there. The scan itself does the diffing.
"""
import logging
import os
import threading

from sqlalchemy import select
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers.polling import PollingObserver

from app.config import get_settings
from app.db.models import Folder
from app.db.session import session_scope
from app.filetypes import is_ignored_name
from app.jobs import enqueue_scan

log = logging.getLogger("infopoint.watcher")


class _Handler(FileSystemEventHandler):
    def __init__(self, folder_id: int):
        self.folder_id = folder_id

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.event_type in ("opened", "closed", "closed_no_write"):
            return
        if not event.is_directory and is_ignored_name(os.path.basename(str(event.src_path))):
            return
        try:
            with session_scope() as db:
                enqueue_scan(db, self.folder_id, delay_seconds=2)
        except Exception:
            log.exception("could not queue scan", extra={"folder_id": self.folder_id})


class WatcherManager:
    def __init__(self, stop: threading.Event):
        self.stop = stop
        self.observers: dict[int, tuple[tuple, PollingObserver]] = {}

    def sync(self) -> None:
        with session_scope() as db:
            rows = db.execute(select(Folder.id, Folder.container_path, Folder.include_subfolders,
                                     Folder.updated_at).where(Folder.enabled)).all()
        wanted = {r.id: (r.container_path, r.include_subfolders, r.updated_at) for r in rows}
        for fid in list(self.observers):
            if fid not in wanted or self.observers[fid][0] != wanted[fid]:
                self._stop_one(fid)
        for fid, sig in wanted.items():
            if fid in self.observers or not os.path.isdir(sig[0]):
                continue
            obs = PollingObserver(timeout=get_settings().folder_poll_seconds)
            obs.schedule(_Handler(fid), sig[0], recursive=sig[1])
            obs.daemon = True
            try:
                obs.start()
            except Exception:
                log.exception("watch failed", extra={"folder_id": fid, "path": sig[0]})
                continue
            self.observers[fid] = (sig, obs)
            log.info("watching folder", extra={"event": "watch_start", "folder_id": fid, "path": sig[0]})

    def _stop_one(self, fid: int) -> None:
        _, obs = self.observers.pop(fid)
        obs.stop()
        log.info("stopped watching folder", extra={"event": "watch_stop", "folder_id": fid})

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                self.sync()
            except Exception:
                log.exception("watcher sync failed")
            self.stop.wait(3)
        for fid in list(self.observers):
            self._stop_one(fid)
