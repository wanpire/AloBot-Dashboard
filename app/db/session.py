"""Two engines, deliberately separate.

`engine` is this project's own database and is always present.

`alobot_engine` is AloBot's database through a read-only role, and it
is optional: `None` when `ALOBOT_DATABASE_URL` is blank. Nothing in this
project may write through it until the final integration phase, and
even then only the tables that phase enables. Keeping the two engines
apart (rather than one connection with two schemas) is what makes
"this project cannot touch AloBot's data" a property of the wiring
rather than a rule someone has to remember.

Pool sizes are small on purpose: the dashboard serves a handful of
operators, and AloBot's Postgres has a 250-connection ceiling of which
the bot itself may use 150. This project must stay well inside the
remaining headroom.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

settings = get_settings()

engine: AsyncEngine = create_async_engine(
    settings.database_url, pool_pre_ping=True, pool_size=5, max_overflow=10, pool_timeout=5
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
alobot_session_maker = (
    async_sessionmaker(alobot_engine, class_=AsyncSession, expire_on_commit=False)
    if alobot_engine is not None
    else None
)
