"""FastAPI entrypoint.

The process boots only when configuration is complete (`ENV_NAME`,
`DATABASE_URL`, `SESSION_SECRET`), and `/health` reports what it can
actually reach. Everything else arrives phase by phase - see `docs/PLAN.md`.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import alobot_engine, engine
from app.web.deps import Unauthenticated
from app.web.guards import OriginGuardMiddleware, RequestIdMiddleware
from app.web.routes import access as access_routes
from app.web.routes import auth as auth_routes
from app.web.routes import pages as page_routes
from app.web.routes import settings as settings_routes
from app.web.templating import render

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(level=settings.log_level, service="dashboard")
    log.info("boot", env=settings.env_name.value, version=settings.app_version)
    if alobot_engine is None:
        log.warning("boot.alobot_db_unconfigured", detail="AloBot-backed pages are disabled")
    if not settings.trusted_proxy_ip_header:
        log.warning("boot.no_trusted_proxy_header", detail="per-IP login rate limit is OFF")
    yield
    await engine.dispose()
    if alobot_engine is not None:
        await alobot_engine.dispose()


app = FastAPI(title="AloBot Dashboard", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(OriginGuardMiddleware)
app.add_middleware(RequestIdMiddleware)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "web" / "static")), name="static")
app.include_router(auth_routes.router)
app.include_router(settings_routes.router)
app.include_router(access_routes.router)
app.include_router(page_routes.router)  # placeholders last: specific pages above win


def _wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "") or not request.url.path.startswith("/api/")


@app.exception_handler(Unauthenticated)
async def _unauthenticated(request: Request, exc: Unauthenticated):
    if request.headers.get("hx-request"):
        return JSONResponse({"error": "unauthenticated"}, status_code=401, headers={"HX-Redirect": "/login"})
    if _wants_html(request):
        return RedirectResponse("/login", status_code=303)
    return JSONResponse({"error": "unauthenticated"}, status_code=401)


@app.exception_handler(HTTPException)
async def _http_error(request: Request, exc: HTTPException):
    if exc.status_code == 403 and _wants_html(request) and not request.headers.get("hx-request"):
        return render(request, "error.html", status_code=403, title="دسترسی ندارید", detail=exc.detail)
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code, headers=exc.headers)


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
