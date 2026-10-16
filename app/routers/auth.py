from typing import Generator

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import AUTH_SECRET_KEY
from app.database import SessionLocal
from app.models import User
from app.security import (
    COOKIE_MAX_AGE,
    COOKIE_NAME,
    cookie_secure,
    create_auth_token,
    hash_password,
    read_auth_token,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])
templates = Jinja2Templates(directory="app/templates")


class AuthInput(BaseModel):
    username: str = Field(
        min_length=3,
        max_length=30,
        pattern=r"^[A-Za-z0-9_]+$",
    )
    password: str = Field(min_length=8, max_length=128)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def user_response(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
    }


def set_auth_cookie(response: Response, user: User) -> None:
    token = create_auth_token(user.id, user.auth_version)

    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
        path="/",
    )


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    token = request.cookies.get(COOKIE_NAME)

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )

    token_data = read_auth_token(token)

    if token_data is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication token.",
        )

    user = db.query(User).filter(User.id == token_data["uid"]).first()

    if user is None or user.auth_version != token_data["v"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session is no longer valid.",
        )

    return user


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return templates.TemplateResponse(
        request,
        "register.html",
        {},
    )


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(
        request,
        "login.html",
        {},
    )


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(
    payload: AuthInput,
    response: Response,
    db: Session = Depends(get_db),
):
    username = payload.username.strip()

    existing_user = (
        db.query(User)
        .filter(func.lower(User.username) == username.lower())
        .first()
    )

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username is already taken.",
        )

    user = User(
        username=username,
        password_hash=hash_password(payload.password),
        auth_version=0,
    )

    db.add(user)

    try:
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username is already taken.",
        )

    set_auth_cookie(response, user)

    return {"user": user_response(user)}


@router.post("/login")
def login(
    payload: AuthInput,
    response: Response,
    db: Session = Depends(get_db),
):
    username = payload.username.strip()

    user = (
        db.query(User)
        .filter(func.lower(User.username) == username.lower())
        .first()
    )

    if user is None or not verify_password(
        payload.password,
        user.password_hash,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )

    set_auth_cookie(response, user)

    return {"user": user_response(user)}


@router.get("/me")
def get_me(user: User = Depends(get_current_user)):
    return {"user": user_response(user)}


@router.post("/logout")
def logout(
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
):
    token = request.cookies.get(COOKIE_NAME)
    token_data = read_auth_token(token) if token else None

    if token_data is not None:
        user = db.query(User).filter(User.id == token_data["uid"]).first()

        if user is not None and user.auth_version == token_data["v"]:
            user.auth_version += 1
            db.commit()

    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
    )

    return {"message": "Logged out successfully."}