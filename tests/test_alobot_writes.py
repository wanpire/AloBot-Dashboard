"""Phase 5 foundation: writing to AloBot's tables is gated twice - a URL with
a write-capable role AND the flag - and the role itself is scoped."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.alobot.link import link
from app.alobot.writes import WritesDisabled, writes
from app.core.config import get_settings


async def test_writes_are_available_only_with_both_a_write_url_and_the_flag(monkeypatch):
    assert writes.available is True
    monkeypatch.setattr(get_settings(), "alobot_db_writes_enabled", False)
    assert writes.available is False
    with pytest.raises(WritesDisabled, match="فقط‌خواندنی"):
        writes.session()


async def test_a_write_engine_without_the_flag_is_still_refused(monkeypatch):
    monkeypatch.setattr(get_settings(), "alobot_db_writes_enabled", False)
    with pytest.raises(WritesDisabled):
        writes.session()


async def test_the_write_role_may_edit_the_catalog():
    async with writes.session() as s:
        await s.execute(text("INSERT INTO service_locations (title, flag_emoji, is_active, sort_order) VALUES ('X', '🏳', true, 9)"))
        await s.commit()
        count = (await s.execute(text("SELECT count(*) FROM service_locations WHERE title = 'X'"))).scalar_one()
    assert count == 1


@pytest.mark.parametrize("table", ["payments", "vpn_users", "bot_users", "resellers", "groups"])
async def test_the_write_role_cannot_touch_anything_it_was_not_given(table):
    async with writes.session() as s:
        with pytest.raises(DBAPIError, match="permission denied"):
            await s.execute(text(f"DELETE FROM {table}"))


async def test_the_read_link_is_still_read_only():
    await link.connect()
    async with link.session() as s:
        with pytest.raises(DBAPIError, match="permission denied"):
            await s.execute(text("INSERT INTO service_locations (title, flag_emoji) VALUES ('Y', '🏳')"))
