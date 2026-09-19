import os

# Configuration is read at import time by app.db.session, so the test
# environment has to be complete BEFORE anything under `app` is imported.
# ENV_NAME=test relaxes production guards; the database is a throwaway
# one (see CLAUDE.md > Testing).
os.environ.setdefault("ENV_NAME", "test")
os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://dashboard:dashboard@localhost:5432/dashboard_test",
    ),
)
os.environ.setdefault("SESSION_SECRET", "test-secret-test-secret-test-secret-0000")
os.environ.setdefault("ALOBOT_DATABASE_URL", "")
