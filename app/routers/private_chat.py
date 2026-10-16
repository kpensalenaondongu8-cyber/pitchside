from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models import PrivateConversation, PrivateMessage, User
from app.routers.auth import get_current_user, get_db


router = APIRouter(
    prefix="/private",
    tags=["Private Messaging"],
)


class PrivateMessageInput(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


def get_conversation_for_user(
    conversation_id: int,
    user_id: int,
    db: Session,
):
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

    other_user = (
        db.query(User)
        .filter(User.id == other_user_id)
        .first()
    )

    if other_user is None:
        raise HTTPException(
            status_code=404,
            detail="User not found.",
        )

    user_one_id = min(current_user.id, other_user_id)
    user_two_id = max(current_user.id, other_user_id)

    conversation = (
        db.query(PrivateConversation)
        .filter(
            PrivateConversation.user_one_id == user_one_id,
            PrivateConversation.user_two_id == user_two_id,
        )
        .first()
    )

    if conversation is None:
        conversation = PrivateConversation(
            user_one_id=user_one_id,
            user_two_id=user_two_id,
            created_at=datetime.now(timezone.utc),
        )

        db.add(conversation)
        db.commit()
        db.refresh(conversation)

    return {
        "id": conversation.id,
        "other_user": {
            "id": other_user.id,
            "username": other_user.username,
        },
        "created_at": conversation.created_at.isoformat(),
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

    results = []

    for conversation in conversations:
        other_user_id = (
            conversation.user_two_id
            if conversation.user_one_id == current_user.id
            else conversation.user_one_id
        )

        other_user = (
            db.query(User)
            .filter(User.id == other_user_id)
            .first()
        )

        if other_user is None:
            continue

        results.append({
            "id": conversation.id,
            "other_user": {
                "id": other_user.id,
                "username": other_user.username,
            },
            "created_at": conversation.created_at.isoformat(),
        })

    return {"conversations": results}


@router.get("/conversations/{conversation_id}/messages")
def get_private_messages(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_conversation_for_user(
        conversation_id,
        current_user.id,
        db,
    )

    messages = (
        db.query(PrivateMessage, User.username)
        .join(User, PrivateMessage.sender_id == User.id)
        .filter(
            PrivateMessage.conversation_id == conversation_id
        )
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
                "created_at": message.created_at.isoformat(),
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
    get_conversation_for_user(
        conversation_id,
        current_user.id,
        db,
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
            "created_at": new_message.created_at.isoformat(),
        }
    }