"""Phase 7 item 3: topping up a reseller's balance from the dashboard.

The balance is a single `Numeric` column on AloBot's `resellers` table with
no ledger behind it, and AloBot's own purchase path reads it, subtracts in
Python and commits. That read-modify-write has no row lock, so a top-up
landing inside its window can be overwritten and the money vanishes. The lock
is being fixed in AloBot separately; this side does three things regardless:

- writes atomically (`balance = balance + :amount`), so two writes of this
  kind can never lose each other,
- keeps its own ledger, so what the dashboard believes it added is recorded
  here even if AloBot's column later disagrees,
- reads the balance back and raises an operator alert when it did not move by
  the amount asked for, so a lost top-up is noticed instead of discovered.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.alobot.link import link
from app.alobot.seed import seed_alobot_copy
from app.alobot.writes import resellers as reseller_writes
from app.alobot.writes import writes
from app.db.session import async_session_maker
from app.models import BotNotification, ResellerTopUp
from app.services import settings as settings_service
from tests.conftest import ALOBOT_ADMIN_URL
from tests.web import make_operator


@pytest.fixture
async def seeded():
    await seed_alobot_copy(ALOBOT_ADMIN_URL, customers=10, seed=13)
    await link.connect()


async def _a_reseller() -> tuple[int, Decimal]:
    async with link.session() as s:
        row = (await s.execute(text("SELECT telegram_id, balance FROM resellers ORDER BY id LIMIT 1"))).first()
    return row.telegram_id, row.balance


async def _balance(telegram_id: int) -> Decimal:
    async with link.session() as s:
        return (await s.execute(text("SELECT balance FROM resellers WHERE telegram_id=:t"), {"t": telegram_id})).scalar_one()


async def test_a_top_up_moves_the_balance_and_is_written_down_here(seeded):
    telegram_id, before = await _a_reseller()
    actor = await make_operator()
    async with async_session_maker() as db:
        await reseller_writes.top_up(db, actor, telegram_id=telegram_id, amount_toman=Decimal("50000"))
        entries = (await db.execute(select(ResellerTopUp))).scalars().all()
    assert await _balance(telegram_id) == before + Decimal("50000")
    assert len(entries) == 1
    entry = entries[0]
    assert entry.telegram_id == telegram_id
    assert entry.amount_irr == 500_000  # this project stores Rial
    assert entry.balance_before_irr == int(before * 10) and entry.balance_after_irr == int((before + 50000) * 10)
    assert entry.verified is True


async def test_the_write_is_atomic_rather_than_read_modify_write(seeded):
    """Two top-ups racing must both land. This is the property AloBot's own
    deduction lacks, and the reason it is being fixed there separately."""
    import asyncio

    telegram_id, before = await _a_reseller()
    actor = await make_operator()

    async def top_up(amount: str):
        async with async_session_maker() as db:
            await reseller_writes.top_up(db, actor, telegram_id=telegram_id, amount_toman=Decimal(amount))

    await asyncio.gather(top_up("1000"), top_up("2000"), top_up("3000"))
    assert await _balance(telegram_id) == before + Decimal("6000")


async def test_a_top_up_that_did_not_land_raises_an_alert_instead_of_reporting_success(seeded, monkeypatch):
    """If AloBot overwrote the balance between our write and our read-back,
    the operator hears about it. Simulated by making the read-back lie."""
    telegram_id, before = await _a_reseller()
    actor = await make_operator()

    async def clobbered(_telegram_id: int) -> Decimal:
        return before  # as if the purchase path had overwritten the top-up

    monkeypatch.setattr(reseller_writes, "_read_balance", clobbered)
    async with async_session_maker() as db:
        await settings_service.set_many(db, {("alerts", "operator_chat_id"): "555"}, actor_email="t", actor_role="ADMIN")
        with pytest.raises(reseller_writes.ResellerError, match="ثبت نشد"):
            await reseller_writes.top_up(db, actor, telegram_id=telegram_id, amount_toman=Decimal("7000"))

    async with async_session_maker() as db:
        entries = (await db.execute(select(ResellerTopUp))).scalars().all()
        alerts = (await db.execute(select(BotNotification))).scalars().all()
    assert len(entries) == 1 and entries[0].verified is False
    assert len(alerts) == 1 and "۷٬۰۰۰" in alerts[0].payload["text"]


async def test_a_zero_or_negative_top_up_is_refused(seeded):
    telegram_id, _ = await _a_reseller()
    actor = await make_operator()
    async with async_session_maker() as db:
        for bad in ("0", "-100"):
            with pytest.raises(reseller_writes.ResellerError, match="بیشتر از صفر"):
                await reseller_writes.top_up(db, actor, telegram_id=telegram_id, amount_toman=Decimal(bad))


async def test_topping_up_someone_who_is_not_a_reseller_is_refused(seeded):
    actor = await make_operator()
    async with async_session_maker() as db:
        with pytest.raises(reseller_writes.ResellerError, match="نماینده"):
            await reseller_writes.top_up(db, actor, telegram_id=987654321, amount_toman=Decimal("1000"))


async def test_the_write_role_may_move_the_balance_and_nothing_else_on_that_table(seeded):
    telegram_id, _ = await _a_reseller()
    async with writes.session() as s:
        with pytest.raises(DBAPIError, match="permission denied"):
            await s.execute(text("UPDATE resellers SET commission_percent = 99 WHERE telegram_id=:t"), {"t": telegram_id})
    async with writes.session() as s:
        with pytest.raises(DBAPIError, match="permission denied"):
            await s.execute(text("DELETE FROM resellers"))


# ── the screen ─────────────────────────────────────────────────────────────


async def test_an_admin_tops_up_from_the_resellers_page_and_sees_the_ledger(seeded):
    from tests.web import logged_in

    telegram_id, before = await _a_reseller()
    c = await logged_in("ADMIN")
    async with c:
        page = await c.get("/resellers")
        assert f"/resellers/{telegram_id}/topup" in page.text
        done = await c.post(
            f"/resellers/{telegram_id}/topup",
            data={"amount": "25000", "note": "کارت به کارت"},
            headers={"Origin": "http://test"},
        )
        assert done.status_code == 303
        after = await c.get("/resellers")
    assert await _balance(telegram_id) == before + Decimal("25000")
    assert "کارت به کارت" in after.text, "the ledger of what the dashboard added is not shown"


async def test_a_reviewer_is_offered_no_top_up_control_and_is_refused_if_they_post(seeded):
    from tests.web import logged_in

    telegram_id, before = await _a_reseller()
    for role in ("REVIEWER", "READ_ONLY"):
        c = await logged_in(role, email=f"rs-{role.lower()}@x.io")
        async with c:
            page = await c.get("/resellers")
            refused = await c.post(f"/resellers/{telegram_id}/topup", data={"amount": "1000"}, headers={"Origin": "http://test"})
        assert "/topup" not in page.text, f"{role} is offered a control it may not use"
        assert refused.status_code == 403
    assert await _balance(telegram_id) == before


async def test_a_non_numeric_amount_is_refused_with_a_sentence(seeded):
    from tests.web import logged_in

    telegram_id, before = await _a_reseller()
    c = await logged_in("ADMIN")
    async with c:
        answer = await c.post(f"/resellers/{telegram_id}/topup", data={"amount": "خیلی"}, headers={"Origin": "http://test"})
        assert answer.status_code == 303
        page = await c.get(answer.headers["location"])
    assert "عدد" in page.text
    assert await _balance(telegram_id) == before
