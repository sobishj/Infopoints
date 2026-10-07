"""File change / delete handling and the stability gate."""
import os
import time

import pytest
from sqlalchemy import text

from app import folders as svc
from tests.conftest import DOCS, make_pdf, run_jobs
from worker.jobs import Requeue
from worker.jobs.index_file import stability_gate


def _chunks(db, file_name):
    return db.execute(text("SELECT c.id, c.page, c.text FROM chunks c JOIN files f ON f.id = c.file_id "
                           "WHERE f.file_name = :n ORDER BY c.id"), {"n": file_name}).all()


def _status(db, file_name):
    return db.execute(text("SELECT status FROM files WHERE file_name = :n"), {"n": file_name}).scalar()


@pytest.fixture
def folder(db, admin):
    os.makedirs(os.path.join(DOCS, "proc"), exist_ok=True)
    return svc.create_folder(db, admin.id, "Procurement", "/docs/proc")


def test_new_pdf_is_indexed_with_pages(db, folder):
    make_pdf(os.path.join(DOCS, "proc", "guide.pdf"), ["Welcome page.", "Approve orders in Procurement."])
    run_jobs()
    assert _status(db, "guide.pdf") == "indexed"
    pages = sorted({r.page for r in _chunks(db, "guide.pdf")})
    assert pages == [1, 2]


def test_changed_file_replaces_all_old_chunks(db, folder):
    path = os.path.join(DOCS, "proc", "guide.pdf")
    make_pdf(path, ["Old approval process uses form A."])
    run_jobs()
    old_ids = {r.id for r in _chunks(db, "guide.pdf")}
    time.sleep(0.05)
    make_pdf(path, ["New approval process uses the Pending Orders screen.", "Second page."])
    os.utime(path, (time.time() + 5, time.time() + 5))
    svc_jobs_scan(db, folder.id)
    run_jobs()
    rows = _chunks(db, "guide.pdf")
    assert old_ids.isdisjoint({r.id for r in rows})
    assert all("form A" not in r.text for r in rows)
    assert any("Pending Orders" in r.text for r in rows)


def test_touch_without_content_change_keeps_chunks(db, folder):
    path = os.path.join(DOCS, "proc", "guide.pdf")
    make_pdf(path, ["Stable content."])
    run_jobs()
    before = [r.id for r in _chunks(db, "guide.pdf")]
    os.utime(path, (time.time() + 10, time.time() + 10))
    svc_jobs_scan(db, folder.id)
    run_jobs()
    assert [r.id for r in _chunks(db, "guide.pdf")] == before
    assert _status(db, "guide.pdf") == "indexed"


def test_deleted_file_removes_chunks(db, folder):
    path = os.path.join(DOCS, "proc", "guide.pdf")
    make_pdf(path, ["Soon to be deleted."])
    run_jobs()
    assert _chunks(db, "guide.pdf")
    os.remove(path)
    svc_jobs_scan(db, folder.id)
    run_jobs()
    assert _chunks(db, "guide.pdf") == []
    assert _status(db, "guide.pdf") == "deleted"


def test_corrupt_file_fails_without_blocking_others(db, folder):
    with open(os.path.join(DOCS, "proc", "broken.pdf"), "wb") as f:
        f.write(b"%PDF-1.4 this is not really a pdf")
    make_pdf(os.path.join(DOCS, "proc", "ok.pdf"), ["Fine."])
    run_jobs()
    assert _status(db, "broken.pdf") == "failed"
    assert _status(db, "ok.pdf") == "indexed"


def test_office_lock_and_temp_files_are_ignored(db, folder):
    for name in ("~$guide.docx", ".~lock.guide.docx#", "download.crdownload"):
        open(os.path.join(DOCS, "proc", name), "wb").close()
    run_jobs()
    assert db.execute(text("SELECT count(*) FROM files")).scalar() == 0


def test_stability_gate_waits_until_size_is_unchanged():
    now = 1000.0
    with pytest.raises(Requeue) as r:  # first sighting: remember size, check again later
        stability_gate({"file_id": 1}, size=10, mtime=1.0, now=now, stability_seconds=10)
    seen = r.value.payload["seen"]
    with pytest.raises(Requeue):  # still growing
        stability_gate(r.value.payload, size=20, mtime=2.0, now=now + 10, stability_seconds=10)
    with pytest.raises(Requeue):  # unchanged, but not for long enough
        stability_gate({"file_id": 1, "seen": seen}, size=10, mtime=1.0, now=now + 4, stability_seconds=10)
    stability_gate({"file_id": 1, "seen": seen}, size=10, mtime=1.0, now=now + 10, stability_seconds=10)  # ready


def svc_jobs_scan(db, folder_id):
    from app.jobs import enqueue_scan
    enqueue_scan(db, folder_id)
    db.commit()
