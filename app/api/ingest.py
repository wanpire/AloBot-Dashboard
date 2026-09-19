"""`POST /api/v1/sms` - the only public surface of this project.

The relay phone posts JSON with the device token IN THE BODY (that is the
relay app's contract; there is no header). The body is capped in bytes
before it is parsed, every authentication failure is one generic 401, and
per-device and per-IP limits sit in front of the hashing. The handler must
answer fast: the relay retries on any non-2xx and a slow 200 is a retry
storm, so parsing stays cheap and matching happens later in a sweep.
"""

from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.ratelimit import FixedWindowLimiter
from app.services import ingest as ingest_service
from app.web.deps import get_db
from app.web.guards import client_ip

router = APIRouter()
log = get_logger(__name__)

device_limiter = FixedWindowLimiter(limit=600, window_seconds=60)
ip_limiter = FixedWindowLimiter(limit=120, window_seconds=60)

FIELD_ALIASES = {
    "apiKey": ("apiKey", "apikey", "api_key", "APIKey"),
    "deviceId": ("deviceId", "deviceid", "device_id"),
    "message": ("message", "Message", "body", "text", "content"),
    "sender": ("sender", "Sender", "from", "From"),
    "timestamp": ("timestamp", "Timestamp", "time", "date"),
    "checksum": ("checksum", "Checksum"),
}


def _pick(data: dict, name: str):
    for alias in FIELD_ALIASES[name]:
        if alias in data:
            return data[alias]
    return None


async def _read_capped(request: Request, cap: int) -> bytes | None:
    """Read at most `cap` bytes; None means the body was larger. Chunked
    bodies are counted as they stream, so a lying Content-Length does not
    get 64 KB into memory."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > cap:
        return None
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > cap:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/api/v1/sms")
async def receive_sms(request: Request, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    settings = get_settings()
    ip = client_ip(request)
    if ip is not None:
        ip_limiter.limit = settings.ingest_ip_rate_per_minute
        if not ip_limiter.hit(ip):
            return JSONResponse({"error": "rate_limited"}, status_code=429)
    raw = await _read_capped(request, settings.ingest_max_body_bytes)
    if raw is None:
        return JSONResponse({"error": "body_too_large"}, status_code=413)
    try:
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except (ValueError, UnicodeDecodeError):
        return JSONResponse({"error": "bad_json"}, status_code=400)
    api_key, device_code, message, sender = (_pick(data, k) for k in ("apiKey", "deviceId", "message", "sender"))
    if not all(isinstance(v, str) and v for v in (api_key, device_code, message, sender)):
        return JSONResponse({"error": "missing_fields"}, status_code=400)

    now = dt.datetime.now(dt.timezone.utc)
    device = await ingest_service.authenticate(db, device_code, api_key, now)
    if device is None:
        log.warning("ingest.unauthorized", device=device_code[:32])
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    device_limiter.limit = settings.ingest_device_rate_per_minute
    if not device_limiter.hit(device.code):
        log.warning("ingest.device_rate_limited", device=device.code)
        return JSONResponse({"error": "rate_limited"}, status_code=429)

    outcome = await ingest_service.ingest(
        db, device, sender=sender, message=message, timestamp_raw=_pick(data, "timestamp"),
        checksum=(str(_pick(data, "checksum"))[:64] if _pick(data, "checksum") else None), now=now,
    )
    return JSONResponse(
        {
            "ok": True,
            "duplicate": outcome.duplicate,
            "eventId": outcome.event_id,
            "classification": outcome.classification,
            "actionable": outcome.actionable,
            "transactionId": outcome.transaction_id,
        }
    )
