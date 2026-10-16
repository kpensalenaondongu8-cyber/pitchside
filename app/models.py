from sqlalchemy import Column, Integer, String

from app.database import Base
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    message = Column(String(1000), nullable=False)

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    
class Match(Base):
    """A football match, saved from the football API."""

    __tablename__ = "matches"

    id = Column(Integer, primary_key=True, index=True)
    api_id = Column(Integer, unique=True, index=True, nullable=False)
    competition = Column(String, default="")
    home_team = Column(String, nullable=False)
    away_team = Column(String, nullable=False)
    home_score = Column(Integer, nullable=True)
    away_score = Column(Integer, nullable=True)
    status = Column(String, default="")
    kickoff = Column(String, default="")  # ISO time string from the API


class User(Base):
    """A Pitchside account. Passwords are stored only as salted hashes."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(30), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    auth_version = Column(Integer, nullable=False, default=0)
