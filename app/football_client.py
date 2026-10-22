"""Talks to the football data provider and returns matches in one simple shape.

Two providers are supported, chosen with FOOTBALL_PROVIDER in .env:
  - "api-football": worldwide leagues (api-sports.io)
  - "football-data": football-data.org (12 competitions on the free plan)

If the chosen provider has no API key, sample matches are returned so the app
still runs and you can build pages before you have a key.
"""
import asyncio
import time
from datetime import datetime, timezone

import httpx

from app import config


class ProviderError(Exception):
    """The provider answered, but reported a problem (bad key, daily limit, ...)."""


_locks = {}
_blocked_until = 0.0            # don't call the provider before this time
quota = {"remaining": None, "day": None}
last_error = {"text": ""}       # the most recent provider error, shown while paused

_cache = {}

TTL = {"live": config.LIVE_CACHE_SECONDS, "today": config.TODAY_CACHE_SECONDS}

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
    {
        "api_id": 900003,
        "competition": "Sample League",
        "home_team": "Matchday Rovers",
        "away_team": "Kickroom Athletic",
        "home_score": None,
        "away_score": None,
        "status": "TIMED",
        "kickoff": "2026-10-14T19:00:00Z",
    },
]

# API-Football status codes -> the statuses Pitchside uses everywhere else.
API_FOOTBALL_STATUS = {
    "TBD": "TIMED",
    "NS": "TIMED",
    "1H": "IN_PLAY",
    "2H": "IN_PLAY",
    "ET": "IN_PLAY",
    "BT": "IN_PLAY",
    "P": "IN_PLAY",
    "LIVE": "IN_PLAY",
    "INT": "IN_PLAY",
    "HT": "PAUSED",
    "FT": "FINISHED",
    "AET": "FINISHED",
    "PEN": "FINISHED",
    "AWD": "FINISHED",
    "WO": "FINISHED",
    "PST": "POSTPONED",
    "SUSP": "POSTPONED",
    "CANC": "POSTPONED",
    "ABD": "POSTPONED",
}


def _normalize_football_data(raw: dict) -> dict:
    """Turn one football-data.org match into the shape Pitchside uses."""
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


def _normalize_api_football(raw: dict) -> dict:
    """Turn one API-Football fixture into the shape Pitchside uses."""
    fixture = raw.get("fixture") or {}
    league = raw.get("league") or {}
    teams = raw.get("teams") or {}
    goals = raw.get("goals") or {}
    short_status = (fixture.get("status") or {}).get("short", "")
    competition = league.get("name", "")
    if league.get("country") and league["country"] != "World":
        competition = f"{league['country']} · {competition}"
    return {
        "api_id": fixture["id"],
        "competition": competition,
        "home_team": (teams.get("home") or {}).get("name") or "TBD",
        "away_team": (teams.get("away") or {}).get("name") or "TBD",
        "home_score": goals.get("home"),
        "away_score": goals.get("away"),
        "status": API_FOOTBALL_STATUS.get(short_status, short_status),
        "kickoff": fixture.get("date", ""),
    }


def _provider_key() -> str:
    if config.FOOTBALL_PROVIDER == "football-data":
        return config.FOOTBALL_API_KEY
    return config.API_FOOTBALL_KEY


async def _call_provider(kind: str, day: str) -> list[dict]:
    """Make one real request to the provider. kind is "live" or "today"."""
    key = _provider_key()

    if config.FOOTBALL_PROVIDER == "football-data":
        url = f"{config.FOOTBALL_API_BASE}/matches"
        params = {"status": "LIVE"} if kind == "live" else {}
        headers = {"X-Auth-Token": key}
    else:
        url = f"{config.API_FOOTBALL_BASE}/fixtures"
        params = {"live": "all"} if kind == "live" else {"date": day}
        headers = {"x-apisports-key": key}

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(url, params=params, headers=headers)
        response.raise_for_status()
        remaining = response.headers.get("x-ratelimit-requests-remaining")
    if remaining is not None:
        quota["remaining"] = int(remaining)
        quota["day"] = day
    data = response.json()

    if config.FOOTBALL_PROVIDER == "football-data":
        return [_normalize_football_data(m) for m in data.get("matches", [])]

    # API-Football reports problems (bad key, daily limit) inside a normal 200 reply.
    if data.get("errors"):
        raise ProviderError(f"API-Football: {data['errors']}")
    return [_normalize_api_football(m) for m in data.get("response", [])]


async def _fetch_matches(kind: str) -> list[dict]:
    """Return matches, reusing a recent answer so we respect the rate limit."""
    global _blocked_until
    day = datetime.now(timezone.utc).date().isoformat()
    cache_key = "live" if kind == "live" else f"today-{day}"
    ttl = TTL[kind]

    cached = _cache.get(cache_key)
    if cached and time.time() - cached["time"] < ttl:
        return cached["data"]

    lock = _locks.setdefault(cache_key, asyncio.Lock())
    async with lock:                      # one provider call per key at a time
        cached = _cache.get(cache_key)    # someone may have refreshed it while we waited
        if cached and time.time() - cached["time"] < ttl:
            return cached["data"]

        low_quota = (
            quota["day"] == day
            and quota["remaining"] is not None
            and quota["remaining"] <= 5
        )
        if time.time() < _blocked_until or low_quota:
            if cached:
                return cached["data"]
            raise ProviderError(
                "Provider paused for a minute after this error: "
                + (last_error["text"] or f"quota almost used up (remaining: {quota['remaining']})")
            )

        try:
            matches = await _call_provider(kind, day)
        except (httpx.HTTPError, ProviderError) as error:
            last_error["text"] = str(error)
            _blocked_until = time.time() + 60   # back off for a minute
            if cached:
                return cached["data"]
            raise

        _cache[cache_key] = {"time": time.time(), "data": matches}
        return matches


async def fetch_live_matches() -> list[dict]:
    """Return matches that are being played right now."""
    if not _provider_key():
        return [m for m in SAMPLE_MATCHES if m["status"] in ("IN_PLAY", "PAUSED")]
    return await _fetch_matches("live")


async def fetch_today_matches() -> list[dict]:
    """Return today's matches: scheduled, live and finished."""
    if not _provider_key():
        return SAMPLE_MATCHES
    return await _fetch_matches("today")