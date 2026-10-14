import os

from dotenv import load_dotenv

load_dotenv()

FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY", "")
FOOTBALL_API_BASE = os.getenv("FOOTBALL_API_BASE", "https://api.football-data.org/v4")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./pitchside.db")

# How long (seconds) we reuse an API response before asking the API again.
# Free plans have strict rate limits, so don't set this too low.
CACHE_SECONDS = 30
