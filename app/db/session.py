"""Three engines, deliberately separate.

`engine` is this project's own database and is always present.

`alobot_engine` is AloBot's database through a read-only role, and it
is optional: `None` when `ALOBOT_DATABASE_URL` is blank.

`alobot_write_engine` is the same database through a role scoped to the
few tables the dashboard may edit, and is `None` unless
`ALOBOT_WRITE_DATABASE_URL` is set - which it is not in production until
the integration phase. Nothing in this
project may write through it until the final integration phase, and
even then only the tables that phase enables. Keeping the two engines
apart (rather than one connection with two schemas) is what makes
"this project cannot touch AloBot's data" a property of the wiring
rather than a rule someone has to remember.

Pool sizes are small on purpose: the dashboard serves a handful of
operators, and AloBot's Postgres has a 250-connection ceiling of which
the bot itself may use 150. This project must stay well inside the
remaining headroom - and the arithmetic is checked here at import time
rather than left to a comment, because the sum is what matters and it is
easy to raise one pool without looking at the other.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

settings = get_settings()

# This project's OWN Postgres: the container is ours alone, so the AloBot
# budget further down does not apply. The size is a setting because the right
# number depends on the host; the ingest load test
# (tests/test_load_ingest.py) is how it is chosen, and what that test
# measured is that throughput is bounded by the two commits each message
# costs, not by the pool - past about fifteen connections a bigger pool buys
# nothing. What a burst past capacity must do is shed politely, which
# app/api/ingest.py does with a 503.
engine: AsyncEngine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=5,
)
async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

alobot_engine: AsyncEngine | None = (
    create_async_engine(
        settings.alobot_database_url,
        pool_pre_ping=True,
        pool_size=3,
        max_overflow=5,
        pool_timeout=5,
    )
    if settings.alobot_database_url
    else None
)
# The write engine exists only when a write URL is configured. Small pool:
# edits are rare and an operator is one person.
alobot_write_engine: AsyncEngine | None = (
    create_async_engine(
        settings.alobot_write_database_url,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=3,
        pool_timeout=5,
    )
    if settings.alobot_write_database_url
    else None
)

# AloBot's own numbers, pinned by tests/test_pool_budget.py against
# `vendor/alobot`: its compose starts Postgres with max_connections=250 and
# its own pool may take pool_size=60 + max_overflow=90 = 150. The remaining
# 100 is headroom for migrations, psql sessions and monitoring - not a pot
# for this project to spend. The dashboard claims a small fixed slice of it.
ALOBOT_MAX_CONNECTIONS = 250
ALOBOT_OWN_MAX_CONNECTIONS = 150
ALOBOT_CONNECTION_BUDGET = 20


def alobot_connections_at_worst() -> int:
    """Every connection this process can open against AloBot's Postgres."""
    total = 0
    for candidate in (alobot_engine, alobot_write_engine):
        if candidate is not None:
            pool = candidate.pool
            total += pool.size() + pool._max_overflow  # type: ignore[attr-defined]
    return total


def check_connection_budget() -> None:
    worst = alobot_connections_at_worst()
    if worst > ALOBOT_CONNECTION_BUDGET:
        raise RuntimeError(
            f"this project's AloBot pools can reach {worst} connections, over its "
            f"budget of {ALOBOT_CONNECTION_BUDGET}; AloBot's Postgres allows "
            f"{ALOBOT_MAX_CONNECTIONS} and its own bot may take {ALOBOT_OWN_MAX_CONNECTIONS}"
        )


check_connection_budget()

alobot_session_maker = (
    async_sessionmaker(alobot_engine, class_=AsyncSession, expire_on_commit=False)
    if alobot_engine is not None
    else None
)
