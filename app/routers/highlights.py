import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.competitions import slugify
from app.config import ADMIN_USERNAMES
from app.models import HighlightVideo, Match, User
from app.ratelimit import RateLimiter
from app.routers.auth import get_current_user, get_db
from app.timeutils import to_utc_iso

router = APIRouter(prefix="/highlights", tags=["Highlights"])

MAX_PER_MATCH = 10
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")

# 10 changes per minute for each admin.
admin_limiter = RateLimiter(max_events=10, per_seconds=60)


class HighlightInput(BaseModel):
    url: str = Field(min_length=5, max_length=300)
    title: str = Field(default="", max_length=150)


def is_admin(user: User) -> bool:
    return user.username.lower() in ADMIN_USERNAMES


def require_admin(user: User) -> None:
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="Only admins can change highlights.")

    if not admin_limiter.allow(user.id):
        raise HTTPException(status_code=429, detail="Too many changes. Please wait a minute.")


def youtube_video_id(url: str):
    """Return the 11-character video id from a YouTube link, or None if it is not one."""
    text = url.strip()

    if "://" not in text:
        text = "https://" + text

    try:
        parsed = urlparse(text)
    except ValueError:
        return None

    if parsed.scheme not in ("http", "https"):
        return None

    host = (parsed.hostname or "").lower()
    candidate = None

    if host == "youtu.be":
        candidate = parsed.path.lstrip("/").split("/")[0]
    elif host in ("youtube.com", "www.youtube.com", "m.youtube.com"):
        parts = [part for part in parsed.path.split("/") if part]

        if parsed.path == "/watch":
            candidate = (parse_qs(parsed.query).get("v") or [""])[0]
        elif len(parts) >= 2 and parts[0] in ("embed", "shorts", "live", "v"):
            candidate = parts[1]

    if candidate and VIDEO_ID.match(candidate):
        return candidate

    return None


def to_dict(item: HighlightVideo) -> dict:
    return {"id": item.id, "video_id": item.video_id, "title": item.title}


# The fixed word "feed" must stay ABOVE "/{match_id}", otherwise it is read as a match id.
@router.get("/feed")
def highlights_feed(
    limit: int = 40,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    limit = max(1, min(limit, 100))

    rows = (
        db.query(HighlightVideo, Match)
        .outerjoin(Match, Match.api_id == HighlightVideo.match_id)
        .order_by(HighlightVideo.id.desc())
        .limit(limit)
        .all()
    )

    items = []

    for highlight, match in rows:
        items.append({
            "id": highlight.id,
            "video_id": highlight.video_id,
            "title": highlight.title,
            "added_at": to_utc_iso(highlight.created_at),
            "match_id": highlight.match_id,
            "competition": match.competition if match else "",
            "competition_slug": slugify(match.competition) if match and match.competition else "",
            "home_team": match.home_team if match else "",
            "away_team": match.away_team if match else "",
            "home_score": match.home_score if match else None,
            "away_score": match.away_score if match else None,
        })

    return {"highlights": items}


@router.get("/{match_id}")
def list_highlights(
    match_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = (
        db.query(HighlightVideo)
        .filter(HighlightVideo.match_id == match_id)
        .order_by(HighlightVideo.id.desc())
        .limit(MAX_PER_MATCH)
        .all()
    )

    return {
        "highlights": [to_dict(row) for row in rows],
        "can_manage": is_admin(current_user),
    }


@router.post("/{match_id}", status_code=201)
def add_highlight(
    match_id: int,
    payload: HighlightInput,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin(current_user)

    video_id = youtube_video_id(payload.url)

    if video_id is None:
        raise HTTPException(status_code=422, detail="That does not look like a YouTube link.")

    if db.query(Match.id).filter(Match.api_id == match_id).first() is None:
        raise HTTPException(status_code=404, detail="Match not found.")

    count = db.query(HighlightVideo).filter(HighlightVideo.match_id == match_id).count()

    if count >= MAX_PER_MATCH:
        raise HTTPException(status_code=400, detail="This match already has the maximum number of videos.")

    exists = (
        db.query(HighlightVideo.id)
        .filter(HighlightVideo.match_id == match_id, HighlightVideo.video_id == video_id)
        .first()
    )

    if exists:
        raise HTTPException(status_code=409, detail="That video is already on this match.")

    item = HighlightVideo(
        match_id=match_id,
        video_id=video_id,
        title=payload.title.strip() or "Highlights",
        added_by=current_user.id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(item)

    try:
        db.commit()
        db.refresh(item)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="That video is already on this match.")

    return {"highlight": to_dict(item)}


@router.delete("/{match_id}/{highlight_id}")
def delete_highlight(
    match_id: int,
    highlight_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin(current_user)

    item = (
        db.query(HighlightVideo)
        .filter(HighlightVideo.id == highlight_id, HighlightVideo.match_id == match_id)
        .first()
    )

    if item is None:
        raise HTTPException(status_code=404, detail="Highlight not found.")

    db.delete(item)
    db.commit()

    return {"ok": True}