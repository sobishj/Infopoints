"""Authentication providers. Local accounts now; an LdapAuthProvider can implement the same interface later
(authenticate against AD, then upsert a users row with auth_source='ldap' and no password hash)."""
from typing import Protocol

import bcrypt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import User


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


class AuthProvider(Protocol):
    name: str

    def authenticate(self, db: Session, username: str, password: str) -> User | None: ...


class LocalAuthProvider:
    name = "local"

    def authenticate(self, db: Session, username: str, password: str) -> User | None:
        user = db.scalar(select(User).where(User.username == username.strip(), User.auth_source == "local"))
        if user and user.is_active and verify_password(password, user.password_hash):
            return user
        # Spend comparable time when the user doesn't exist (no username probing via timing).
        if user is None:
            verify_password(password, "$2b$12$C6UzMDM.H6dfI/f/IKcEeO5V6m2bzGfFhlNqGEnWd6NsZfLk2Ym2a")
        return None


PROVIDERS: list[AuthProvider] = [LocalAuthProvider()]


def authenticate(db: Session, username: str, password: str) -> User | None:
    for provider in PROVIDERS:
        user = provider.authenticate(db, username, password)
        if user:
            return user
    return None
