"""Phase 7 item 1: blocking a customer from the dashboard.

This needs no change to AloBot at all, and the reason is worth stating.
`BlockedUserMiddleware` asks the database `is_blocked` on EVERY update before
any handler runs, and AloBot's own `block_user` does nothing but set that
flag. So the flag is the whole mechanism: set it here and AloBot enforces it
on the person's next tap.

The two rules that come with it are AloBot's, not ours, and the screen has to
respect them rather than pretend: admins and resellers are exempt in that
middleware, so blocking one does nothing at all, and a block closes the bot
door only - the person's VPN service keeps working until it expires.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.alobot.link import link
from app.alobot.seed import seed_alobot_copy
from app.alobot.writes import botusers as botusers_writes
from app.alobot.writes import writes
from app.db.session import async_session_maker
from app.models import AuditLog
from tests.conftest import ALOBOT_ADMIN_URL
from tests.web import make_operator


@pytest.fixture
async def seeded():
    counts = await seed_alobot_copy(ALOBOT_ADMIN_URL, customers=10, seed=11)
    await link.connect()
    return counts


async def _telegram_ids() -> list[int]:
    """Plain customers only: the seed makes some of them admins and resellers,
    and those are the ones AloBot exempts."""
    async with link.session() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT telegram_id FROM bot_users WHERE telegram_id NOT IN "
                    "(SELECT telegram_id FROM admin_users UNION SELECT telegram_id FROM resellers) ORDER BY id"
                )
            )
        ).all()
    return [r.telegram_id for r in rows]


async def _is_blocked(telegram_id: int) -> bool:
    async with link.session() as s:
        return bool((await s.execute(text("SELECT is_blocked FROM bot_users WHERE telegram_id=:t"), {"t": telegram_id})).scalar_one())


async def test_blocking_sets_the_flag_alobot_reads_before_every_handler(seeded):
    telegram_id = (await _telegram_ids())[0]
    actor = await make_operator()
    async with async_session_maker() as db:
        await botusers_writes.block(db, actor, telegram_id=telegram_id)
        rows = list((await db.execute(text("SELECT action, entity_id FROM audit_logs"))).all())
    assert await _is_blocked(telegram_id) is True
    assert (rows[0].action, rows[0].entity_id) == ("botuser.block", str(telegram_id))


async def test_blocking_someone_the_bot_never_saw_creates_the_row(seeded):
    """AloBot does the same: a fake-receipt sender may have no bot_users row
    at all, and must still be blockable."""
    actor = await make_operator()
    async with async_session_maker() as db:
        await botusers_writes.block(db, actor, telegram_id=999000111)
    assert await _is_blocked(999000111) is True


async def test_unblocking_clears_it_and_says_so_when_there_was_nothing_to_clear(seeded):
    telegram_id = (await _telegram_ids())[0]
    actor = await make_operator()
    async with async_session_maker() as db:
        await botusers_writes.block(db, actor, telegram_id=telegram_id)
        await botusers_writes.unblock(db, actor, telegram_id=telegram_id)
        assert await _is_blocked(telegram_id) is False
        with pytest.raises(botusers_writes.BotUserError, match="مسدود نیست"):
            await botusers_writes.unblock(db, actor, telegram_id=telegram_id)


async def test_an_admin_is_refused_because_alobots_middleware_exempts_admins(seeded):
    """Blocking an admin would set a flag that changes nothing. Saying so is
    the honest answer; doing it and reporting success is not."""
    async with link.session() as s:
        admin_id = (await s.execute(text("SELECT telegram_id FROM admin_users LIMIT 1"))).scalar_one()
    actor = await make_operator()
    async with async_session_maker() as db:
        with pytest.raises(botusers_writes.BotUserError, match="ادمین"):
            await botusers_writes.block(db, actor, telegram_id=admin_id)
    async with link.session() as s:
        still = (await s.execute(text("SELECT is_blocked FROM bot_users WHERE telegram_id=:t"), {"t": admin_id})).scalar_one_or_none()
    assert not still


async def test_a_reseller_is_refused_for_the_same_reason(seeded):
    async with link.session() as s:
        reseller_id = (await s.execute(text("SELECT telegram_id FROM resellers LIMIT 1"))).scalar_one()
    actor = await make_operator()
    async with async_session_maker() as db:
        with pytest.raises(botusers_writes.BotUserError, match="نماینده"):
            await botusers_writes.block(db, actor, telegram_id=reseller_id)


async def test_the_write_role_may_touch_the_block_flag_and_nothing_else_on_that_table(seeded):
    """The grant is per column. Blocking is the only thing this project may
    ever do to a customer's row."""
    telegram_id = (await _telegram_ids())[0]
    async with writes.session() as s:
        with pytest.raises(DBAPIError, match="permission denied"):
            await s.execute(text("UPDATE bot_users SET username='stolen' WHERE telegram_id=:t"), {"t": telegram_id})
    async with writes.session() as s:
        with pytest.raises(DBAPIError, match="permission denied"):
            await s.execute(text("DELETE FROM bot_users WHERE telegram_id=:t"), {"t": telegram_id})


async def test_blocking_is_refused_when_writes_are_off(seeded, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "alobot_db_writes_enabled", False)
    actor = await make_operator()
    async with async_session_maker() as db:
        with pytest.raises(Exception, match="فقط‌خواندنی"):
            await botusers_writes.block(db, actor, telegram_id=(await _telegram_ids())[0])


# ── the screen ─────────────────────────────────────────────────────────────


async def test_an_admin_can_block_and_unblock_from_the_customer_page(seeded):
    from tests.web import logged_in

    telegram_id = (await _telegram_ids())[0]
    c = await logged_in("ADMIN")
    async with c:
        page = await c.get(f"/customers/{telegram_id}")
        assert page.status_code == 200
        assert f"/customers/{telegram_id}/block" in page.text
        # What a block does, and what it does not, on the screen itself.
        assert "سرویس" in page.text

        blocked = await c.post(f"/customers/{telegram_id}/block", headers={"Origin": "http://test"})
        assert blocked.status_code == 303
        after = await c.get(f"/customers/{telegram_id}")
        assert "مسدود" in after.text and f"/customers/{telegram_id}/unblock" in after.text

        back = await c.post(f"/customers/{telegram_id}/unblock", headers={"Origin": "http://test"})
        assert back.status_code == 303
    assert await _is_blocked(telegram_id) is False


async def test_the_page_offers_no_block_control_to_a_role_that_may_not_use_it(seeded):
    from tests.web import logged_in

    telegram_id = (await _telegram_ids())[0]
    for role in ("REVIEWER", "READ_ONLY"):
        c = await logged_in(role, email=f"cust-{role.lower()}@x.io")
        async with c:
            page = await c.get(f"/customers/{telegram_id}")
            refused = await c.post(f"/customers/{telegram_id}/block", headers={"Origin": "http://test"})
        assert "/block" not in page.text, f"{role} is offered a control it may not use"
        assert refused.status_code == 403


async def test_an_exempt_customer_is_told_why_there_is_no_control(seeded):
    from tests.web import logged_in

    async with link.session() as s:
        reseller_id = (await s.execute(text("SELECT telegram_id FROM resellers LIMIT 1"))).scalar_one()
    c = await logged_in("ADMIN")
    async with c:
        page = await c.get(f"/customers/{reseller_id}")
    assert page.status_code == 200
    assert f"/customers/{reseller_id}/block" not in page.text
    assert "مستثنا" in page.text
