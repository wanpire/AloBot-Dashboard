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
# The write role is scoped to the tables the dashboard may edit; everything
# else it can only read. Production gets the same shape in Phase 7.
ALOBOT_RW_URL = ALOBOT_ADMIN_URL.replace("//dashboard:dashboard@", "//dashboard_rw:rw@")
os.environ["ALOBOT_DATABASE_URL"] = ALOBOT_RO_URL
os.environ["ALOBOT_WRITE_DATABASE_URL"] = ALOBOT_RW_URL
os.environ.setdefault("ALOBOT_DB_WRITES_ENABLED", "true")

# The only AloBot tables this project may ever write. Anything else stays
# SELECT-only even for the write role - that is what makes "the dashboard
# cannot touch payments or vpn_users" a privilege, not a promise.
# Phase 7: column-scoped access, so "the dashboard cannot read or change a
# customer's details" stays true while blocking one is possible.
ALOBOT_COLUMN_GRANTS = (
    "GRANT INSERT (telegram_id, is_blocked), UPDATE (is_blocked) ON bot_users TO dashboard_rw",
    "GRANT UPDATE (balance) ON resellers TO dashboard_rw",
)

ALOBOT_WRITABLE_TABLES = (
    "services", "service_locations", "app_config", "discount_codes",
    "tutorial_platforms", "tutorial_protocols", "tutorial_guides",
    "download_links", "openvpn_profiles", "admin_users",
)


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
            # An aborted earlier run can leave a connection behind, and one
            # stale backend would otherwise fail the whole session at setup.
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1 AND pid <> pg_backend_pid()",
                ALOBOT_COPY_DB,
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{ALOBOT_COPY_DB}"')
            await admin.execute(f'CREATE DATABASE "{ALOBOT_COPY_DB}"')
            for role, password in (("dashboard_ro", "ro"), ("dashboard_rw", "rw")):
                exists = await admin.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role)
                if not exists:
                    await admin.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{password}'")
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
            await conn.execute("GRANT USAGE ON SCHEMA public TO dashboard_ro, dashboard_rw")
            await conn.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO dashboard_ro, dashboard_rw")
            for table in ALOBOT_WRITABLE_TABLES:
                await conn.execute(f"GRANT INSERT, UPDATE, DELETE ON {table} TO dashboard_rw")
            # Phase 7 adds two COLUMN-level grants and nothing wider: the
            # dashboard may flip a customer's block flag and move a reseller's
            # balance, and may not touch another column of either table.
            for grant in ALOBOT_COLUMN_GRANTS:
                await conn.execute(grant)
            await conn.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO dashboard_rw")
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
    from app.services import pace
    from app.web.routes.auth import login_limiter

    yield
    pace.reset()
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


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
async def base_url():
    """The app served over a real TCP socket, inside this test session's own
    event loop, so the browser (or a load generator) and the test share one
    database. ASGI-in-process transports cannot show queueing at the socket,
    which is exactly what the load test is about."""
    import asyncio

    import uvicorn

    from app.main import app

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"))
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.05)
    else:  # pragma: no cover - the server failed to come up
        task.cancel()
        raise RuntimeError("uvicorn did not start")
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    await task


async def alobot_admin_execute(*statements: str) -> None:
    """Run DDL on the AloBot COPY as its owner. Used by tests that need a
    schema change AloBot has not merged yet, so this project's half can be
    built and proven before that change lands."""
    import asyncpg

    conn = await asyncpg.connect(ALOBOT_ADMIN_URL.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        for statement in statements:
            await conn.execute(statement)
    finally:
        await conn.close()
