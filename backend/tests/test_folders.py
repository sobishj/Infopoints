"""Folder add / change / disable / remove."""
import os

import pytest
from sqlalchemy import text

from app import folders as svc
from app.access import visible_project_ids
from app.paths import PathNotAllowed
from tests.conftest import DOCS, make_pdf, run_jobs


def _count(db, sql, **kw):
    return db.execute(text(sql), kw).scalar()


def test_add_rejects_paths_outside_roots_and_traversal(db, admin):
    with pytest.raises(PathNotAllowed, match="outside the allowed"):
        svc.create_folder(db, admin.id, "x", "/etc")
    with pytest.raises(PathNotAllowed):
        svc.create_folder(db, admin.id, "x", "/docs/../etc")
    with pytest.raises(PathNotAllowed, match="doesn't exist"):
        svc.create_folder(db, admin.id, "x", "/docs/missing")


def test_preview_counts_supported_files(db):
    os.makedirs(os.path.join(DOCS, "p", "sub"))
    make_pdf(os.path.join(DOCS, "p", "a.pdf"), ["a"])
    make_pdf(os.path.join(DOCS, "p", "sub", "b.pdf"), ["b"])
    open(os.path.join(DOCS, "p", "notes.md"), "w").close()
    open(os.path.join(DOCS, "p", "setup.exe"), "w").close()
    p = svc.preview("/docs/p", include_subfolders=True, file_types=None)
    assert p.counts == {"md": 1, "pdf": 2} and p.total_files == 3
    assert svc.preview("/docs/p", include_subfolders=False, file_types=None).total_files == 2
    assert svc.preview("/docs/p", include_subfolders=True, file_types=["pdf"]).total_files == 2


def test_add_starts_indexing_and_writes_audit(db, admin):
    os.makedirs(os.path.join(DOCS, "a"))
    make_pdf(os.path.join(DOCS, "a", "x.pdf"), ["Hello procurement."])
    f = svc.create_folder(db, admin.id, "", "/docs/a")
    assert f.display_name == "a"
    assert _count(db, "SELECT count(*) FROM jobs WHERE type = 'scan_folder'") == 1
    run_jobs()
    assert _count(db, "SELECT count(*) FROM files WHERE status = 'indexed'") == 1
    assert _count(db, "SELECT count(*) FROM audit_log WHERE entity = 'folder' AND action = 'create'") == 1


def test_duplicate_folder_rejected(db, admin):
    os.makedirs(os.path.join(DOCS, "a"))
    svc.create_folder(db, admin.id, "A", "/docs/a")
    with pytest.raises(PathNotAllowed, match="already configured"):
        svc.create_folder(db, admin.id, "A again", "/docs/a")


def test_change_path_purges_old_index_and_indexes_new_location(db, admin):
    os.makedirs(os.path.join(DOCS, "old"))
    os.makedirs(os.path.join(DOCS, "new"))
    make_pdf(os.path.join(DOCS, "old", "old.pdf"), ["Old content."])
    make_pdf(os.path.join(DOCS, "new", "new.pdf"), ["New content."])
    f = svc.create_folder(db, admin.id, "P", "/docs/old")
    run_jobs()
    svc.update_folder(db, admin.id, f, {"host_path": "/docs/new"})
    assert _count(db, "SELECT count(*) FROM files WHERE file_name = 'old.pdf'") == 0
    run_jobs()
    assert _count(db, "SELECT count(*) FROM files WHERE file_name = 'new.pdf' AND status = 'indexed'") == 1
    assert _count(db, "SELECT count(*) FROM chunks c JOIN files f ON f.id = c.file_id WHERE f.file_name = 'old.pdf'") == 0
    assert _count(db, "SELECT count(*) FROM audit_log WHERE action = 'update'") == 1


def test_disable_hides_from_search_but_keeps_index(db, admin):
    os.makedirs(os.path.join(DOCS, "a"))
    make_pdf(os.path.join(DOCS, "a", "x.pdf"), ["Hello."])
    f = svc.create_folder(db, admin.id, "A", "/docs/a")
    run_jobs()
    assert visible_project_ids(db, admin)
    svc.update_folder(db, admin.id, f, {"enabled": False})
    assert visible_project_ids(db, admin) == []
    assert _count(db, "SELECT count(*) FROM chunks") > 0


def test_remove_deletes_index(db, admin):
    os.makedirs(os.path.join(DOCS, "a"))
    make_pdf(os.path.join(DOCS, "a", "x.pdf"), ["Hello."])
    f = svc.create_folder(db, admin.id, "A", "/docs/a")
    run_jobs()
    svc.delete_folder(db, admin.id, f)
    for table in ("folders", "projects", "files", "chunks"):
        assert _count(db, f"SELECT count(*) FROM {table}") == 0
    assert os.path.exists(os.path.join(DOCS, "a", "x.pdf"))  # files on disk are never touched
