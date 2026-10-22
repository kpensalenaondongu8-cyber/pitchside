import json
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import ALLOWED_ORIGINS
from app.database import SessionLocal
from app.models import Match, MatchMessage, User
from app.ratelimit import RateLimiter
from app.security import COOKIE_NAME, read_auth_token
from app.timeutils import to_utc_iso

router = APIRouter(tags=["Match chat"])

MAX_MESSAGE_LENGTH = 1000
MAX_FRAME_LENGTH = 4096
MAX_TABS_PER_USER = 5

# 5 match chat messages per 10 seconds for each user (across all matches).
match_message_limiter = RateLimiter(max_events=5, per_seconds=10)


class RoomManager:
    """Keeps a separate set of connections for each match."""

    def __init__(self):
        self.rooms = {}  # match_id -> {websocket: {"user_id": ..., "username": ...}}

    def join(self, match_id, websocket, user_id, username):
        self.rooms.setdefault(match_id, {})[websocket] = {
            "user_id": user_id,
            "username": username,
        }

    def leave(self, match_id, websocket):
        room = self.rooms.get(match_id)
        if room is None:
            return
        room.pop(websocket, None)
        if not room:
            self.rooms.pop(match_id, None)

    def tabs_for_user(self, match_id, user_id):
        room = self.rooms.get(match_id, {})
        return sum(1 for c in room.values() if c["user_id"] == user_id)

    def viewers(self, match_id):
        """Everyone in this match room, once each, sorted by name."""
        room = self.rooms.get(match_id, {})
        people = {c["user_id"]: c["username"] for c in room.values()}
        return sorted(
            ({"id": uid, "username": name} for uid, name in people.items()),
            key=lambda person: person["username"].casefold(),
        )

    async def broadcast(self, match_id, data: dict):
        dead = []

        for connection in list(self.rooms.get(match_id, {})):
            try:
                await connection.send_json(data)
            except Exception:
                dead.append(connection)

        for connection in dead:
            self.leave(match_id, connection)

    async def broadcast_viewers(self, match_id):
        users = self.viewers(match_id)
        await self.broadcast(match_id, {
            "type": "presence",
            "count": len(users),
            "users": users,
        })


rooms = RoomManager()


def message_to_dict(message: MatchMessage, username: str) -> dict:
    return {
        "id": message.id,
        "username": username,
        "message": message.message,
        "created_at": to_utc_iso(message.created_at),
    }


@router.websocket("/ws/match/{match_id}")
async def match_chat(websocket: WebSocket, match_id: int):
    # Accept first so the browser can read our close codes (4401, 4403, ...).
    # Nothing is sent or read until every check below has passed.
    await websocket.accept()

    if websocket.headers.get("origin") not in ALLOWED_ORIGINS:
        await websocket.close(code=4403, reason="Origin not allowed")
        return

    token = websocket.cookies.get(COOKIE_NAME)
    token_data = read_auth_token(token) if token else None

    if token_data is None:
        await websocket.close(code=4401, reason="Authentication required")
        return

    db = SessionLocal()

    try:
        user = db.get(User, token_data["uid"])

        if user is None or user.auth_version != token_data["v"]:
            await websocket.close(code=4401, reason="Authentication required")
            return

        user_id = user.id
        username = user.username

        match_exists = (
            db.query(Match.id).filter(Match.api_id == match_id).first() is not None
        )

        if not match_exists:
            await websocket.close(code=4404, reason="Match not found")
            return

        if rooms.tabs_for_user(match_id, user_id) >= MAX_TABS_PER_USER:
            await websocket.close(code=4429, reason="Too many open tabs")
            return

        rows = (
            db.query(MatchMessage, User.username)
            .join(User, MatchMessage.user_id == User.id)
            .filter(MatchMessage.match_id == match_id)
            .order_by(MatchMessage.id.desc())
            .limit(100)
            .all()
        )
        rows.reverse()
        history = [message_to_dict(m, name) for m, name in rows]

    finally:
        db.close()

    rooms.join(match_id, websocket, user_id, username)

    try:
        await websocket.send_json({"type": "history", "messages": history})
        await rooms.broadcast_viewers(match_id)

        while True:
            raw_data = await websocket.receive_text()

            if not match_message_limiter.allow(user_id):
                await websocket.send_json({
                    "type": "error",
                    "detail": "You're sending messages too fast. Please slow down.",
                })
                continue

            if len(raw_data) > MAX_FRAME_LENGTH:
                await websocket.send_json({
                    "type": "error",
                    "detail": "Message is too large.",
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

            message_text = data.get("message") if isinstance(data, dict) else None

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

            if len(message_text) > MAX_MESSAGE_LENGTH:
                await websocket.send_json({
                    "type": "error",
                    "detail": "Message cannot exceed 1000 characters.",
                })
                continue

            db = SessionLocal()

            try:
                new_message = MatchMessage(
                    match_id=match_id,
                    user_id=user_id,
                    message=message_text,
                    created_at=datetime.now(timezone.utc),
                )
                db.add(new_message)
                db.commit()
                db.refresh(new_message)
                message_data = message_to_dict(new_message, username)
            finally:
                db.close()

            await rooms.broadcast(match_id, {
                "type": "message",
                "message": message_data,
            })

    except WebSocketDisconnect:
        pass

    finally:
        rooms.leave(match_id, websocket)
        await rooms.broadcast_viewers(match_id)