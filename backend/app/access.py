"""Project access: the single place that decides which projects a user may search or open."""
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models import User


def visible_project_ids(db: Session, user: User) -> list[int]:
    """Projects of enabled folders that the user may see (admins: all)."""
    if user.role == "admin":
        sql = "SELECT p.id FROM projects p JOIN folders f ON f.id = p.folder_id WHERE f.enabled ORDER BY p.id"
        return list(db.execute(text(sql)).scalars())
    sql = """SELECT p.id FROM projects p JOIN folders f ON f.id = p.folder_id
             JOIN user_projects up ON up.project_id = p.id
             WHERE f.enabled AND up.user_id = :u ORDER BY p.id"""
    return list(db.execute(text(sql), {"u": user.id}).scalars())


def allowed_project_ids(db: Session, user: User, requested: list[int] | None) -> list[int]:
    visible = visible_project_ids(db, user)
    if not requested:
        return visible
    wanted = set(requested)
    return [p for p in visible if p in wanted]


def can_open_file(db: Session, user: User, file_id: int) -> bool:
    project = db.execute(text("SELECT project_id FROM files WHERE id = :id"), {"id": file_id}).scalar()
    return project is not None and project in set(visible_project_ids(db, user))
