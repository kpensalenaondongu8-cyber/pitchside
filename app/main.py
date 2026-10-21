from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import models  # Register all SQLAlchemy models before create_all.
from app.database import Base, SessionLocal, engine
from app.models import User
from app.routers import auth, chat, matches, private_chat
from app.security import COOKIE_NAME, read_auth_token

BASE_DIR = Path(__file__).resolve().parent

Base.metadata.create_all(bind=engine)

# The API docs pages are switched off so the site doesn't publish a map of its routes.
app = FastAPI(title="Pitchside", docs_url=None, redoc_url=None, openapi_url=None)

PUBLIC_PATHS = {"/health"}

# Calls to these paths come from JavaScript, so they get a JSON 401 instead of a redirect.
JSON_API_PREFIXES = ("/matches/", "/chat/", "/private/conversations")


def not_logged_in(path: str):
    """JSON 401 for API calls, redirect to the login page for normal pages."""
    if path.startswith(JSON_API_PREFIXES):
        return JSONResponse(
            status_code=401,
            content={"detail": "Authentication required."},
        )
    return RedirectResponse(url=f"/auth/login?next={quote(path)}", status_code=303)


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path

    if path in PUBLIC_PATHS or path.startswith("/auth/"):
        return await call_next(request)

    token = request.cookies.get(COOKIE_NAME)
    token_data = read_auth_token(token) if token else None

    if token_data is None:
        return not_logged_in(path)

    db = SessionLocal()
    try:
        user = db.get(User, token_data["uid"])
        if user is None or user.auth_version != token_data["v"]:
            return not_logged_in(path)
    finally:
        db.close()

    return await call_next(request)


templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app.include_router(matches.router)
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(private_chat.router)


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


@app.get("/match/{api_id}")
def match_room(request: Request, api_id: int):
    """The match room. The page loads the match's data itself from /matches/{api_id}."""
    return templates.TemplateResponse(
        request,
        "match.html",
        {"page": "match", "subtitle": "Match room", "endpoint": f"/matches/{api_id}"},
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