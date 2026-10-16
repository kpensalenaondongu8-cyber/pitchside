# Pitchside

A live matchday hub for football fans: live scores, match rooms, chat and reactions.
Built with Python and FastAPI.

## Run it

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # then add your football API key (optional at first)
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000 for the home page and http://127.0.0.1:8000/docs for the API docs.

Without an API key the app shows sample matches, so you can build pages right away.

Pitchside supports two data providers, chosen with `FOOTBALL_PROVIDER` in `.env`:

- `api-football` (default): worldwide leagues. Get a key at https://dashboard.api-football.com
  and set `API_FOOTBALL_KEY`. The free plan allows 100 requests per day, so keep
  `CACHE_SECONDS` at 900 or more.
- `football-data`: 12 competitions on the free plan. Get a key at
  https://www.football-data.org/client/register and set `FOOTBALL_API_KEY`.

If you switch provider, delete `pitchside.db` once, because the two providers use different match ids.

## Project structure

```
app/
  main.py            FastAPI app, home page, /health
  config.py          Settings from .env
  database.py        SQLAlchemy engine and session
  models.py          Database tables (Match for now)
  football_client.py Fetches and caches live matches from the API
  competitions.py    Groups matches by league (slugs, top-league ordering)
  routers/matches.py /matches/live, /matches/today, /matches/competitions, /matches/{api_id}
  templates/home.html
```

## Next steps

1. Match room page (`/match/{id}`) with events and lineups
2. Users and login (JWT)
3. WebSocket live chat per match
4. Background job that polls the API and pushes score changes over WebSockets
5. Reactions, polls, then a skits feed
