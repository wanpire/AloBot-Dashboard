"""Continuity mode: keep delivering when the SMS channel is down.

One settings row, `pay/continuity_mode`, holding {until, by, reason}. The read
enforces the expiry, so the mode turns itself off even if nothing else runs.
A claim opened while it is on is FULFILLED_UNRECONCILED - delivered on trust,
and kept in a queue that says the bank has not confirmed it yet - with a
24-hour matching window instead of five minutes."""

from __future__ import annotations

import datetime as dt

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Setting
from app.services.audit import audit_row

SCOPE, KEY = "pay", "continuity_mode"
MIN_DURATION = dt.timedelta(minutes=5)
MAX_DURATION = dt.timedelta(hours=6)


class ContinuityError(ValueError):
    pass


async def state(session: AsyncSession, now: dt.datetime) -> dict | None:
    row = await session.get(Setting, (SCOPE, KEY))
    if row is None or not row.value:
        return None
    until = dt.datetime.fromisoformat(row.value["until"])
    if until <= now:
        return None
    return {"until": until, "by": row.value.get("by"), "reason": row.value.get("reason"), "since": row.value.get("since")}


async def is_active(session: AsyncSession, now: dt.datetime) -> bool:
    return await state(session, now) is not None


async def activate(session: AsyncSession, actor, *, minutes: int, reason: str, now: dt.datetime) -> dict:
    duration = dt.timedelta(minutes=minutes)
    if duration < MIN_DURATION or duration > MAX_DURATION:
        raise ContinuityError("مدت حالت تداوم باید بین ۵ دقیقه و ۶ ساعت باشد.")
    if not reason.strip():
        raise ContinuityError("دلیل لازم است: این حالت یعنی فروشگاه بدون مدرک بانکی تحویل می‌دهد.")
    value = {"until": (now + duration).isoformat(), "by": actor.email, "reason": reason.strip(), "since": now.isoformat()}
    row = await session.get(Setting, (SCOPE, KEY))
    before = row.value if row else None
    if row is None:
        session.add(Setting(scope=SCOPE, key=KEY, value=value, updated_by=actor.email))
    else:
        row.value, row.updated_by = value, actor.email
    session.add(audit_row(action="continuity.activate", entity_type="setting", entity_id=f"{SCOPE}/{KEY}", actor_role=actor.role, actor_operator_id=actor.id, before=before, after=value))
    await session.commit()
    return value


async def deactivate(session: AsyncSession, actor, now: dt.datetime) -> None:
    row = await session.get(Setting, (SCOPE, KEY))
    before = row.value if row else None
    if row is not None:
        row.value, row.updated_by = {}, actor.email
    session.add(audit_row(action="continuity.deactivate", entity_type="setting", entity_id=f"{SCOPE}/{KEY}", actor_role=actor.role, actor_operator_id=actor.id, before=before, after=None))
    await session.commit()
