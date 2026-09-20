"""The loop that does things while nobody is looking.

Every sweep runs every cycle (~25 s); what decides whether something is due
is a threshold the sweep reads, not a schedule. A sweep that raises is logged
and recorded, and the cycle carries on with the next one - one broken sweep
must not stop the others. The heartbeat file is touched only after a
complete cycle, so "alive but not sweeping" shows up in /health as stale.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

log = get_logger(__name__)

Sweep = Callable[[AsyncSession], Awaitable[Any]]
HEARTBEAT_STALE_SECONDS = 90


class SweepRegistry:
    def __init__(self) -> None:
        self._sweeps: list[tuple[str, Sweep]] = []

    def register(self, name: str, fn: Sweep) -> None:
        self._sweeps.append((name, fn))

    def names(self) -> list[str]:
        return [n for n, _ in self._sweeps]

    def __iter__(self):
        return iter(self._sweeps)


registry = SweepRegistry()


def touch_heartbeat(path: str) -> None:
    with open(path, "a"):
        pass
    os.utime(path, None)


def heartbeat_age(path: str) -> float | None:
    try:
        return max(0.0, time.time() - os.stat(path).st_mtime)
    except FileNotFoundError:
        return None


async def run_cycle(reg: SweepRegistry, heartbeat_path: str, session_maker=None) -> dict[str, Any]:
    if session_maker is None:
        from app.db.session import async_session_maker

        session_maker = async_session_maker
    results: dict[str, Any] = {}
    for name, fn in reg:
        try:
            async with session_maker() as session:
                results[name] = await fn(session)
        except Exception as exc:  # noqa: BLE001 - one sweep must not stop the rest
            results[name] = exc
            log.exception("sweep.failed", sweep=name)
    touch_heartbeat(heartbeat_path)
    return results


async def run_forever(reg: SweepRegistry, heartbeat_path: str, interval_seconds: float) -> None:
    log.info("sweeps.started", sweeps=reg.names(), interval_seconds=interval_seconds)
    while True:
        started = time.monotonic()
        try:
            await run_cycle(reg, heartbeat_path)
        except Exception:  # noqa: BLE001
            log.exception("sweeps.cycle_failed")
        await asyncio.sleep(max(1.0, interval_seconds - (time.monotonic() - started)))


# ── Phase 1 sweeps ─────────────────────────────────────────────────────────


async def _events_flush(session: AsyncSession) -> int:
    from app.services.events import flush_sink

    return await flush_sink(session)


async def _events_prune(session: AsyncSession) -> int:
    from app.services import events, settings

    days = await settings.get(session, "events", "retention_days")
    return await events.prune(session, retention_days=int(days))


async def _sessions_prune(session: AsyncSession) -> int:
    """Rows a session can never come back from: past their absolute expiry,
    or revoked more than a month ago. Kept a month so «نشست‌های من» can show
    a recent revocation."""
    import datetime as dt

    from sqlalchemy import delete, or_

    from app.models import OperatorSession

    now = dt.datetime.now(dt.timezone.utc)
    result = await session.execute(
        delete(OperatorSession).where(
            or_(OperatorSession.expires_at < now, OperatorSession.revoked_at < now - dt.timedelta(days=30))
        )
    )
    await session.commit()
    return result.rowcount


async def _claims_mirror(session: AsyncSession) -> dict:
    from app.services.claims import mirror_claims

    return await mirror_claims(session, now=__import__("datetime").datetime.now(__import__("datetime").timezone.utc))


async def _claims_settle(session: AsyncSession) -> dict:
    from app.services.settle import settle

    return await settle(session)


async def _outbox_flush(session: AsyncSession) -> dict:
    from app.core.config import get_settings
    from app.services.alerts import alert
    from app.services.outbox import flush
    from app.services.telegram import TelegramApi

    token = get_settings().telegram_bot_token
    result = await flush(session, TelegramApi(token) if token else None)
    if result.get("dead"):
        await alert(session, "notify.dead", f"{result['dead']} پیام تلگرام برای همیشه ارسال نشد؛ صفحهٔ رویدادها را ببینید.")
    return result


registry.register("claims.mirror", _claims_mirror)
registry.register("claims.settle", _claims_settle)
registry.register("outbox.flush", _outbox_flush)
registry.register("events.flush", _events_flush)
registry.register("events.prune", _events_prune)
registry.register("sessions.prune", _sessions_prune)
