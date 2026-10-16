import os

# A test-only secret; never use this value for a deployed application.
os.environ.setdefault("AUTH_SECRET_KEY", "test-only-secret-key-that-is-long-enough")
os.environ.setdefault("AUTH_COOKIE_SECURE", "false")
