"""Password hashing and signed authentication-cookie helpers."""
import hashlib
import hmac
import os
import secrets

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

PBKDF2_ITERATIONS = 600_000
COOKIE_NAME = "pitchside_auth"
COOKIE_MAX_AGE = 60 * 60 * 24 * 7  


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = stored_hash.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _serializer() -> URLSafeTimedSerializer:
    from app.config import AUTH_SECRET_KEY
    if not AUTH_SECRET_KEY or AUTH_SECRET_KEY == "change-me-in-production":
        raise RuntimeError("AUTH_SECRET_KEY is missing. Set a strong random secret in the environment.")
    return URLSafeTimedSerializer(AUTH_SECRET_KEY, salt="pitchside-auth-v1")


def create_auth_token(user_id: int, auth_version: int) -> str:
    return _serializer().dumps({"uid": user_id, "v": auth_version})


def read_auth_token(token: str) -> dict | None:
    try:
        data = _serializer().loads(token, max_age=COOKIE_MAX_AGE)
        if not isinstance(data, dict) or not isinstance(data.get("uid"), int) or not isinstance(data.get("v"), int):
            return None
        return data
    except (BadSignature, SignatureExpired, RuntimeError, TypeError, ValueError):
        return None


def cookie_secure() -> bool:
    return os.getenv("AUTH_COOKIE_SECURE", "false").lower() in {"1", "true", "yes"}
