"""Phase 2 tasks 2-3: the read-only window, and the check that it fits."""

import pytest
from sqlalchemy import MetaData, text
from sqlalchemy.exc import DBAPIError

from app.alobot import compat
from app.alobot.link import AloBotLink, link


async def test_link_connects_reflects_every_required_table_and_reports_no_problems():
    await link.connect()
    assert link.available, link.problems
    for table in compat.REQUIRED_COLUMNS:
        assert table in link.tables, table


async def test_compat_names_the_missing_column():
    meta = MetaData()
    async with link.engine.connect() as conn:
        await conn.run_sync(meta.reflect, only=["payments"])
    problems = compat.check(meta, required={"payments": frozenset({"id", "no_such_column"}), "ghost_table": frozenset({"id"})})
    assert any("payments.no_such_column" in p for p in problems)
    assert any("ghost_table" in p for p in problems)


async def test_the_link_cannot_write_even_if_code_tries():
    await link.connect()
    async with link.session() as s:
        with pytest.raises(DBAPIError, match="permission denied"):
            await s.execute(text("INSERT INTO bot_users (telegram_id) VALUES (1)"))


async def test_blank_url_means_unavailable_with_a_reason():
    empty = AloBotLink(engine=None)
    await empty.connect()
    assert not empty.available and "ALOBOT_DATABASE_URL" in empty.problems[0]
