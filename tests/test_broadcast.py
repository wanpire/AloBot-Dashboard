"""Phase 5 task 6: reaching every customer at once, from the web."""

import datetime as dt

import httpx
import pytest
import respx
from sqlalchemy import select, text

from app.alobot.link import link
from app.alobot.seed import seed_alobot_copy
from app.db.session import async_session_maker
from app.models import Broadcast, BroadcastRecipient
from app.services import broadcast
from app.services.telegram import TelegramApi
from tests.alobot_seed import write_engine
from tests.conftest import ALOBOT_ADMIN_URL
from tests.web import make_operator

NOW = dt.datetime(2026, 9, 20, 12, 0, tzinfo=dt.timezone.utc)
TOKEN = "123456:TEST-TOKEN"
URL = f"https://api.telegram.org/bot{TOKEN}/sendMessage"


@pytest.fixture
async def seeded():
    await seed_alobot_copy(ALOBOT_ADMIN_URL, customers=20, seed=11)
    await link.connect()
    return await make_operator(email="bc@x.io", role="ADMIN")


async def test_reach_counts_each_audience_against_alobots_customers(seeded):
    async with async_session_maker() as db:
        counts = {a["id"]: await broadcast.reach(db, a["id"]) for a in broadcast.AUDIENCES}
    async with write_engine.connect() as conn:
        everyone = (await conn.execute(text("SELECT count(*) FROM bot_users WHERE NOT is_blocked"))).scalar_one()
        buyers = (await conn.execute(text("SELECT count(DISTINCT b.telegram_id) FROM bot_users b JOIN payments p ON p.telegram_id = b.telegram_id AND p.status='approved' WHERE NOT b.is_blocked"))).scalar_one()
    assert counts["all"] == everyone and counts["buyers"] == buyers
    assert counts["never_bought"] == everyone - buyers
    assert counts["all"] > counts["buyers"] > 0


async def test_blocked_customers_are_never_in_any_audience(seeded):
    async with write_engine.connect() as conn:
        blocked = [r.telegram_id for r in await conn.execute(text("SELECT telegram_id FROM bot_users WHERE is_blocked"))]
    assert blocked, "the seed must contain a blocked customer for this to prove anything"
    async with async_session_maker() as db:
        bid = await broadcast.create(db, seeded, batch_id="b1", audience="all", body="سلام", button_text=None, button_url=None, now=NOW)
        rows = (await db.execute(select(BroadcastRecipient.chat_id).where(BroadcastRecipient.broadcast_id == bid))).scalars().all()
    assert not set(blocked) & set(rows)


async def test_the_same_batch_id_submitted_twice_is_one_broadcast(seeded):
    async with async_session_maker() as db:
        first = await broadcast.create(db, seeded, batch_id="same", audience="all", body="یک", button_text=None, button_url=None, now=NOW)
        second = await broadcast.create(db, seeded, batch_id="same", audience="all", body="دو", button_text=None, button_url=None, now=NOW)
        rows = (await db.execute(select(Broadcast))).scalars().all()
    assert first == second and len(rows) == 1 and rows[0].body == "یک"


async def test_an_empty_audience_or_body_is_refused(seeded):
    async with async_session_maker() as db:
        with pytest.raises(broadcast.BroadcastError, match="متن"):
            await broadcast.create(db, seeded, batch_id="b", audience="all", body="   ", button_text=None, button_url=None, now=NOW)
        with pytest.raises(broadcast.BroadcastError):
            await broadcast.create(db, seeded, batch_id="b", audience="nope", body="x", button_text=None, button_url=None, now=NOW)


async def test_a_button_needs_both_a_label_and_an_http_url(seeded):
    async with async_session_maker() as db:
        with pytest.raises(broadcast.BroadcastError, match="دکمه"):
            await broadcast.create(db, seeded, batch_id="b", audience="all", body="x", button_text="بزن", button_url=None, now=NOW)
        with pytest.raises(broadcast.BroadcastError, match="لینک"):
            await broadcast.create(db, seeded, batch_id="b2", audience="all", body="x", button_text="بزن", button_url="javascript:x", now=NOW)


@respx.mock
async def test_drain_sends_each_recipient_once_and_records_progress(seeded):
    respx.post(URL).mock(return_value=httpx.Response(200, json={"ok": True, "result": {}}))
    async with async_session_maker() as db:
        bid = await broadcast.create(db, seeded, batch_id="b1", audience="all", body="سلام", button_text="سایت", button_url="https://example.com", now=NOW)
        total = (await broadcast.progress(db, bid))["total"]
        first = await broadcast.drain(db, TelegramApi(TOKEN), limit=5, now=NOW)
        mid = await broadcast.progress(db, bid)
        while (await broadcast.drain(db, TelegramApi(TOKEN), limit=50, now=NOW))["sent"]:
            pass
        done = await broadcast.progress(db, bid)
    assert first["sent"] == 5 and mid["sent"] == 5 and mid["status"] == "SENDING"
    assert done["sent"] == total and done["failed"] == 0 and done["status"] == "DONE"
    assert len(respx.calls) == total
    body = respx.calls[0].request.content.decode()
    assert "inline_keyboard" in body and "https://example.com" in body


@respx.mock
async def test_a_blocked_recipient_is_recorded_with_its_reason_and_never_retried(seeded):
    respx.post(URL).mock(return_value=httpx.Response(403, json={"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked by the user"}))
    async with async_session_maker() as db:
        bid = await broadcast.create(db, seeded, batch_id="b1", audience="buyers", body="x", button_text=None, button_url=None, now=NOW)
        await broadcast.drain(db, TelegramApi(TOKEN), limit=3, now=NOW)
        calls_after_first = len(respx.calls)
        await broadcast.drain(db, TelegramApi(TOKEN), limit=3, now=NOW)
        failures = await broadcast.failures(db, bid)
        prog = await broadcast.progress(db, bid)
    assert prog["failed"] == 6 and prog["sent"] == 0
    assert len(respx.calls) == calls_after_first + 3  # the next three, never the first three again
    assert "blocked" in failures[0].error


@respx.mock
async def test_a_rate_limit_pauses_the_whole_send_rather_than_burning_recipients(seeded):
    respx.post(URL).mock(return_value=httpx.Response(429, json={"ok": False, "error_code": 429, "parameters": {"retry_after": 30}}))
    async with async_session_maker() as db:
        bid = await broadcast.create(db, seeded, batch_id="b1", audience="all", body="x", button_text=None, button_url=None, now=NOW)
        result = await broadcast.drain(db, TelegramApi(TOKEN), limit=5, now=NOW)
        prog = await broadcast.progress(db, bid)
    assert result["rate_limited"] is True and result["sent"] == 0 and prog["failed"] == 0
    assert len(respx.calls) == 1  # it stopped at the first 429 instead of asking 5 times


async def test_the_broadcast_sweep_is_registered():
    from app.services.sweeps import registry

    assert "broadcast.drain" in registry.names()
