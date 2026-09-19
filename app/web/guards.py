"""Request-level guards: a request id on every line, and the origin check
that stands in for a CSRF token on every mutating request."""

from __future__ import annotations

import uuid
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import get_settings
from app.core.logging import bind_request_id

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        token = bind_request_id(request_id)
        try:
            response = await call_next(request)
        finally:
            bind_request_id(None, reset=token)
        response.headers["X-Request-Id"] = request_id
        return response


class OriginGuardMiddleware(BaseHTTPMiddleware):
    """A mutating request must come from a page we served: its Origin (or the
    Referer's origin) must match the Host the request arrived on, port
    included. A request with neither header is refused outside local/test."""

    async def dispatch(self, request: Request, call_next):
        if request.method in MUTATING and not request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if not origin and request.headers.get("referer"):
                parts = urlsplit(request.headers["referer"])
                origin = f"{parts.scheme}://{parts.netloc}"
            host = request.headers.get("host", "")
            if origin:
                if urlsplit(origin).netloc != host:
                    return JSONResponse({"error": "cross_origin_forbidden"}, status_code=403)
            elif not get_settings().is_relaxed_env:
                return JSONResponse({"error": "origin_required"}, status_code=403)
        return await call_next(request)


def client_ip(request: Request) -> str | None:
    """The address the trusted proxy wrote, or None when no header is
    configured - in which case callers skip per-IP limits entirely."""
    header = get_settings().trusted_proxy_ip_header
    if not header:
        return None
    value = request.headers.get(header)
    return value.strip() if value else None
