from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Skit, SkitReport, User
from app.ratelimit import RateLimiter
from app.routers.auth import get_current_user, get_db
from app.routers.highlights import is_admin, youtube_video_id
from app.timeutils import to_utc_iso

router = APIRouter(prefix="/skits", tags=["Skits"])

MAX_PENDING_PER_USER = 3
MAX_SUBMISSIONS_PER_DAY = 5
REPORTS_TO_HIDE = 3
FEED_LIMIT = 40

submit_limiter = RateLimiter(max_events=5, per_seconds=60)
report_limiter = RateLimiter(max_events=10, per_seconds=60)
admin_limiter = RateLimiter(max_events=30, per_seconds=60)

LIVE_STATUSES = ("pending", "approved", "reported")


class SkitInput(BaseModel):
    url: str = Field(min_length=5, max_length=300)
    title: str = Field(min_length=3, max_length=100)
    confirm: bool = False


def require_admin(user: User) -> None:
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="Only admins can do that.")

    if not admin_limiter.allow(user.id):
        raise HTTPException(status_code=429, detail="Too many actions. Please wait a minute.")


def get_skit(db: Session, skit_id: int) -> Skit:
    skit = db.query(Skit).filter(Skit.id == skit_id).first()

    if skit is None:
        raise HTTPException(status_code=404, detail="Skit not found.")

    return skit


def report_counts(db: Session, ids: list) -> dict:
    if not ids:
        return {}

    rows = (
        db.query(SkitReport.skit_id, func.count(SkitReport.id))
        .filter(SkitReport.skit_id.in_(ids))
        .group_by(SkitReport.skit_id)
        .all()
    )

    return {skit_id: count for skit_id, count in rows}


