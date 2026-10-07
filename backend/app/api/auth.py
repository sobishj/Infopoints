from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.deps import _user_from_cookie, auto_login_user, create_session, end_session
from app.auth.provider import authenticate
from app.db.models import User
from app.db.session import get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


def user_dict(u: User) -> dict:
    return {"id": u.id, "username": u.username, "display_name": u.display_name or u.username, "role": u.role}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    user = authenticate(db, body.username, body.password)
    if user is None:
        raise HTTPException(401, "Wrong username or password.")
    create_session(db, response, user, request)
    return user_dict(user)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    end_session(db, request, response)
    return {"ok": True}


@router.get("/me")
def me(request: Request, response: Response, db: Session = Depends(get_db)):
    user = _user_from_cookie(db, request)
    if user is None:
        user = auto_login_user(db)  # desktop single-user mode
        if user is None:
            raise HTTPException(401, "Not signed in.")
        create_session(db, response, user, request)
    return user_dict(user)
