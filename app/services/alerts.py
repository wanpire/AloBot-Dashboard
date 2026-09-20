"""Alerts to the operators' chat, rate-limited by a UNIQUE key: one message
per event per Tehran hour, whatever restarts in between."""

from __future__ import annotations

import datetime as dt

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import outbox
from app.services import settings as settings_service
from app.web.format import TEHRAN


async def alert(session: AsyncSession, event: str, text: str, now: dt.datetime | None = None) -> bool:
    chat_id = await settings_service.get(session, "alerts", "operator_chat_id")
    if not chat_id:
        return False
    now = now or dt.datetime.now(dt.timezone.utc)
    hour = now.astimezone(TEHRAN).strftime("%Y%m%d%H")
    return await outbox.enqueue(session, dedupe_key=f"alert:{event}:{hour}", chat_id=int(chat_id), text=f"⚠️ {text}")
