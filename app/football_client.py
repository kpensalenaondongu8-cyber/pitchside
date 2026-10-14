"""Talks to the football data API (football-data.org by default).

If no API key is set, it returns a few sample matches so the app still runs
and you can build the pages before you have a key.
"""

import time

import httpx

from app import config

_cache = {"time": 0.0, "data": []}

SAMPLE_MATCHES = [
    {
        "api_id": 900001,
        "competition": "Sample League",
        "home_team": "Pitchside FC",
        "away_team": "Banter United",
        "home_score": 2,
        "away_score": 1,
        "status": "IN_PLAY",
        "kickoff": "2026-10-14T15:00:00Z",
    },
    {
        "api_id": 900002,
        "competition": "Sample League",
        "home_team": "Gist City",
        "away_team": "Terrace Town",
        "home_score": 0,
        "away_score": 0,
        "status": "PAUSED",
        "kickoff": "2026-10-14T15:00:00Z",
    },
]


def _normalize(raw: dict) -> dict:
    """Turn one football-data.org match into the simple shape Pitchside uses."""
    full_time = (raw.get("score") or {}).get("fullTime") or {}
    return {
        "api_id": raw["id"],
        "competition": (raw.get("competition") or {}).get("name", ""),
        "home_team": (raw.get("homeTeam") or {}).get("name") or "TBD",
        "away_team": (raw.get("awayTeam") or {}).get("name") or "TBD",
        "home_score": full_time.get("home"),
        "away_score": full_time.get("away"),
        "status": raw.get("status", ""),
        "kickoff": raw.get("utcDate", ""),
    }


async def fetch_live_matches() -> list[dict]:
    """Return matches that are being played right now."""
    if not config.FOOTBALL_API_KEY:
        return SAMPLE_MATCHES

    # Reuse a recent response so we don't burn the API rate limit.
    if time.time() - _cache["time"] < config.CACHE_SECONDS:
        return _cache["data"]

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            f"{config.FOOTBALL_API_BASE}/matches",
            params={"status": "LIVE"},
            headers={"X-Auth-Token": config.FOOTBALL_API_KEY},
        )
        response.raise_for_status()

    matches = [_normalize(m) for m in response.json().get("matches", [])]
    _cache["time"] = time.time()
    _cache["data"] = matches
    return matches
