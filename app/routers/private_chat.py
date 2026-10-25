from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import PrivateConversation, PrivateMessage, PrivateRead, User
from app.ratelimit import RateLimiter
from app.routers.auth import get_current_user, get_db
from app.timeutils import to_utc_iso


router = APIRouter(
    prefix="/private",
    tags=["Private Messaging"],
)

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

PREVIEW_LENGTH = 80

# 5 private messages per 10 seconds for each user.
private_message_limiter = RateLimiter(max_events=5, per_seconds=10)
# 20 new conversations per hour for each user.
new_conversation_limiter = RateLimiter(max_events=20, per_seconds=3600)
# 60 "mark as read" calls per minute for each user.
read_limiter = RateLimiter(max_events=60, per_seconds=60)


class PrivateMessageInput(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def make_preview(text: str) -> str:
    flat = " ".join(text.split())
    if len(flat) > PREVIEW_LENGTH:
        return flat[:PREVIEW_LENGTH].rstrip() + "…"
    return flat


@router.get("/messages", response_class=HTMLResponse)
def private_messages_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="private_chat.html",
        context={},
    )


def get_conversation_for_user(
    conversation_id: int,
    user_id: int,
    db: Session,
):
    """Return the conversation only if this user is one of its two participants."""
    conversation = (
        db.query(PrivateConversation)
        .filter(
            PrivateConversation.id == conversation_id,
            or_(
                PrivateConversation.user_one_id == user_id,
                PrivateConversation.user_two_id == user_id,
            ),
        )
        .first()
    )

    if conversation is None:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found.",
        )

    return conversation


def find_conversation(db: Session, user_one_id: int, user_two_id: int):
    return (
        db.query(PrivateConversation)
        .filter(
            PrivateConversation.user_one_id == user_one_id,
            PrivateConversation.user_two_id == user_two_id,
        )
        .first()
    )


