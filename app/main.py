from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates

from app.database import Base, engine
from app.routers import matches

BASE_DIR = Path(__file__).resolve().parent

# Create the database tables on startup (Alembic migrations come later).
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Pitchside")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app.include_router(matches.router)


@app.get("/health")
def health():
    return {"status": "ok", "app": "Pitchside"}


@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "page": "live",
            "subtitle": "Live matches. Updates every 30 seconds.",
            "endpoint": "/matches/live",
            "empty_text": "No live matches right now.",
        },
    )


@app.get("/fixtures")
def fixtures(request: Request):
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "page": "fixtures",
            "subtitle": "Today's matches. Updates every 30 seconds.",
            "endpoint": "/matches/today",
            "empty_text": "No matches today in the competitions we cover.",
        },
    )
