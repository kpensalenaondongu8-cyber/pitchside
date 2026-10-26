from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import MessageDeletion
from app.ratelimit import RateLimiter

GROUP = "group"
PRIVATE = "private"

# 20 delete actions per minute for each user.
delete_limiter = RateLimiter(max_events=20, per_seconds=60)

SEEN_DETAIL = "Someone has already seen this message, so it can only be deleted for you."


class DeleteInput(BaseModel):
    scope: Literal["everyone", "self"]


def load_deletions(db: Session, chat_type: str, message_ids: list, viewer_id: int):
    """Return (deleted for everyone: {message id: who deleted it}, hidden for this viewer: set of ids)."""
    if not message_ids:
        return {}, set()

    rows = (
        db.query(MessageDeletion)
        .filter(
            MessageDeletion.chat_type == chat_type,
            MessageDeletion.message_id.in_(message_ids),
            or_(
                MessageDeletion.scope == "everyone",
                and_(MessageDeletion.scope == "self", MessageDeletion.user_id == viewer_id),
            ),
        )
        .all()
    )

    everyone = {row.message_id: row.user_id for row in rows if row.scope == "everyone"}
    hidden = {row.message_id for row in rows if row.scope == "self"}

    return everyone, hidden


def hidden_ids(chat_type: str, viewer_id: int):
    """A query for the ids of messages this user deleted just for themselves."""
    return select(MessageDeletion.message_id).where(
        MessageDeletion.chat_type == chat_type,
        MessageDeletion.scope == "self",
        MessageDeletion.user_id == viewer_id,
    )


def deleted_for_everyone_ids(chat_type: str):
    """A query for the ids of messages deleted for everyone."""
    return select(MessageDeletion.message_id).where(
        MessageDeletion.chat_type == chat_type,
        MessageDeletion.scope == "everyone",
    )


def record_deletion(db: Session, chat_type: str, message_id: int, user_id: int, scope: str) -> None:
    exists = (
        db.query(MessageDeletion.id)
        .filter(
            MessageDeletion.chat_type == chat_type,
            MessageDeletion.message_id == message_id,
            MessageDeletion.user_id == user_id,
            MessageDeletion.scope == scope,
        )
        .first()
    )

    if exists:
        return

    db.add(MessageDeletion(
        chat_type=chat_type,
        message_id=message_id,
        user_id=user_id,
        scope=scope,
        created_at=datetime.now(timezone.utc),
    ))

    try:
        db.commit()
    except IntegrityError:
        db.rollback()