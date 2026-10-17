import json
from datetime import datetime, timezone

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import ChatMessage, User
from app.routers.auth import get_current_user, get_db
from app.security import COOKIE_NAME, read_auth_token

import time
from collections import defaultdict, deque


class RateLimiter:
    """Allow at most max_events per per_seconds for each key (such as a user id)."""

    def __init__(self, max_events: int, per_seconds: float):
        self.max_events = max_events
        self.per_seconds = per_seconds
        self._events = defaultdict(deque)

    def allow(self, key) -> bool:
        now = time.monotonic()
        events = self._events[key]
        while events and now - events[0] > self.per_seconds:
            events.popleft()
        if len(events) >= self.max_events:
            return False
        events.append(now)
        return True


message_limiter = RateLimiter(max_events=5, per_seconds=10)
router = APIRouter(tags=["Chat"])

templates = Jinja2Templates(directory="app/templates")


class MessageInput(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


class ConnectionManager:
    def __init__(self):
        self.active_connections = {}

    async def connect(self, websocket, user_id, username):
        await websocket.accept()

        self.active_connections[websocket] = {
            "user_id": user_id,
            "username": username,
        }

    def disconnect(self, websocket):
        self.active_connections.pop(websocket, None)

    def get_online_users(self):
        users = {}

        for connection in self.active_connections.values():
            user_id = connection["user_id"]
            username = connection["username"]
            users[user_id] = username

        return sorted(users.values(), key=str.casefold)

    async def broadcast(self, data: dict):
        disconnected = []

        for connection in list(self.active_connections.keys()):
            try:
                await connection.send_json(data)
            except (WebSocketDisconnect, RuntimeError):
                disconnected.append(connection)

        for connection in disconnected:
            self.disconnect(connection)

    async def broadcast_online_users(self):
        await self.broadcast({
            "type": "presence",
            "users": self.get_online_users(),
        })


manager = ConnectionManager()

@router.get("/chat/users")
def get_chat_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    users = (
        db.query(User)
        .filter(User.id != current_user.id)
        .order_by(User.username.asc())
        .all()
    )

    online_usernames = set(manager.get_online_users())

    return {
        "users": [
            {
                "id": user.id,
                "username": user.username,
                "online": user.username in online_usernames,
            }
            for user in users
        ]
    }
@router.get("/chat", response_class=HTMLResponse)
def chat_page(
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        current_user = get_current_user(request, db)
    except HTTPException:
        return RedirectResponse(
            url="/auth/login?next=/chat",
            status_code=303,
        )

    return templates.TemplateResponse(
        request,
        "chat.html",
        {"username": current_user.username},
    )


@router.get("/chat/messages")
def get_messages(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    messages = (
        db.query(ChatMessage, User.username)
        .join(User, ChatMessage.user_id == User.id)
        .order_by(ChatMessage.id.desc())
        .limit(100)
        .all()
    )

    messages.reverse()

    return {
        "messages": [
            {
                "id": message.id,
                "username": username,
                "message": message.message,
                "created_at": message.created_at.isoformat(),
            }
            for message, username in messages
        ]
    }


@router.post("/chat/messages", status_code=201)
def send_message(
    payload: MessageInput,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    message_text = payload.message.strip()

    if not message_text:
        raise HTTPException(
            status_code=422,
            detail="Message cannot be empty.",
        )

    if len(message_text) > 1000:
        raise HTTPException(
            status_code=422,
            detail="Message cannot exceed 1000 characters.",
        )

    new_message = ChatMessage(
        user_id=current_user.id,
        message=message_text,
        created_at=datetime.now(timezone.utc),
    )

    db.add(new_message)
    db.commit()
    db.refresh(new_message)

    return {
        "message": {
            "id": new_message.id,
            "username": current_user.username,
            "message": new_message.message,
            "created_at": new_message.created_at.isoformat(),
        }
    }


@router.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    token = websocket.cookies.get(COOKIE_NAME)
    token_data = read_auth_token(token) if token else None

    if token_data is None:
        await websocket.close(
            code=4401,
            reason="Authentication required",
        )
        return

    db = SessionLocal()

    try:
        current_user = (
            db.query(User)
            .filter(User.id == token_data["uid"])
            .first()
        )

        if (
            current_user is None
            or current_user.auth_version != token_data["v"]
        ):
            await websocket.close(
                code=4401,
                reason="Authentication required",
            )
            return

        user_id = current_user.id
        username = current_user.username

    finally:
        db.close()

    await manager.connect(
        websocket,
        user_id=user_id,
        username=username,
    )

    try:
        db = SessionLocal()

        try:
            history = (
                db.query(ChatMessage, User.username)
                .join(User, ChatMessage.user_id == User.id)
                .order_by(ChatMessage.id.desc())
                .limit(100)
                .all()
            )

            history.reverse()

            messages = [
                {
                    "id": message.id,
                    "username": message_username,
                    "message": message.message,
                    "created_at": message.created_at.isoformat(),
                }
                for message, message_username in history
            ]

        finally:
            db.close()

        await websocket.send_json({
            "type": "history",
            "messages": messages,
        })

        await manager.broadcast_online_users()

        while True:
            raw_data = await websocket.receive_text()

            try:
                data = json.loads(raw_data)
            except json.JSONDecodeError:
                await websocket.send_json({
                    "type": "error",
                    "detail": "Invalid message format.",
                })
                continue

            if not isinstance(data, dict):
                await websocket.send_json({
                    "type": "error",
                    "detail": "Invalid message format.",
                })
                continue

            message_text = data.get("message")

            if not isinstance(message_text, str):
                await websocket.send_json({
                    "type": "error",
                    "detail": "Message must be text.",
                })
                continue

            message_text = message_text.strip()

            if not message_text:
                await websocket.send_json({
                    "type": "error",
                    "detail": "Message cannot be empty.",
                })
                continue

            if len(message_text) > 1000:
                await websocket.send_json({
                    "type": "error",
                    "detail": "Message cannot exceed 1000 characters.",
                })
                continue

            db = SessionLocal()

            try:
                new_message = ChatMessage(
                    user_id=user_id,
                    message=message_text,
                    created_at=datetime.now(timezone.utc),
                )

                db.add(new_message)
                db.commit()
                db.refresh(new_message)

                message_data = {
                    "id": new_message.id,
                    "username": username,
                    "message": new_message.message,
                    "created_at": new_message.created_at.isoformat(),
                }

            finally:
                db.close()

            await manager.broadcast({
                "type": "message",
                "message": message_data,
            })

    except WebSocketDisconnect:
        pass

    finally:
        manager.disconnect(websocket)
        await manager.broadcast_online_users()