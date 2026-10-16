import os

os.environ["AUTH_SECRET_KEY"] = "test-only-secret-key-that-is-long-enough"
os.environ["AUTH_COOKIE_SECURE"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.main import app
from app.routers.auth import get_db
from app.security import COOKIE_NAME, hash_password, read_auth_token, verify_password

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    client.cookies.clear()


def test_password_hashing_and_wrong_password():
    hashed = hash_password("correct horse battery")
    assert hashed != "correct horse battery"
    assert verify_password("correct horse battery", hashed)
    assert not verify_password("wrong password", hashed)


def test_register_me_logout_and_replayed_cookie():
    response = client.post("/auth/register", json={"username": "Fan_123", "password": "correct horse battery"})
    assert response.status_code == 201
    assert response.json()["user"]["username"] == "Fan_123"
    cookie = response.cookies[COOKIE_NAME]
    assert client.get("/auth/me").json()["user"]["username"] == "Fan_123"
    assert "httponly" in response.headers["set-cookie"].lower()
    assert client.post("/auth/logout").status_code == 200
    client.cookies.set(COOKIE_NAME, cookie)
    assert client.get("/auth/me").status_code == 401


def test_duplicate_username_rejected_case_insensitively():
    payload = {"username": "Supporter", "password": "correct horse battery"}
    assert client.post("/auth/register", json=payload).status_code == 201
    assert client.post("/auth/register", json={**payload, "username": "supporter"}).status_code == 409


def test_wrong_password_and_tampered_cookie_rejected():
    client.post("/auth/register", json={"username": "fanuser", "password": "correct horse battery"})
    client.post("/auth/logout")
    response = client.post("/auth/login", json={"username": "fanuser", "password": "incorrect password"})
    assert response.status_code == 401
    client.cookies.set(COOKIE_NAME, "tampered.cookie.value")
    assert client.get("/auth/me").status_code == 401
    assert read_auth_token("tampered.cookie.value") is None


def test_validation_and_login():
    assert client.post("/auth/register", json={"username": "x", "password": "short"}).status_code == 422
    assert client.post("/auth/register", json={"username": "good_user", "password": "long-enough-password"}).status_code == 201
    client.post("/auth/logout")
    response = client.post("/auth/login", json={"username": "good_user", "password": "long-enough-password"})
    assert response.status_code == 200
    assert client.get("/auth/me").status_code == 200


def test_auth_pages_load():
    assert client.get("/auth/register").status_code == 200
    assert client.get("/auth/login").status_code == 200


def test_me_requires_authentication():
    assert client.get("/auth/me").status_code == 401
