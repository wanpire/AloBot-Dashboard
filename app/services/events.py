"""Events that outlive the container.

`docker logs` disappears with every redeploy, which is exactly when the
history is needed. WARNING and above from the structured logger are copied
into `app_events` by a sink the sweep runner flushes, and code that has a
session in hand can `record()` directly. Rows are only ever removed by the
retention prune - there is no delete button, because the person most
motivated to press it is the one whose mistake the row records.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
from collections import deque
from typing import Any

from sqlalchemy import delete, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import current_request_id, redact
from app.models import AppEvent

SINK_LEVEL = logging.WARNING
SINK_MAX_BUFFER = 2000


async def record(
    session: AsyncSession,
    level: str,
    event: str,
    *,
    fields: dict[str, Any] | None = None,
    err: str | None = None,
    service: str = "dashboard",
    request_id: str | None = None,
) -> AppEvent:
    row = AppEvent(
        level=level,
        event=event,
        service=service,
        request_id=request_id or current_request_id(),
        fields=redact(fields) if fields else None,
        err=err,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def list_events(
    session: AsyncSession,
    *,
    level: str | None = None,
    q: str | None = None,
    since: dt.datetime | None = None,
    limit: int = 200,
) -> list[AppEvent]:
    stmt = select(AppEvent).order_by(AppEvent.at.desc(), AppEvent.id.desc()).limit(limit)
    if level:
        stmt = stmt.where(AppEvent.level == level)
    if since:
        stmt = stmt.where(AppEvent.at >= since)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            or_(
                AppEvent.event.ilike(pattern),
                AppEvent.err.ilike(pattern),
                text("fields::text ILIKE :pattern").bindparams(pattern=pattern),
            )
        )
    return list((await session.execute(stmt)).scalars().all())


async def prune(session: AsyncSession, retention_days: int) -> int:
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=retention_days)
    result = await session.execute(delete(AppEvent).where(AppEvent.at < cutoff))
    await session.commit()
    return result.rowcount


class EventSinkHandler(logging.Handler):
    """Buffers WARNING+ records in memory; `flush_sink` writes them. The
    handler is synchronous and the database is not, so the buffer is the
    seam - bounded, so a flood of warnings costs memory, not correctness."""

    def __init__(self) -> None:
        super().__init__(level=SINK_LEVEL)
        self.buffer: deque[dict[str, Any]] = deque(maxlen=SINK_MAX_BUFFER)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        err = None
        if record.exc_info and record.exc_info[0] is not None:
            err = f"{record.exc_info[0].__name__}: {record.exc_info[1]}"
        item = {
            "level": record.levelname,
            "event": record.getMessage(),
            "fields": redact(getattr(record, "fields", None) or None),
            "err": err,
            "request_id": current_request_id(),
            "at": dt.datetime.fromtimestamp(record.created, tz=dt.timezone.utc),
        }
        with self._lock:
            self.buffer.append(item)

    def drain(self) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self.buffer)
            self.buffer.clear()
        return items


_sink: EventSinkHandler | None = None


def install_event_sink() -> EventSinkHandler:
    global _sink
    root = logging.getLogger()
    if _sink is not None:
        root.removeHandler(_sink)
    _sink = EventSinkHandler()
    root.addHandler(_sink)
    return _sink


async def flush_sink(session: AsyncSession, service: str = "dashboard") -> int:
    if _sink is None:
        return 0
    items = _sink.drain()
    if not items:
        return 0
    for item in items:
        session.add(
            AppEvent(
                at=item["at"],
                level=item["level"],
                event=item["event"],
                service=service,
                request_id=item["request_id"],
                fields=item["fields"],
                err=item["err"],
            )
        )
    await session.commit()
    return len(items)
