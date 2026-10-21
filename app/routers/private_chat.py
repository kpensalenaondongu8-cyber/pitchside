from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import PrivateConversation, PrivateMessage, User
from app.ratelimit import RateLimiter
from app.routers.auth import get_current_user, get_db
from app.timeutils import to_utc_iso


router = APIRouter(
    prefix="/private",
    tags=["Private Messaging"],
)

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# 5 private messages per 10 seconds for each user.
private_message_limiter = RateLimiter(max_events=5, per_seconds=10)
# 20 new conversations per hour for each user.
new_conversation_limiter = RateLimiter(max_events=20, per_seconds=3600)


class PrivateMessageInput(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


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
        .order_by(PrivateConversation.id.desc())
        .all()
    )

    def other_id(conversation):
        if conversation.user_one_id == current_user.id:
            return conversation.user_two_id
        return conversation.user_one_id

    # One query for all the other users, instead of one query per conversation.
    other_ids = {other_id(c) for c in conversations}
    users_by_id = {}

    if other_ids:
        users_by_id = {
            user.id: user
            for user in db.query(User).filter(User.id.in_(other_ids)).all()
        }

    results = []

    for conversation in conversations:
        other_user = users_by_id.get(other_id(conversation))

        if other_user is None:
            continue

        results.append({
            "id": conversation.id,
            "other_user": {
                "id": other_user.id,
                "username": other_user.username,
            },
            "created_at": to_utc_iso(conversation.created_at),
        })

    return {"conversations": results}


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