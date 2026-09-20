"""Phase 4 task 9: every Telegram message goes through the outbox, and the
outbox knows the difference between "Telegram is unwell" and "this person
blocked us"."""

import datetime as dt

import httpx
import pytest
import respx
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import async_session_maker
from app.models import BotNotification
from app.services import outbox
from app.services.alerts import alert
from app.services.telegram import TelegramApi

NOW = dt.datetime(2026, 9, 20, 12, 0, tzinfo=dt.timezone.utc)
TOKEN = "123456:TEST-TOKEN"
URL = f"https://api.telegram.org/bot{TOKEN}/sendMessage"


@pytest.fixture
def api():
    return TelegramApi(TOKEN)


async def _pending():
    async with async_session_maker() as s:
        return (await s.execute(select(BotNotification).order_by(BotNotification.id))).scalars().all()


async def test_enqueue_is_idempotent_on_the_dedupe_key():
    async with async_session_maker() as s:
        assert await outbox.enqueue(s, dedupe_key="k1", chat_id=1, text="a") is True
        assert await outbox.enqueue(s, dedupe_key="k1", chat_id=1, text="b") is False
    rows = await _pending()
    assert len(rows) == 1 and rows[0].payload["text"] == "a" and rows[0].status == "PENDING"


@respx.mock
async def test_flush_sends_and_marks_sent(api):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 5}}))
    async with async_session_maker() as s:
        await outbox.enqueue(s, dedupe_key="k1", chat_id=42, text="سلام", parse_mode="HTML")
        result = await outbox.flush(s, api, now=NOW)
    rows = await _pending()
    assert result["sent"] == 1 and rows[0].status == "SENT" and rows[0].sent_at is not None
    assert route.calls[0].request.content and b'"chat_id": 42' in route.calls[0].request.content or b'"chat_id":42' in route.calls[0].request.content


@respx.mock
async def test_blocked_bot_is_dead_immediately_and_the_key_stays_taken(api):
    respx.post(URL).mock(return_value=httpx.Response(403, json={"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked by the user"}))
    async with async_session_maker() as s:
        await outbox.enqueue(s, dedupe_key="k1", chat_id=42, text="x")
        await outbox.flush(s, api, now=NOW)
        assert await outbox.enqueue(s, dedupe_key="k1", chat_id=42, text="x") is False
    rows = await _pending()
    assert rows[0].status == "DEAD" and rows[0].attempt_count == 1 and "blocked" in rows[0].last_error


@respx.mock
async def test_server_error_backs_off_exponentially_and_dies_after_eight(api):
    respx.post(URL).mock(return_value=httpx.Response(502, text="bad gateway"))
    async with async_session_maker() as s:
        await outbox.enqueue(s, dedupe_key="k1", chat_id=42, text="x")
        t = NOW
        delays = []
        for attempt in range(8):
            await outbox.flush(s, api, now=t)
            row = (await s.execute(select(BotNotification))).scalar_one()
            await s.refresh(row)
            delays.append((row.next_attempt_at - t).total_seconds())
            t = row.next_attempt_at
    assert row.status == "DEAD" and row.attempt_count == 8
    assert delays[:4] == [60, 120, 240, 480] and max(delays) <= 3600


@respx.mock
async def test_rate_limit_honours_retry_after_without_charging_an_attempt(api):
    respx.post(URL).mock(return_value=httpx.Response(429, json={"ok": False, "error_code": 429, "parameters": {"retry_after": 17}}))
    async with async_session_maker() as s:
        await outbox.enqueue(s, dedupe_key="k1", chat_id=42, text="x")
        await outbox.flush(s, api, now=NOW)
        row = (await s.execute(select(BotNotification))).scalar_one()
        await s.refresh(row)
    assert row.status == "PENDING" and row.attempt_count == 0 and row.next_attempt_at == NOW + dt.timedelta(seconds=17)


@respx.mock
async def test_flush_batch_limit_is_a_real_cap_on_a_full_table(api):
    respx.post(URL).mock(return_value=httpx.Response(200, json={"ok": True, "result": {}}))
    async with async_session_maker() as s:
        for i in range(30):
            await outbox.enqueue(s, dedupe_key=f"k{i}", chat_id=i, text="x")
        result = await outbox.flush(s, api, now=NOW, limit=5)
    rows = await _pending()
    assert result["sent"] == 5 and sum(1 for r in rows if r.status == "SENT") == 5


async def test_flush_without_a_token_touches_nothing():
    async with async_session_maker() as s:
        await outbox.enqueue(s, dedupe_key="k1", chat_id=1, text="x")
        result = await outbox.flush(s, None, now=NOW)
    assert result == {"skipped": "no_bot_token"}
    assert (await _pending())[0].status == "PENDING"


async def test_alert_goes_to_the_operator_chat_once_per_hour_per_event():
    from app.services import settings as settings_service

    NOW = dt.datetime(2026, 9, 20, 12, 30, tzinfo=dt.timezone.utc)  # 16:00 Tehran, on the hour
    async with async_session_maker() as s:
        assert await alert(s, "notify.dead", "یک پیام مرد", now=NOW) is False  # no chat configured
        await settings_service.set_many(s, {("alerts", "operator_chat_id"): -100123}, actor_email="t", actor_role="ADMIN")
        assert await alert(s, "notify.dead", "یک پیام مرد", now=NOW) is True
        assert await alert(s, "notify.dead", "یک پیام دیگر مرد", now=NOW + dt.timedelta(minutes=30)) is False
        assert await alert(s, "notify.dead", "ساعت بعد", now=NOW + dt.timedelta(hours=1)) is True
    rows = await _pending()
    assert [r.chat_id for r in rows] == [-100123, -100123]
