import os

from dotenv import load_dotenv

load_dotenv()

# Which football data provider to use: "api-football" (worldwide) or "football-data" (12 competitions on the free plan).
FOOTBALL_PROVIDER = os.getenv("FOOTBALL_PROVIDER", "api-football")

# API-Football (api-sports.io): https://dashboard.api-football.com
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "")
API_FOOTBALL_BASE = os.getenv("API_FOOTBALL_BASE", "https://v3.football.api-sports.io")

# football-data.org: https://www.football-data.org/client/register
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY", "")
FOOTBALL_API_BASE = os.getenv("FOOTBALL_API_BASE", "https://api.football-data.org/v4")

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./pitchside.db")

# How long (seconds) we reuse an API answer before asking the API again.
# API-Football's free plan allows only 100 requests per DAY, so keep this high
# (900 or more) on the free plan. On a paid plan you can lower it.
CACHE_SECONDS = int(os.getenv("CACHE_SECONDS", "300"))
