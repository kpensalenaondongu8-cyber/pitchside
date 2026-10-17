from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.competitions import group_by_competition, slugify, sort_matches
from app.database import SessionLocal
from app.football_client import ProviderError, fetch_live_matches, fetch_today_matches
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
    if not matches:
        return
    ids = [data["api_id"] for data in matches]
    existing = {
        m.api_id: m
        for m in db.query(Match).filter(Match.api_id.in_(ids)).all()
    }
    for data in matches:
        match = existing.get(data["api_id"])
        if match is None:
            db.add(Match(**data))
        else:
            for key, value in data.items():
                setattr(match, key, value)
    db.commit()

_last_saved = {}   # fetcher name -> the list we last saved to the database
async def load_matches(fetcher, db: Session) -> list[dict]:
    """Get matches with the given fetch function, save them, and return them sorted."""
    try:
        matches = await fetcher()
    except (httpx.HTTPError, ProviderError) as error:
        raise HTTPException(status_code=502, detail=f"Football API error: {error}")

    # The cache returns the same list until it refreshes, so only save fresh data.
    if _last_saved.get(fetcher.__name__) is not matches:
        save_matches(db, matches)
        _last_saved[fetcher.__name__] = matches
    return sort_matches(matches)

@router.get("/live")
async def live_matches(db: Session = Depends(get_db)):
    """Matches being played right now."""
    return await load_matches(fetch_live_matches, db)


# Routes with a fixed word (/today, /competitions) must stay ABOVE "/{api_id}",
# otherwise FastAPI treats the word as an id.
@router.get("/today")
async def today_matches(competition: Optional[str] = None, db: Session = Depends(get_db)):
    """Today's matches. Add ?competition=<slug> to see only one league."""
    matches = await load_matches(fetch_today_matches, db)
    if competition:
        matches = [m for m in matches if slugify(m["competition"]) == competition]
    return matches


@router.get("/competitions")
async def competitions(db: Session = Depends(get_db)):
    """Today's competitions with how many matches each has."""
    matches = await load_matches(fetch_today_matches, db)
    return group_by_competition(matches)


@router.get("/{api_id}")
async def match_detail(api_id: int, db: Session = Depends(get_db)):
    """One match by its API id. The match room page uses this.

    First we refresh today's matches, so the score is current. This reuses the shared
    cache, so it usually costs no API request. If the API is down or out of requests,
    we carry on with what we saved earlier.
    """
    try:
        await load_matches(fetch_today_matches, db)
    except HTTPException:
        pass
    match = db.query(Match).filter(Match.api_id == api_id).first()
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    return {
        "api_id": match.api_id,
        "competition": match.competition,
        "competition_slug": slugify(match.competition),
        "home_team": match.home_team,
        "away_team": match.away_team,
        "home_score": match.home_score,
        "away_score": match.away_score,
        "status": match.status,
        "kickoff": match.kickoff,
    }