# Fixed words (feed, mine, admin, submit) are separate paths from "/{skit_id}/...", so order is safe.
@router.get("/feed")
def feed(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    admin = is_admin(current_user)

    rows = (
        db.query(Skit, User.username)
        .join(User, Skit.user_id == User.id)
        .filter(Skit.status == "approved")
        .order_by(Skit.id.desc())
        .limit(FEED_LIMIT)
        .all()
    )

    ids = [skit.id for skit, _ in rows]
    reported_by_me = set()

    if ids:
        reported_by_me = {
            row[0]
            for row in db.query(SkitReport.skit_id)
            .filter(SkitReport.skit_id.in_(ids), SkitReport.user_id == current_user.id)
            .all()
        }

    counts = report_counts(db, ids) if admin else {}
    items = []

    for skit, username in rows:
        item = {
            "id": skit.id,
            "video_id": skit.video_id,
            "title": skit.title,
            "username": username,
            "created_at": to_utc_iso(skit.created_at),
            "mine": skit.user_id == current_user.id,
            "reported_by_me": skit.id in reported_by_me,
        }

        if admin:
            item["reports"] = counts.get(skit.id, 0)

        items.append(item)

    return {"skits": items, "can_manage": admin}


@router.get("/mine")
def my_submissions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = (
        db.query(Skit)
        .filter(Skit.user_id == current_user.id, Skit.status.in_(("pending", "rejected")))
        .order_by(Skit.id.desc())
        .limit(20)
        .all()
    )

    return {
        "skits": [
            {"id": s.id, "video_id": s.video_id, "title": s.title, "status": s.status}
            for s in rows
        ]
    }


@router.get("/admin")
def review_queue(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not is_admin(current_user):
        raise HTTPException(status_code=403, detail="Only admins can do that.")

    rows = (
        db.query(Skit, User.username)
        .join(User, Skit.user_id == User.id)
        .filter(Skit.status.in_(("pending", "reported")))
        .order_by(Skit.id.asc())
        .limit(100)
        .all()
    )

    counts = report_counts(db, [skit.id for skit, _ in rows])

    return {
        "queue": [
            {
                "id": skit.id,
                "video_id": skit.video_id,
                "title": skit.title,
                "username": username,
                "status": skit.status,
                "reports": counts.get(skit.id, 0),
            }
            for skit, username in rows
        ]
    }


@router.post("/submit", status_code=201)
def submit_skit(
    payload: SkitInput,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not payload.confirm:
        raise HTTPException(
            status_code=422,
            detail="Please tick the box to confirm you made this skit or have permission to share it.",
        )

    if not submit_limiter.allow(current_user.id):
        raise HTTPException(status_code=429, detail="Too many attempts. Please wait a minute.")

    video_id = youtube_video_id(payload.url)

    if video_id is None:
        raise HTTPException(status_code=422, detail="That does not look like a YouTube link.")

    title = payload.title.strip()

    if len(title) < 3:
        raise HTTPException(status_code=422, detail="Please give your skit a title (at least 3 characters).")

    pending = (
        db.query(Skit)
        .filter(Skit.user_id == current_user.id, Skit.status == "pending")
        .count()
    )

    if pending >= MAX_PENDING_PER_USER:
        raise HTTPException(
            status_code=400,
            detail=f"You already have {MAX_PENDING_PER_USER} skits waiting for approval. Please wait for a review.",
        )

    since = datetime.now(timezone.utc) - timedelta(hours=24)
    today = (
        db.query(Skit)
        .filter(Skit.user_id == current_user.id, Skit.created_at >= since)
        .count()
    )

    if today >= MAX_SUBMISSIONS_PER_DAY:
        raise HTTPException(status_code=429, detail="Daily limit reached. Please try again tomorrow.")

    duplicate = (
        db.query(Skit.id)
        .filter(Skit.video_id == video_id, Skit.status.in_(LIVE_STATUSES))
        .first()
    )

    if duplicate:
        raise HTTPException(status_code=409, detail="That video has already been submitted.")

    skit = Skit(
        source="youtube",
        video_id=video_id,
        title=title,
        user_id=current_user.id,
        status="pending",
        created_at=datetime.now(timezone.utc),
    )
    db.add(skit)
    db.commit()
    db.refresh(skit)

    return {"skit": {"id": skit.id, "status": skit.status}}


@router.post("/{skit_id}/approve")
def approve_skit(
    skit_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin(current_user)
    skit = get_skit(db, skit_id)

    if skit.status not in ("pending", "reported"):
        raise HTTPException(status_code=400, detail="This skit is not waiting for review.")

    skit.status = "approved"
    skit.reviewed_by = current_user.id
    skit.reviewed_at = datetime.now(timezone.utc)
    db.query(SkitReport).filter(SkitReport.skit_id == skit.id).delete()
    db.commit()

    return {"ok": True}


@router.post("/{skit_id}/reject")
def reject_skit(
    skit_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin(current_user)
    skit = get_skit(db, skit_id)

    if skit.status != "pending":
        raise HTTPException(status_code=400, detail="Only waiting skits can be rejected.")

    skit.status = "rejected"
    skit.reviewed_by = current_user.id
    skit.reviewed_at = datetime.now(timezone.utc)
    db.commit()

    return {"ok": True}


@router.post("/{skit_id}/remove")
def remove_skit(
    skit_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin(current_user)
    skit = get_skit(db, skit_id)

    if skit.status not in ("approved", "reported"):
        raise HTTPException(status_code=400, detail="Only published skits can be removed.")

    skit.status = "removed"
    skit.reviewed_by = current_user.id
    skit.reviewed_at = datetime.now(timezone.utc)
    db.commit()

    return {"ok": True}


@router.post("/{skit_id}/report")
def report_skit(
    skit_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not report_limiter.allow(current_user.id):
        raise HTTPException(status_code=429, detail="Too many reports. Please wait a minute.")

    skit = db.query(Skit).filter(Skit.id == skit_id).first()

    if skit is None or skit.status != "approved":
        raise HTTPException(status_code=404, detail="Skit not found.")

    if skit.user_id == current_user.id:
        raise HTTPException(status_code=400, detail="You cannot report your own skit.")

    already = (
        db.query(SkitReport.id)
        .filter(SkitReport.skit_id == skit_id, SkitReport.user_id == current_user.id)
        .first()
    )

    if already:
        return {"ok": True, "hidden": False}

    db.add(SkitReport(
        skit_id=skit_id,
        user_id=current_user.id,
        created_at=datetime.now(timezone.utc),
    ))

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return {"ok": True, "hidden": False}

    count = (
        db.query(func.count(SkitReport.id))
        .filter(SkitReport.skit_id == skit_id)
        .scalar()
    )

    hidden = count >= REPORTS_TO_HIDE

    if hidden:
        skit.status = "reported"
        db.commit()

    return {"ok": True, "hidden": hidden}