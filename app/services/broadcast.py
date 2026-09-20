"""Reaching every customer at once.

The audience is AloBot's customers (read-only); the message, the recipient
snapshot and the outcome are ours. Sending is a sweep, not a request: eleven
thousand Telegram calls do not fit in one form submission, and a process that
dies halfway through an inline send has no record of who already heard.

There is no retry. A failed send is recorded with its reason and never
offered again, because the ordinary cause is a customer who blocked the bot
and the alternative risks the duplicate the snapshot exists to prevent.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.alobot.link import link
from app.core.logging import get_logger
from app.models import Broadcast, BroadcastRecipient
from app.services import pace
from app.services.audit import audit_row
from app.services.telegram import TelegramApi

log = get_logger(__name__)

AUDIENCES: tuple[dict[str, str], ...] = (
    {"id": "all", "label": "همهٔ مشتریان", "hint": "هر کسی که تا امروز ربات را استارت کرده و مسدود نیست."},
    {"id": "buyers", "label": "خریداران", "hint": "کسانی که دست‌کم یک پرداخت تاییدشده دارند."},
    {"id": "never_bought", "label": "استارت‌کرده‌ها بدون خرید", "hint": "ربات را استارت کرده‌اند و هیچ خریدی نکرده‌اند."},
    {"id": "active_service", "label": "دارندگان سرویس فعال", "hint": "کسانی که اکانتی با تاریخ انقضای آینده دارند."},
)
_AUDIENCE_SQL = {
    "all": "SELECT telegram_id FROM bot_users WHERE NOT is_blocked",
    "buyers": "SELECT b.telegram_id FROM bot_users b WHERE NOT b.is_blocked AND EXISTS (SELECT 1 FROM payments p WHERE p.telegram_id = b.telegram_id AND p.status = 'approved')",
    "never_bought": "SELECT b.telegram_id FROM bot_users b WHERE NOT b.is_blocked AND NOT EXISTS (SELECT 1 FROM payments p WHERE p.telegram_id = b.telegram_id AND p.status = 'approved')",
    "active_service": "SELECT DISTINCT b.telegram_id FROM bot_users b JOIN vpn_users v ON v.telegram_id = b.telegram_id WHERE NOT b.is_blocked AND v.expires_at > now()",
}


class BroadcastError(ValueError):
    pass


async def reach(db: AsyncSession, audience: str) -> int:
    if audience not in _AUDIENCE_SQL:
        raise BroadcastError("مخاطب نامعتبر است.")
    if not link.available:
        raise BroadcastError("پایگاه‌دادهٔ آلوبات متصل نیست؛ فهرست مخاطبان از آن‌جا خوانده می‌شود.")
    async with link.session() as read:
        return (await read.execute(text(f"SELECT count(*) FROM ({_AUDIENCE_SQL[audience]}) AS a"))).scalar_one()


def _check_button(button_text: str | None, button_url: str | None) -> None:
    if not button_text and not button_url:
        return
    if not button_text or not button_url:
        raise BroadcastError("دکمه هم عنوان می‌خواهد هم لینک.")
    if urlsplit(button_url).scheme not in ("http", "https") or not urlsplit(button_url).netloc:
        raise BroadcastError("لینک دکمه باید با http:// یا https:// شروع شود.")


async def create(db: AsyncSession, actor, *, batch_id: str, audience: str, body: str, button_text: str | None, button_url: str | None, now: dt.datetime | None = None) -> int:
    """Snapshot the audience and queue it. The same `batch_id` twice returns
    the first broadcast untouched."""
    now = now or dt.datetime.now(dt.timezone.utc)
    existing = (await db.execute(select(Broadcast).where(Broadcast.batch_id == batch_id))).scalar_one_or_none()
    if existing is not None:
        return existing.id
    if not body.strip():
        raise BroadcastError("متن پیام خالی است.")
    if audience not in _AUDIENCE_SQL:
        raise BroadcastError("مخاطب نامعتبر است.")
    _check_button(button_text, button_url)
    if not link.available:
        raise BroadcastError("پایگاه‌دادهٔ آلوبات متصل نیست؛ فهرست مخاطبان از آن‌جا خوانده می‌شود.")
    async with link.session() as read:
        chat_ids = [r.telegram_id for r in (await read.execute(text(_AUDIENCE_SQL[audience]))).all()]
    if not chat_ids:
        raise BroadcastError("این مخاطب هیچ عضوی ندارد.")

    row = Broadcast(
        batch_id=batch_id, audience=audience, body=body.strip(), button_text=(button_text or None),
        button_url=(button_url or None), total=len(chat_ids), created_by=actor.email, created_at=now,
    )
    db.add(row)
    await db.flush()
    db.add_all([BroadcastRecipient(broadcast_id=row.id, chat_id=chat_id) for chat_id in chat_ids])
    db.add(audit_row(action="broadcast.create", entity_type="broadcast", entity_id=batch_id, actor_role=actor.role, actor_operator_id=actor.id, after={"audience": audience, "total": len(chat_ids), "has_button": bool(button_text)}))
    await db.commit()
    log.info("broadcast.created", broadcast_id=row.id, audience=audience, total=len(chat_ids))
    return row.id


def _markup(row: Broadcast) -> dict[str, Any] | None:
    if not row.button_text or not row.button_url:
        return None
    return {"inline_keyboard": [[{"text": row.button_text, "url": row.button_url}]]}


async def drain(db: AsyncSession, api: TelegramApi | None, limit: int = 20, now: dt.datetime | None = None) -> dict[str, Any]:
    """Send the next batch. Stops at the first rate limit, leaving the rest
    PENDING - burning recipients against a ban helps nobody."""
    if api is None:
        return {"skipped": "no_bot_token", "sent": 0, "failed": 0, "rate_limited": False}
    now = now or dt.datetime.now(dt.timezone.utc)
    if pace.is_paused(now):
        return {"skipped": "paced", "sent": 0, "failed": 0, "rate_limited": True}
    row = (await db.execute(select(Broadcast).where(Broadcast.status.in_(("PENDING", "SENDING"))).order_by(Broadcast.id).limit(1))).scalar_one_or_none()
    if row is None:
        return {"sent": 0, "failed": 0, "rate_limited": False}
    claimed = (
        await db.execute(
            text(
                """
                WITH due AS MATERIALIZED (
                    SELECT id FROM broadcast_recipients
                     WHERE broadcast_id = :bid AND status = 'PENDING'
                     ORDER BY id LIMIT :limit FOR UPDATE SKIP LOCKED
                )
                SELECT r.id, r.chat_id FROM broadcast_recipients r JOIN due ON due.id = r.id
                """
            ),
            {"bid": row.id, "limit": limit},
        )
    ).all()
    if not claimed:
        await _finish_if_done(db, row.id, now)
        return {"sent": 0, "failed": 0, "rate_limited": False}
    if row.status == "PENDING":
        row.status = "SENDING"
        await db.commit()

    markup = _markup(row)
    sent = failed = 0
    rate_limited = False
    for recipient in claimed:
        result = await api.send_message(recipient.chat_id, row.body, "HTML", markup)
        if result.retry_after:
            pace.note_rate_limit(result.retry_after, now)
            rate_limited = True
            log.warning("broadcast.rate_limited", broadcast_id=row.id, retry_after=result.retry_after)
            break
        if result.ok:
            await db.execute(text("UPDATE broadcast_recipients SET status='SENT', sent_at=:now WHERE id=:id"), {"now": now, "id": recipient.id})
            sent += 1
        else:
            await db.execute(text("UPDATE broadcast_recipients SET status='FAILED', error=:err, sent_at=:now WHERE id=:id"), {"err": f"{result.error_code}: {result.description}"[:500], "now": now, "id": recipient.id})
            failed += 1
    await db.commit()
    await _finish_if_done(db, row.id, now)
    return {"broadcast_id": row.id, "sent": sent, "failed": failed, "rate_limited": rate_limited}


async def _finish_if_done(db: AsyncSession, broadcast_id: int, now: dt.datetime) -> None:
    pending = (await db.execute(select(func.count()).select_from(BroadcastRecipient).where(BroadcastRecipient.broadcast_id == broadcast_id, BroadcastRecipient.status == "PENDING"))).scalar_one()
    if pending == 0:
        await db.execute(text("UPDATE broadcasts SET status='DONE', finished_at=:now WHERE id=:id AND status <> 'DONE'"), {"now": now, "id": broadcast_id})
        await db.commit()


async def progress(db: AsyncSession, broadcast_id: int) -> dict[str, Any]:
    row = await db.get(Broadcast, broadcast_id)
    if row is None:
        raise BroadcastError("چنین ارسالی نیست.")
    counts = dict(
        (r.status, r.n)
        for r in (await db.execute(select(BroadcastRecipient.status, func.count().label("n")).where(BroadcastRecipient.broadcast_id == broadcast_id).group_by(BroadcastRecipient.status))).all()
    )
    return {
        "id": row.id, "audience": row.audience, "body": row.body, "status": row.status, "total": row.total,
        "sent": counts.get("SENT", 0), "failed": counts.get("FAILED", 0), "pending": counts.get("PENDING", 0),
        "created_by": row.created_by, "created_at": row.created_at, "finished_at": row.finished_at,
    }


async def recent(db: AsyncSession, limit: int = 20) -> list[dict[str, Any]]:
    rows = (await db.execute(select(Broadcast).order_by(Broadcast.id.desc()).limit(limit))).scalars().all()
    return [await progress(db, r.id) for r in rows]


async def failures(db: AsyncSession, broadcast_id: int, limit: int = 200) -> list:
    return list((await db.execute(select(BroadcastRecipient).where(BroadcastRecipient.broadcast_id == broadcast_id, BroadcastRecipient.status == "FAILED").order_by(BroadcastRecipient.id).limit(limit))).scalars().all())
