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
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import ALLOWED_ORIGINS
from app.database import SessionLocal
from app.deletions import (
    GROUP,
    SEEN_DETAIL,
    DeleteInput,
    delete_limiter,
    load_deletions,
    record_deletion,
)
from app.models import ChatMessage, GroupRead, User
from app.ratelimit import RateLimiter
from app.routers.auth import get_current_user, get_db
from app.security import COOKIE_NAME, read_auth_token
from app.timeutils import to_utc_iso

router = APIRouter(tags=["Chat"])

templates = Jinja2Templates(directory="app/templates")

# 5 group chat messages per 10 seconds for each user.
message_limiter = RateLimiter(max_events=5, per_seconds=10)
# 30 "I have seen up to here" reports per minute for each user.
read_limiter = RateLimiter(max_events=30, per_seconds=60)


class MessageInput(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


class ReadInput(BaseModel):
    last_id: int = Field(ge=0)


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
            users[connection["user_id"]] = connection["username"]

        return sorted(users.values(), key=str.casefold)

    async def broadcast(self, data: dict):
        disconnected = []

        for connection in list(self.active_connections.keys()):
            try:
                await connection.send_json(data)
            except Exception:
                disconnected.append(connection)

        for connection in disconnected:
            self.disconnect(connection)

    async def broadcast_online_users(self):
        await self.broadcast({
            "type": "presence",
            "users": self.get_online_users(),
        })


manager = ConnectionManager()


def visible_group_messages(db: Session, rows: list, viewer_id: int) -> list:
    """Turn (message, username) rows into JSON, hiding or replacing deleted messages."""
    everyone, hidden = load_deletions(db, GROUP, [message.id for message, _ in rows], viewer_id)

    # The newest message that any other user's screen has displayed.
    seen_up_to = (
        db.query(func.max(GroupRead.last_read_id))
        .filter(GroupRead.user_id != viewer_id)
        .scalar()
    ) or 0

    items = []

    for message, username in rows:
        if message.id in hidden:
            continue

        deleted = message.id in everyone

        items.append({
            "id": message.id,
            "username": username,
            "message": "" if deleted else message.message,
            "deleted": deleted,
            "can_unsend": (
                not deleted
                and message.user_id == viewer_id
                and message.id > seen_up_to
            ),
            "created_at": to_utc_iso(message.created_at),
        })

    return items


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
    rows = (
        db.query(ChatMessage, User.username)
        .join(User, ChatMessage.user_id == User.id)
        .order_by(ChatMessage.id.desc())
        .limit(100)
        .all()
    )

    rows.reverse()

    return {"messages": visible_group_messages(db, rows, current_user.id)}


@router.post("/chat/messages", status_code=201)
def send_message(
    payload: MessageInput,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not message_limiter.allow(current_user.id):
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
            "deleted": False,
            "can_unsend": True,
            "created_at": to_utc_iso(new_message.created_at),
        }
    }


@router.post("/chat/read")
def mark_group_read(
    payload: ReadInput,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The browser reports the newest message it has displayed while the page was visible."""
    if not read_limiter.allow(current_user.id):
        raise HTTPException(status_code=429, detail="Too many requests. Please slow down.")

    newest = db.query(func.max(ChatMessage.id)).scalar() or 0
    last_id = min(payload.last_id, newest)
    now = datetime.now(timezone.utc)

    row = db.query(GroupRead).filter(GroupRead.user_id == current_user.id).first()

    if row is None:
        db.add(GroupRead(user_id=current_user.id, last_read_id=last_id, updated_at=now))

        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            row = db.query(GroupRead).filter(GroupRead.user_id == current_user.id).first()

            if row is not None and last_id > row.last_read_id:
                row.last_read_id = last_id
                row.updated_at = now
                db.commit()

    elif last_id > row.last_read_id:
        row.last_read_id = last_id
        row.updated_at = now
        db.commit()

    return {"ok": True}


@router.post("/chat/messages/{message_id}/delete")
async def delete_group_message(
    message_id: int,
    payload: DeleteInput,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not delete_limiter.allow(current_user.id):
        raise HTTPException(status_code=429, detail="Too many deletes. Please wait a minute.")

    message = db.get(ChatMessage, message_id)

    # Only the sender can delete a message.
    if message is None or message.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Message not found.")

    if payload.scope == "self":
        record_deletion(db, GROUP, message_id, current_user.id, "self")
        return {"ok": True}

    everyone, _ = load_deletions(db, GROUP, [message_id], current_user.id)

    if message_id in everyone:
        raise HTTPException(status_code=400, detail="This message is already deleted.")

    seen_by_someone = (
        db.query(GroupRead.id)
        .filter(GroupRead.user_id != current_user.id, GroupRead.last_read_id >= message_id)
        .first()
        is not None
    )

    if seen_by_someone:
        raise HTTPException(status_code=409, detail=SEEN_DETAIL)

    record_deletion(db, GROUP, message_id, current_user.id, "everyone")

    await manager.broadcast({
        "type": "deleted",
        "id": message_id,
        "username": current_user.username,
    })

    return {"ok": True}


@router.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    # Only accept connections that come from our own website.
    if websocket.headers.get("origin") not in ALLOWED_ORIGINS:
        await websocket.close(code=4403, reason="Origin not allowed")
        return

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

            messages = visible_group_messages(db, history, user_id)

        finally:
            db.close()

        await websocket.send_json({
            "type": "history",
            "messages": messages,
        })

        await manager.broadcast_online_users()

        while True:
            raw_data = await websocket.receive_text()

            if not message_limiter.allow(user_id):
                await websocket.send_json({
                    "type": "error",
                    "detail": "You're sending messages too fast. Please slow down.",
                })
                continue

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
                    "deleted": False,
                    "can_unsend": True,
                    "created_at": to_utc_iso(new_message.created_at),
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