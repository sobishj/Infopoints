"""Project access filtering in retrieval and file access, plus the not-found gate."""
import os

import pytest
from sqlalchemy import text

from app import folders as svc
from app.access import allowed_project_ids, can_open_file
from app.auth.provider import hash_password
from app.db.models import File, User, UserProject
from app.rag.retrieve import retrieve
from tests.conftest import DOCS, fake_vector, make_pdf, run_jobs


@pytest.fixture
def two_projects(db, admin):
    for name, body in (("alpha", "Approve purchase orders under Procurement then Pending Orders."),
                       ("beta", "Payroll runs are approved by the HR manager every month.")):
        os.makedirs(os.path.join(DOCS, name))
        make_pdf(os.path.join(DOCS, name, f"{name}.pdf"), [body])
    a = svc.create_folder(db, admin.id, "Alpha", "/docs/alpha")
    b = svc.create_folder(db, admin.id, "Beta", "/docs/beta")
    run_jobs()
    pa = db.execute(text("SELECT id FROM projects WHERE folder_id = :f"), {"f": a.id}).scalar()
    pb = db.execute(text("SELECT id FROM projects WHERE folder_id = :f"), {"f": b.id}).scalar()
    return pa, pb


def _user(db, project_ids):
    u = User(username="ana", role="user", password_hash=hash_password("x"))
    db.add(u)
    db.flush()
    for p in project_ids:
        db.add(UserProject(user_id=u.id, project_id=p))
    db.commit()
    return u


def test_user_only_sees_assigned_projects(db, two_projects):
    pa, pb = two_projects
    u = _user(db, [pa])
    assert allowed_project_ids(db, u, None) == [pa]
    assert allowed_project_ids(db, u, [pa, pb]) == [pa]  # asking for more doesn't grant it
    assert allowed_project_ids(db, u, [pb]) == []


def test_retrieval_never_returns_other_projects(db, two_projects):
    pa, pb = two_projects
    q = "payroll runs approved by which manager"
    hits, relevant = retrieve(db, q, fake_vector(q), [pa])
    assert all(h.file_name == "alpha.pdf" for h in hits)
    hits_b, relevant_b = retrieve(db, q, fake_vector(q), [pb])
    assert hits_b and hits_b[0].file_name == "beta.pdf" and relevant_b
    assert hits_b[0].label == "Page 1" and hits_b[0].header == "[Source: beta.pdf | Page 1]"


def test_no_projects_means_no_results(db, two_projects):
    assert retrieve(db, "anything", fake_vector("anything"), []) == ([], False)


def test_unrelated_question_fails_the_relevance_gate(db, two_projects):
    pa, pb = two_projects
    q = "quantum chromodynamics lattice gauge"
    _hits, relevant = retrieve(db, q, fake_vector(q), [pa, pb])
    assert relevant is False


def test_file_access_checks_project(db, two_projects):
    pa, pb = two_projects
    u = _user(db, [pa])
    fa = db.query(File).filter_by(file_name="alpha.pdf").one()
    fb = db.query(File).filter_by(file_name="beta.pdf").one()
    assert can_open_file(db, u, fa.id)
    assert not can_open_file(db, u, fb.id)
