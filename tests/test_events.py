"""Phase 1 task 9: events that outlive the container, and the page that shows them."""

import datetime as dt
import io
import json

from sqlalchemy import select

from app.core.logging import configure_logging, get_logger
from app.db.session import async_session_maker
from app.models import AppEvent
from app.services import events
from tests.web import logged_in


async def test_record_and_list_with_level_and_text_filters(session):
    await events.record(session, "WARNING", "sms.unparsed", fields={"sender": "Bank"})
    await events.record(session, "ERROR", "sweep.failed", fields={"sweep": "x"}, err="boom")
    await events.record(session, "INFO", "login.ok", fields={})
    assert [e.event for e in await events.list_events(session)] == ["login.ok", "sweep.failed", "sms.unparsed"]
    assert [e.event for e in await events.list_events(session, level="ERROR")] == ["sweep.failed"]
    assert [e.event for e in await events.list_events(session, q="Bank")] == ["sms.unparsed"]


async def test_prune_removes_only_rows_older_than_the_retention(session):
    old = await events.record(session, "INFO", "old", fields={})
    old.at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=31)
    await session.commit()
    await events.record(session, "INFO", "new", fields={})
    assert await events.prune(session, retention_days=30) == 1
    assert [e.event for e in await events.list_events(session)] == ["new"]


async def test_warnings_from_the_logger_reach_app_events_after_a_flush(session):
    stream = io.StringIO()
    configure_logging(level="INFO", stream=stream, service="test")
    sink = events.install_event_sink()
    get_logger("x").warning("notify.dead", chat_id=5, token="tok-SECRET")
    get_logger("x").info("quiet.info")
    assert await events.flush_sink(session) == 1
    row = (await session.execute(select(AppEvent))).scalar_one()
    assert row.event == "notify.dead" and row.level == "WARNING"
    assert row.fields == {"chat_id": 5, "token": "[redacted]"}
    assert "SECRET" not in json.dumps(row.fields)
    sink.close()


async def test_events_page_is_admin_only_and_carries_copyable_json():
    async with async_session_maker() as s:
        await events.record(s, "ERROR", "sweep.failed", fields={"sweep": "prune"}, err="ValueError: boom")
    reviewer = await logged_in("REVIEWER")
    async with reviewer:
        assert (await reviewer.get("/events")).status_code == 403
    admin = await logged_in("ADMIN")
    async with admin:
        r = await admin.get("/events?level=ERROR")
    assert r.status_code == 200
    assert "sweep.failed" in r.text
    assert "data-json=" in r.text and "&#34;sweep&#34;: &#34;prune&#34;" in r.text


async def test_events_page_has_no_delete_control():
    admin = await logged_in("ADMIN")
    async with admin:
        r = await admin.get("/events")
    assert "حذف" not in r.text
