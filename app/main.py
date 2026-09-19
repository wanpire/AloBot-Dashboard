"""FastAPI entrypoint.

Phase 0: the process boots only when configuration is complete
(`ENV_NAME`, `DATABASE_URL`, `SESSION_SECRET`), and `/health` reports
what it can actually reach. Everything else arrives phase by phase -
see `docs/PLAN.md`.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import alobot_engine, engine

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    logger.info("alobot-dashboard starting env=%s version=%s", settings.env_name.value, settings.app_version)
    if alobot_engine is None:
        logger.warning("ALOBOT_DATABASE_URL is blank - AloBot-backed pages are disabled")
    yield
    await engine.dispose()
    if alobot_engine is not None:
        await alobot_engine.dispose()


app = FastAPI(title="AloBot Dashboard", lifespan=lifespan, docs_url=None, redoc_url=None)


async def _ping(target) -> str:
    try:
        async with target.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return "ok"
    except Exception as exc:  # noqa: BLE001 - health must report, not raise
        return f"error: {type(exc).__name__}"


@app.get("/health")
async def health() -> JSONResponse:
    settings = get_settings()
    own = await _ping(engine)
    alobot = "unconfigured" if alobot_engine is None else await _ping(alobot_engine)
    body = {
        "ok": own == "ok",
        "env": settings.env_name.value,
        "version": settings.app_version,
        "db": own,
        "alobot_db": alobot,
        "alobot_db_writes_enabled": settings.alobot_db_writes_enabled,
    }
    return JSONResponse(body, status_code=200 if body["ok"] else 503)