# The fixed word "unread" is a separate path, so it never clashes with the routes below.
@router.get("/unread")
def total_unread(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    total = (
        db.query(func.count(PrivateMessage.id))
        .join(
            PrivateConversation,
            PrivateConversation.id == PrivateMessage.conversation_id,
        )
        .outerjoin(
            PrivateRead,
            and_(
                PrivateRead.conversation_id == PrivateMessage.conversation_id,
                PrivateRead.user_id == current_user.id,
            ),
        )
        .filter(
            or_(
                PrivateConversation.user_one_id == current_user.id,
                PrivateConversation.user_two_id == current_user.id,
            ),
            PrivateMessage.sender_id != current_user.id,
            PrivateMessage.id > func.coalesce(PrivateRead.last_read_id, 0),
        )
        .scalar()
    )

    return {"unread": int(total or 0)}


@router.post("/conversations", status_code=201)
def create_conversation(
    other_user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if other_user_id == current_user.id:
        raise HTTPException(
            status_code=400,
            detail="You cannot start a conversation with yourself.",
        )

    other_user = db.query(User).filter(User.id == other_user_id).first()

    if other_user is None:
        raise HTTPException(
            status_code=404,
            detail="User not found.",
        )

    user_one_id = min(current_user.id, other_user_id)
    user_two_id = max(current_user.id, other_user_id)

    conversation = find_conversation(db, user_one_id, user_two_id)

    if conversation is None:
        if not new_conversation_limiter.allow(current_user.id):
            raise HTTPException(
                status_code=429,
                detail="You are starting too many conversations. Please try again later.",
            )

        conversation = PrivateConversation(
            user_one_id=user_one_id,
            user_two_id=user_two_id,
            created_at=datetime.now(timezone.utc),
        )

        db.add(conversation)

        try:
            db.commit()
            db.refresh(conversation)
        except IntegrityError:
            # Another request created it at the same moment: use that one.
            db.rollback()
            conversation = find_conversation(db, user_one_id, user_two_id)

            if conversation is None:
                raise HTTPException(
                    status_code=500,
                    detail="Could not create the conversation.",
                )

    return {
        "id": conversation.id,
        "other_user": {
            "id": other_user.id,
            "username": other_user.username,
        },
        "created_at": to_utc_iso(conversation.created_at),
    }


@router.get("/conversations")
def list_conversations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conversations = (
        db.query(PrivateConversation)
        .filter(
            or_(
                PrivateConversation.user_one_id == current_user.id,
                PrivateConversation.user_two_id == current_user.id,
            )
        )
        .all()
    )

    if not conversations:
        return {"conversations": []}

    conversation_ids = [c.id for c in conversations]

    def other_id(conversation):
        if conversation.user_one_id == current_user.id:
            return conversation.user_two_id
        return conversation.user_one_id

    # One query for all the other users.
    users_by_id = {
        user.id: user
        for user in db.query(User).filter(User.id.in_({other_id(c) for c in conversations})).all()
    }

    # One query for the newest message in each conversation.
    newest_ids = [
        row[1]
        for row in db.query(PrivateMessage.conversation_id, func.max(PrivateMessage.id))
        .filter(PrivateMessage.conversation_id.in_(conversation_ids))
        .group_by(PrivateMessage.conversation_id)
        .all()
    ]

    last_by_conversation = {}

    if newest_ids:
        for message in db.query(PrivateMessage).filter(PrivateMessage.id.in_(newest_ids)).all():
            last_by_conversation[message.conversation_id] = message

    # One query for the unread count in each conversation.
    unread_rows = (
        db.query(PrivateMessage.conversation_id, func.count(PrivateMessage.id))
        .outerjoin(
            PrivateRead,
            and_(
                PrivateRead.conversation_id == PrivateMessage.conversation_id,
                PrivateRead.user_id == current_user.id,
            ),
        )
        .filter(
            PrivateMessage.conversation_id.in_(conversation_ids),
            PrivateMessage.sender_id != current_user.id,
            PrivateMessage.id > func.coalesce(PrivateRead.last_read_id, 0),
        )
        .group_by(PrivateMessage.conversation_id)
        .all()
    )
    unread_by_conversation = {row[0]: int(row[1]) for row in unread_rows}

    entries = []

    for conversation in conversations:
        other_user = users_by_id.get(other_id(conversation))

        if other_user is None:
            continue

        last = last_by_conversation.get(conversation.id)
        activity = as_utc(last.created_at if last else conversation.created_at)

        entries.append((activity, {
            "id": conversation.id,
            "other_user": {
                "id": other_user.id,
                "username": other_user.username,
            },
            "created_at": to_utc_iso(conversation.created_at),
            "last_message": (
                {
                    "text": make_preview(last.message),
                    "created_at": to_utc_iso(last.created_at),
                    "mine": last.sender_id == current_user.id,
                }
                if last
                else None
            ),
            "unread": unread_by_conversation.get(conversation.id, 0),
        }))

    # Newest activity first.
    entries.sort(key=lambda entry: entry[0], reverse=True)

    return {"conversations": [entry[1] for entry in entries]}


@router.get("/conversations/{conversation_id}/messages")
def get_private_messages(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_conversation_for_user(conversation_id, current_user.id, db)

    messages = (
        db.query(PrivateMessage, User.username)
        .join(User, PrivateMessage.sender_id == User.id)
        .filter(PrivateMessage.conversation_id == conversation_id)
        .order_by(PrivateMessage.id.desc())
        .limit(100)
        .all()
    )

    messages.reverse()

    return {
        "messages": [
            {
                "id": message.id,
                "sender_id": message.sender_id,
                "username": username,
                "message": message.message,
                "created_at": to_utc_iso(message.created_at),
            }
            for message, username in messages
        ]
    }


@router.post(
    "/conversations/{conversation_id}/messages",
    status_code=201,
)
def send_private_message(
    conversation_id: int,
    payload: PrivateMessageInput,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_conversation_for_user(conversation_id, current_user.id, db)

    if not private_message_limiter.allow(current_user.id):
        raise HTTPException(
            status_code=429,
            detail="You're sending messages too fast. Please slow down.",
        )

    message_text = payload.message.strip()

    if not message_text:
        raise HTTPException(
            status_code=422,
            detail="Message cannot be empty.",
        )

    new_message = PrivateMessage(
        conversation_id=conversation_id,
        sender_id=current_user.id,
        message=message_text,
        created_at=datetime.now(timezone.utc),
    )

    db.add(new_message)
    db.commit()
    db.refresh(new_message)

    return {
        "message": {
            "id": new_message.id,
            "sender_id": current_user.id,
            "username": current_user.username,
            "message": new_message.message,
            "created_at": to_utc_iso(new_message.created_at),
        }
    }


@router.post("/conversations/{conversation_id}/read")
def mark_conversation_read(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_conversation_for_user(conversation_id, current_user.id, db)

    if not read_limiter.allow(current_user.id):
        raise HTTPException(status_code=429, detail="Too many requests. Please slow down.")

    newest = (
        db.query(func.max(PrivateMessage.id))
        .filter(PrivateMessage.conversation_id == conversation_id)
        .scalar()
    ) or 0

    row = (
        db.query(PrivateRead)
        .filter(
            PrivateRead.conversation_id == conversation_id,
            PrivateRead.user_id == current_user.id,
        )
        .first()
    )

    now = datetime.now(timezone.utc)

    if row is None:
        db.add(PrivateRead(
            conversation_id=conversation_id,
            user_id=current_user.id,
            last_read_id=newest,
            updated_at=now,
        ))

        try:
            db.commit()
        except IntegrityError:
            # Another request created the row at the same moment: update that one.
            db.rollback()
            row = (
                db.query(PrivateRead)
                .filter(
                    PrivateRead.conversation_id == conversation_id,
                    PrivateRead.user_id == current_user.id,
                )
                .first()
            )

            if row is not None and newest > row.last_read_id:
                row.last_read_id = newest
                row.updated_at = now
                db.commit()

    elif newest > row.last_read_id:
        row.last_read_id = newest
        row.updated_at = now
        db.commit()

    return {"ok": True}