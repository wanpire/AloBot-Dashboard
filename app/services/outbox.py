"""The outbox. A message is a row before it is a request, so a Telegram
hiccup delays it instead of losing it, and a retried sweep or a double
click lands on the same row instead of sending twice."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import BotNotification
from app.services.telegram import TelegramApi

log = get_logger(__name__)
MAX_ATTEMPTS = 8
LEASE = dt.timedelta(minutes=2)
MAX_BACKOFF = dt.timedelta(hours=1)


async def enqueue(session: AsyncSession, *, dedupe_key: str, chat_id: int, text_: str | None = None, text: str | None = None, parse_mode: str | None = "HTML", reply_markup: dict | None = None, commit: bool = True) -> bool:
    payload: dict[str, Any] = {"text": text if text is not None else text_, "parse_mode": parse_mode}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    stmt = insert(BotNotification).values(dedupe_key=dedupe_key, chat_id=chat_id, payload=payload).on_conflict_do_nothing(index_elements=["dedupe_key"])
    result = await session.execute(stmt)
    if commit:
        await session.commit()
    return result.rowcount == 1


def _backoff(attempts: int) -> dt.timedelta:
    return min(dt.timedelta(seconds=60 * (2 ** (attempts - 1))), MAX_BACKOFF)


async def flush(session: AsyncSession, api: TelegramApi | None, now: dt.datetime | None = None, limit: int = 20) -> dict[str, Any]:
    if api is None:
        return {"skipped": "no_bot_token"}
    now = now or dt.datetime.now(dt.timezone.utc)
    # Claim a batch under a lease: `AS MATERIALIZED` makes LIMIT a real cap,
    # SKIP LOCKED keeps two sweeps off the same row.
    rows = (
        await session.execute(
            text(
                """
                WITH due AS MATERIALIZED (
                    SELECT id FROM bot_notifications
                     WHERE status = 'PENDING' AND next_attempt_at <= :now
                     ORDER BY id LIMIT :limit FOR UPDATE SKIP LOCKED
                )
                UPDATE bot_notifications b SET next_attempt_at = :lease
                  FROM due WHERE b.id = due.id
                RETURNING b.id, b.chat_id, b.payload, b.attempt_count
                """
            ),
            {"now": now, "limit": limit, "lease": now + LEASE},
        )
    ).all()
    await session.commit()
    counts = {"sent": 0, "failed": 0, "dead": 0, "rate_limited": 0}
    for row in rows:
        payload = row.payload or {}
        result = await api.send_message(row.chat_id, payload.get("text", ""), payload.get("parse_mode"), payload.get("reply_markup"))
        if result.ok:
            await session.execute(text("UPDATE bot_notifications SET status = 'SENT', sent_at = :now, attempt_count = attempt_count + 1, last_error = NULL WHERE id = :id"), {"now": now, "id": row.id})
            counts["sent"] += 1
        elif result.retry_after:
            await session.execute(text("UPDATE bot_notifications SET next_attempt_at = :at WHERE id = :id"), {"at": now + dt.timedelta(seconds=int(result.retry_after)), "id": row.id})
            counts["rate_limited"] += 1
            log.warning("notify.rate_limited", retry_after=result.retry_after)
        else:
            attempts = row.attempt_count + 1
            dead = result.permanent or attempts >= MAX_ATTEMPTS
            error = f"{result.error_code}: {result.description}"[:500]
            if dead:
                await session.execute(text("UPDATE bot_notifications SET status = 'DEAD', attempt_count = :n, last_error = :err WHERE id = :id"), {"n": attempts, "err": error, "id": row.id})
                counts["dead"] += 1
                log.warning("notify.dead", notification_id=row.id, chat_id=row.chat_id, error=error, permanent=result.permanent)
            else:
                await session.execute(text("UPDATE bot_notifications SET attempt_count = :n, last_error = :err, next_attempt_at = :at WHERE id = :id"), {"n": attempts, "err": error, "at": now + _backoff(attempts), "id": row.id})
                counts["failed"] += 1
        await session.commit()
    return counts
