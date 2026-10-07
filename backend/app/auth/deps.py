"""Server-side sessions (HTTP-only cookie), current-user dependencies and CSRF check."""
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import User, UserSession
from app.db.session import get_db

COOKIE = "infopoint_session"
SESSION_DAYS = 14
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def create_session(db: Session, response: Response, user: User, request: Request) -> None:
    sid = secrets.token_urlsafe(32)
    db.add(UserSession(id=sid, user_id=user.id, expires_at=datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS),
                       ip=request.client.host if request.client else None,
                       user_agent=(request.headers.get("user-agent") or "")[:300]))
    db.execute(update(User).where(User.id == user.id).values(last_login_at=datetime.now(timezone.utc)))
    db.commit()
    response.set_cookie(COOKIE, sid, httponly=True, samesite="strict", max_age=SESSION_DAYS * 86400, path="/")


def end_session(db: Session, request: Request, response: Response) -> None:
    sid = request.cookies.get(COOKIE)
    if sid:
        db.execute(delete(UserSession).where(UserSession.id == sid))
        db.commit()
    response.delete_cookie(COOKIE, path="/")


def _user_from_cookie(db: Session, request: Request) -> User | None:
    sid = request.cookies.get(COOKIE)
    if not sid:
        return None
    row = db.execute(select(User).join(UserSession, UserSession.user_id == User.id)
                     .where(UserSession.id == sid, UserSession.expires_at > datetime.now(timezone.utc),
                            User.is_active)).scalar_one_or_none()
    return row


def auto_login_user(db: Session) -> User | None:
    if not get_settings().auto_login:
        return None
    return db.scalar(select(User).where(User.username == get_settings().initial_admin_user, User.is_active))


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    if request.method not in SAFE_METHODS and request.headers.get("x-requested-with") != "infopoint":
        raise HTTPException(403, "Missing CSRF header.")
    user = _user_from_cookie(db, request)
    if user is None:
        raise HTTPException(401, "Not signed in.")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Administrator access required.")
    return user
