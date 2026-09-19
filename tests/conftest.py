import os
import subprocess
import sys

import pytest
import pytest_asyncio
from sqlalchemy import text

# Configuration is read at import time by app.db.session, so the test
# environment has to be complete BEFORE anything under `app` is imported.
# ENV_NAME=test relaxes production guards; the database is a throwaway
# one (see CLAUDE.md > Testing).
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://dashboard:dashboard@localhost:55432/dashboard_test",
)
os.environ.setdefault("ENV_NAME", "test")
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ.setdefault("SESSION_SECRET", "test-secret-test-secret-test-secret-0000")
os.environ.setdefault("ALOBOT_DATABASE_URL", "")


def alembic(*args: str) -> subprocess.CompletedProcess:
    """Run the real Alembic CLI against the test database, in a subprocess
    because env.py drives its own asyncio loop."""
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env={**os.environ, "DATABASE_URL": TEST_DATABASE_URL},
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="session", autouse=True)
def migrated_schema():
    """The real migrations, applied once per test session. Tests never use
    create_all: triggers and partial indexes only exist in migrations."""
    down = alembic("downgrade", "base")
    assert down.returncode == 0, down.stderr
    up = alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr
    yield


@pytest_asyncio.fixture(autouse=True)
async def clean_tables(migrated_schema):
    """Every test starts from empty tables (schema kept, rows truncated)."""
    from app.db.session import engine
    from app.web.routes.auth import login_limiter

    yield
    login_limiter.reset()
    async with engine.begin() as conn:
        rows = await conn.execute(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version'"
            )
        )
        tables = [r[0] for r in rows]
        if tables:
            await conn.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE"))


@pytest_asyncio.fixture
async def session():
    from app.db.session import async_session_maker

    async with async_session_maker() as s:
        yield s
