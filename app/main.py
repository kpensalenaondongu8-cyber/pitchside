from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates

from app.database import Base, engine
from app.routers import matches

BASE_DIR = Path(__file__).resolve().parent

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Pitchside")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app.include_router(matches.router)


def render_page(request: Request, page: str, subtitle: str, endpoint: str, empty_text: str, slug: str = ""):
    """Every page uses home.html. Only the data below changes between them."""
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "page": page,
            "subtitle": subtitle,
            "endpoint": endpoint,
            "empty_text": empty_text,
            "slug": slug,
        },
    )


@app.get("/health")
def health():
    return {"status": "ok", "app": "Pitchside"}


@app.get("/")
def home(request: Request):
    return render_page(
        request,
        page="leagues",
        subtitle="Pick a league to see today's matches.",
        endpoint="/matches/competitions",
        empty_text="No matches today in the competitions we cover.",
    )


@app.get("/live")
def live(request: Request):
    return render_page(
        request,
        page="live",
        subtitle="Live matches from every league.",
        endpoint="/matches/live",
        empty_text="No live matches right now.",
    )


@app.get("/fixtures")
def fixtures(request: Request):
    return render_page(
        request,
        page="fixtures",
        subtitle="All of today's matches.",
        endpoint="/matches/today",
        empty_text="No matches today in the competitions we cover.",
    )


@app.get("/league/{slug}")
def league(request: Request, slug: str):
    return render_page(
        request,
        page="league",
        subtitle="Today's matches in this league.",
        endpoint=f"/matches/today?competition={quote(slug)}",
        empty_text="No matches today in this league.",
        slug=slug,
    )
