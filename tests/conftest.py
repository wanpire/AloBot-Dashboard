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
os.environ.setdefault("RUN_SWEEPS", "false")

# A COPY of AloBot's schema, built from AloBot's own migrations in
# `vendor/alobot`, read through a SELECT-only role exactly as production will
# be. Seeding uses the superuser URL; the app only ever sees the RO one.
ALOBOT_COPY_DB = "alobot_copy_test"
ALOBOT_ADMIN_URL = TEST_DATABASE_URL.rsplit("/", 1)[0] + "/" + ALOBOT_COPY_DB
ALOBOT_RO_URL = ALOBOT_ADMIN_URL.replace("//dashboard:dashboard@", "//dashboard_ro:ro@")
os.environ["ALOBOT_DATABASE_URL"] = ALOBOT_RO_URL


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


def _alobot_env() -> dict[str, str]:
    from urllib.parse import urlsplit

    u = urlsplit(TEST_DATABASE_URL)
    return {
        "PATH": os.environ["PATH"],
        "HOME": os.environ.get("HOME", "/tmp"),
        "BOT_TOKEN": "x",
        "ADMIN_IDS": "1",
        "IBSNG_BASE_URL": "http://x:1235",
        "IBSNG_USERNAME": "x",
        "IBSNG_PASSWORD": "x",
        "IBSNG_ISP_NAME": "x",
        "POSTGRES_HOST": u.hostname or "localhost",
        "POSTGRES_PORT": str(u.port or 5432),
        "POSTGRES_DB": ALOBOT_COPY_DB,
        "POSTGRES_USER": u.username or "",
        "POSTGRES_PASSWORD": u.password or "",
        "REDIS_HOST": "localhost",
    }


@pytest.fixture(scope="session", autouse=True)
def alobot_copy_schema():
    """AloBot's schema at the pinned commit, in its own throwaway database,
    plus the SELECT-only role the app connects with."""
    import asyncio

    import asyncpg

    vendor = os.path.join(os.path.dirname(os.path.dirname(__file__)), "vendor", "alobot")
    assert os.path.isdir(os.path.join(vendor, "alembic")), "run `make link-alobot` first"

    async def prepare():
        admin = await asyncpg.connect(TEST_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://").rsplit("/", 1)[0] + "/postgres")
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{ALOBOT_COPY_DB}"')
            await admin.execute(f'CREATE DATABASE "{ALOBOT_COPY_DB}"')
            exists = await admin.fetchval("SELECT 1 FROM pg_roles WHERE rolname = 'dashboard_ro'")
            if not exists:
                await admin.execute("CREATE ROLE dashboard_ro LOGIN PASSWORD 'ro'")
        finally:
            await admin.close()

    asyncio.run(prepare())
    up = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=vendor, env=_alobot_env(), capture_output=True, text=True
    )
    assert up.returncode == 0, up.stderr

    async def grant():
        conn = await asyncpg.connect(ALOBOT_ADMIN_URL.replace("postgresql+asyncpg://", "postgresql://"))
        try:
            await conn.execute("GRANT USAGE ON SCHEMA public TO dashboard_ro")
            await conn.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO dashboard_ro")
        finally:
            await conn.close()

    asyncio.run(grant())
    yield


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
    from app.api.ingest import device_limiter, ip_limiter
    from app.web.routes.auth import login_limiter

    yield
    login_limiter.reset()
    device_limiter.reset()
    ip_limiter.reset()
    from tests.alobot_seed import truncate_alobot

    await truncate_alobot()
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
