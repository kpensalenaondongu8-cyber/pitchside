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
Get a free key at https://www.football-data.org/client/register and put it in `.env`
as `FOOTBALL_API_KEY`.

## Project structure

```
app/
  main.py            FastAPI app, home page, /health
  config.py          Settings from .env
  database.py        SQLAlchemy engine and session
  models.py          Database tables (Match for now)
  football_client.py Fetches and caches live matches from the API
  routers/matches.py /matches/live and /matches/{api_id}
  templates/home.html
```

## Next steps

1. Match room page (`/match/{id}`) with events and lineups
2. Users and login (JWT)
3. WebSocket live chat per match
4. Background job that polls the API and pushes score changes over WebSockets
5. Reactions, polls, then a skits feed
