import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.football_client import fetch_live_matches
from app.models import Match

router = APIRouter(prefix="/matches", tags=["matches"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def save_matches(db: Session, matches: list[dict]) -> None:
    """Insert new matches and update ones we already have."""
    for data in matches:
        match = db.query(Match).filter(Match.api_id == data["api_id"]).first()
        if match is None:
            db.add(Match(**data))
        else:
            for key, value in data.items():
                setattr(match, key, value)
    db.commit()


@router.get("/live")
async def live_matches(db: Session = Depends(get_db)):
    """Matches being played right now."""
    try:
        matches = await fetch_live_matches()
    except httpx.HTTPError as error:
        raise HTTPException(status_code=502, detail=f"Football API error: {error}")
    save_matches(db, matches)
    return matches


@router.get("/{api_id}")
def match_detail(api_id: int, db: Session = Depends(get_db)):
    """One saved match by its API id (the match room page will use this)."""
    match = db.query(Match).filter(Match.api_id == api_id).first()
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    return {
        "api_id": match.api_id,
        "competition": match.competition,
        "home_team": match.home_team,
        "away_team": match.away_team,
        "home_score": match.home_score,
        "away_score": match.away_score,
        "status": match.status,
        "kickoff": match.kickoff,
    }
