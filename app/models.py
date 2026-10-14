from sqlalchemy import Column, Integer, String

from app.database import Base


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
