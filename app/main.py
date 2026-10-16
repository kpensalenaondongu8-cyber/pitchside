from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates

from app.database import Base, engine
from app import models  # Register all SQLAlchemy models before create_all.
from app.routers import auth, matches
from app.routers import chat
from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.routers.auth import get_current_user, get_db

BASE_DIR = Path(__file__).resolve().parent

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Pitchside")
@app.middleware("http")
async def require_login(request: Request, call_next):
    public_paths = {
        "/auth/login",
        "/auth/register",
        "/health",
        "/docs",
        "/openapi.json",
        "/redoc",
    }

    path = request.url.path

    if path in public_paths or path.startswith("/auth/"):
        return await call_next(request)

    from app.security import COOKIE_NAME, read_auth_token
    from app.database import SessionLocal
    from app.models import User

    token = request.cookies.get(COOKIE_NAME)
    token_data = read_auth_token(token) if token else None

    if token_data is None:
        if path.startswith("/matches/") or path.startswith("/chat/"):
            from fastapi.responses import JSONResponse

            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required."},
            )

        return RedirectResponse(
            url=f"/auth/login?next={path}",
            status_code=303,
        )

    db = SessionLocal()

    try:
        user = db.query(User).filter(User.id == token_data["uid"]).first()

        if user is None or user.auth_version != token_data["v"]:
            if path.startswith("/matches/") or path.startswith("/chat/"):
                from fastapi.responses import JSONResponse

                return JSONResponse(
                    status_code=401,
                    content={"detail": "Authentication required."},
                )

            return RedirectResponse(
                url=f"/auth/login?next={path}",
                status_code=303,
            )

    finally:
        db.close()

    return await call_next(request)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app.include_router(matches.router)
app.include_router(auth.router)
app.include_router(chat.router)


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
