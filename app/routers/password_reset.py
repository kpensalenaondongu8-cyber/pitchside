import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import AUTH_SECRET_KEY
from app.models import User
from app.ratelimit import RateLimiter
from app.routers.auth import get_current_user, get_db
from app.routers.highlights import is_admin
from app.security import hash_password

log = logging.getLogger("uvicorn.error")

router = APIRouter(tags=["Password reset"])
templates = Jinja2Templates(directory="app/templates")

RESET_MAX_AGE = 60 * 60  # a reset link works for one hour

# 10 reset attempts per 15 minutes for each connection.
reset_limiter = RateLimiter(max_events=10, per_seconds=900)
# 10 links per minute for each admin.
link_limiter = RateLimiter(max_events=10, per_seconds=60)

BAD_LINK = "This link is invalid or has expired. Please ask an admin for a new one."


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(AUTH_SECRET_KEY, salt="pitchside-password-reset-v1")


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


class LinkRequest(BaseModel):
    username: str = Field(min_length=3, max_length=30, pattern=r"^[A-Za-z0-9_]+$")


class ResetInput(BaseModel):
    token: str = Field(min_length=10, max_length=500)
    password: str = Field(min_length=8, max_length=128)


@router.get("/auth/reset", response_class=HTMLResponse)
def reset_page(request: Request):
    return templates.TemplateResponse(request, "reset.html", {})


@router.post("/auth/reset")
def apply_reset(
    payload: ResetInput,
    request: Request,
    db: Session = Depends(get_db),
):
    if not reset_limiter.allow(client_ip(request)):
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Please wait a few minutes and try again.",
        )

    try:
        data = _serializer().loads(payload.token, max_age=RESET_MAX_AGE)
    except (BadSignature, SignatureExpired):
        raise HTTPException(status_code=400, detail=BAD_LINK)

    if not isinstance(data, dict) or not isinstance(data.get("uid"), int) or not isinstance(data.get("v"), int):
        raise HTTPException(status_code=400, detail=BAD_LINK)

    user = db.get(User, data["uid"])

    # If the account's session version changed (a reset already happened, or the user
    # logged out), the link is no longer valid.
    if user is None or user.auth_version != data["v"]:
        raise HTTPException(status_code=400, detail=BAD_LINK)

    user.password_hash = hash_password(payload.password)
    user.auth_version += 1  # logs the account out everywhere and kills every reset link for it
    db.commit()

    log.warning("password reset completed for user id %s", user.id)

    return {"ok": True}


@router.get("/admin/reset", response_class=HTMLResponse)
def admin_reset_page(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    if not is_admin(current_user):
        raise HTTPException(status_code=404, detail="Not found.")

    return templates.TemplateResponse(
        request,
        "admin_reset.html",
        {"page": "admin", "subtitle": "Admin: help someone get back into their account."},
    )


@router.post("/admin/reset-link")
def create_reset_link(
    payload: LinkRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not is_admin(current_user):
        raise HTTPException(status_code=403, detail="Only admins can do that.")

    if not link_limiter.allow(current_user.id):
        raise HTTPException(status_code=429, detail="Too many links. Please wait a minute.")

    username = payload.username.strip()

    user = (
        db.query(User)
        .filter(func.lower(User.username) == username.lower())
        .first()
    )

    if user is None:
        raise HTTPException(status_code=404, detail="No account with that username.")

    token = _serializer().dumps({"uid": user.id, "v": user.auth_version})
    base = str(request.base_url).rstrip("/")

    # Log who made a link and for whom (never the link itself).
    log.warning("reset link created by %s for user id %s", current_user.username, user.id)

    return {
        "username": user.username,
        "link": f"{base}/auth/reset?token={token}",
        "expires_in_minutes": RESET_MAX_AGE // 60,
    }