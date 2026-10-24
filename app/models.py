from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint

from app.database import Base


class PrivateConversation(Base):
    __tablename__ = "private_conversations"
    __table_args__ = (
        UniqueConstraint("user_one_id", "user_two_id", name="uq_private_pair"),
    )

    id = Column(Integer, primary_key=True)
    user_one_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    user_two_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
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


class PrivateMessage(Base):
    __tablename__ = "private_messages"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(
        Integer,
        ForeignKey("private_conversations.id"),
        nullable=False,
        index=True,
    )
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    message = Column(String(1000), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class MatchMessage(Base):
    """A chat message in one match's room. match_id is the match's API id."""

    __tablename__ = "match_messages"

    id = Column(Integer, primary_key=True, index=True)
    match_id = Column(Integer, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    message = Column(String(1000), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

class HighlightVideo(Base):
    """An official YouTube video (highlights) linked to a match. Only the video id is stored."""

    __tablename__ = "highlight_videos"
    __table_args__ = (
        UniqueConstraint("match_id", "video_id", name="uq_highlight_match_video"),
    )

    id = Column(Integer, primary_key=True, index=True)
    match_id = Column(Integer, nullable=False, index=True)
    video_id = Column(String(11), nullable=False)
    title = Column(String(150), nullable=False, default="Highlights")
    added_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

class Skit(Base):
    """A skit shared on Pitchside. status: pending, approved, rejected, removed or reported."""

    __tablename__ = "skits"

    id = Column(Integer, primary_key=True, index=True)
    source = Column(String(20), nullable=False, default="youtube")
    video_id = Column(String(64), nullable=False, index=True)
    title = Column(String(100), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="pending", index=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    reviewed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)


class SkitReport(Base):
    """One user's report of one skit. A user can report a skit only once."""

    __tablename__ = "skit_reports"
    __table_args__ = (
        UniqueConstraint("skit_id", "user_id", name="uq_skit_report_user"),
    )

    id = Column(Integer, primary_key=True, index=True)
    skit_id = Column(Integer, ForeignKey("skits.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )