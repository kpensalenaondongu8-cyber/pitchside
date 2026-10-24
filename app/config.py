import os

from dotenv import load_dotenv

load_dotenv()


def _clean(value: str) -> str:
    """Remove spaces, line breaks and quote marks that sneak in when pasting a value."""
    return value.strip().strip('"').strip("'").strip()


# Which football data provider to use: "api-football" (worldwide) or "football-data" (12 competitions on the free plan).
FOOTBALL_PROVIDER = _clean(os.getenv("FOOTBALL_PROVIDER", "api-football"))

# API-Football (api-sports.io): https://dashboard.api-football.com
API_FOOTBALL_KEY = _clean(os.getenv("API_FOOTBALL_KEY", ""))
API_FOOTBALL_BASE = _clean(os.getenv("API_FOOTBALL_BASE", "https://v3.football.api-sports.io"))

# Seconds to reuse an API answer. On API-Football's free plan (100 requests/day), keep these high.
LIVE_CACHE_SECONDS = int(os.getenv("LIVE_CACHE_SECONDS", "600"))
TODAY_CACHE_SECONDS = int(os.getenv("TODAY_CACHE_SECONDS", "3600"))

# football-data.org: https://www.football-data.org/client/register
FOOTBALL_API_KEY = _clean(os.getenv("FOOTBALL_API_KEY", ""))
FOOTBALL_API_BASE = _clean(os.getenv("FOOTBALL_API_BASE", "https://api.football-data.org/v4"))

DATABASE_URL = _clean(os.getenv("DATABASE_URL", "sqlite:///./pitchside.db"))
# Name the driver explicitly: newer SQLAlchemy defaults to "psycopg", but we install psycopg2.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

# Required for signed login cookies. Set a unique random value in production.
AUTH_SECRET_KEY = _clean(os.getenv("AUTH_SECRET_KEY", ""))
if not AUTH_SECRET_KEY:
    raise RuntimeError("AUTH_SECRET_KEY is not set")

# Websites allowed to open the chat WebSocket (your site's address, without a trailing slash).
ALLOWED_ORIGINS = {
    o.strip().rstrip("/")
    for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",")
    if o.strip()
}
# Usernames allowed to add and remove highlight videos, separated by commas.
ADMIN_USERNAMES = {
    name.strip().lower()
    for name in os.getenv("ADMIN_USERNAMES", "").split(",")
    if name.strip()
}