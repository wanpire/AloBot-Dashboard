"""Phase 5 task 2: discount codes."""

from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.alobot.link import link
from app.alobot.seed import seed_alobot_copy
from app.alobot.writes import discounts as discount_writes
from app.db.session import async_session_maker
from app.models import AuditLog
from tests.alobot_seed import write_engine
from tests.conftest import ALOBOT_ADMIN_URL
from tests.web import make_operator


@pytest.fixture
async def seeded():
    await seed_alobot_copy(ALOBOT_ADMIN_URL, customers=5, seed=3)
    await link.connect()
    return await make_operator(email="disc@x.io", role="ADMIN")


async def _one(sql: str, **params):
    async with write_engine.connect() as conn:
        return (await conn.execute(text(sql), params)).first()


async def test_code_is_upper_cased_the_way_alobot_normalises_it(seeded):
    async with async_session_maker() as db:
        await discount_writes.create(db, seeded, code="  spring25 ", percent=Decimal("25"), usage_limit=10, categories=["prime"], is_public=False)
    row = await _one("SELECT code, percent, usage_limit, categories, is_public, is_active FROM discount_codes WHERE code='SPRING25'")
    assert row is not None and row.percent == Decimal("25.00") and row.usage_limit == 10
    assert row.categories == "prime" and row.is_public is False and row.is_active is True


async def test_every_category_selected_is_stored_as_null_meaning_everything(seeded):
    async with async_session_maker() as db:
        await discount_writes.create(db, seeded, code="ALLCATS", percent=Decimal("5"), usage_limit=None, categories=["normal", "prime", "fixed", "junior"], is_public=True)
    assert (await _one("SELECT categories FROM discount_codes WHERE code='ALLCATS'")).categories is None


@pytest.mark.parametrize("percent", [Decimal("0"), Decimal("-5"), Decimal("101")])
async def test_percent_must_be_between_one_and_a_hundred(seeded, percent):
    async with async_session_maker() as db:
        with pytest.raises(discount_writes.DiscountError, match="درصد"):
            await discount_writes.create(db, seeded, code="BAD", percent=percent, usage_limit=None, categories=None, is_public=True)


async def test_a_duplicate_code_is_refused_with_a_sentence_not_a_crash(seeded):
    async with async_session_maker() as db:
        with pytest.raises(discount_writes.DiscountError, match="قبلاً"):
            await discount_writes.create(db, seeded, code="welcome10", percent=Decimal("10"), usage_limit=None, categories=None, is_public=True)


async def test_unknown_category_is_refused(seeded):
    async with async_session_maker() as db:
        with pytest.raises(discount_writes.DiscountError):
            await discount_writes.create(db, seeded, code="NEWC", percent=Decimal("10"), usage_limit=None, categories=["platinum"], is_public=True)


async def test_enable_disable_and_audit(seeded):
    async with async_session_maker() as db:
        await discount_writes.set_active(db, seeded, 1, False)
        audit = (await db.execute(select(AuditLog).where(AuditLog.action == "discount.active"))).scalars().one()
    assert (await _one("SELECT is_active FROM discount_codes WHERE id=1")).is_active is False
    assert audit.after == {"is_active": False}


async def _use_code(code_id: int = 1) -> int:
    """A deterministic usage row: the seed only sometimes produces one."""
    async with write_engine.begin() as conn:
        payment_id = (await conn.execute(text("SELECT id FROM payments ORDER BY id LIMIT 1"))).scalar_one()
        await conn.execute(
            text("INSERT INTO discount_code_usages (discount_code_id, code, percent, payment_id, telegram_id, original_amount, amount, used_at) "
                 "VALUES (:id, 'WELCOME10', 10, :pid, 100000001, 100000, 90000, now())"),
            {"id": code_id, "pid": payment_id},
        )
    return code_id


async def test_an_unused_code_can_be_deleted_and_a_used_one_cannot(seeded):
    used_id = await _use_code()
    async with async_session_maker() as db:
        with pytest.raises(discount_writes.DiscountError, match="مصرف"):
            await discount_writes.delete(db, seeded, used_id)
        await discount_writes.create(db, seeded, code="UNUSED", percent=Decimal("10"), usage_limit=None, categories=None, is_public=True)
        fresh = (await _one("SELECT id FROM discount_codes WHERE code='UNUSED'")).id
        await discount_writes.delete(db, seeded, fresh)
    assert await _one("SELECT id FROM discount_codes WHERE code='UNUSED'") is None
    assert await _one("SELECT id FROM discount_codes WHERE id=:id", id=used_id) is not None


async def test_usages_list_names_the_customer_and_what_was_given(seeded):
    await _use_code()
    async with async_session_maker() as db:
        rows = await discount_writes.usages(db, 1)
    assert rows and rows[0].telegram_id and rows[0].original_amount > rows[0].amount


async def test_expiry_and_per_user_limit_came_from_alobots_own_migration():
    from pathlib import Path

    draft = Path("docs/alobot-migrations/0001_discount_expiry_and_per_user_limit.md")
    assert draft.exists(), "the Phase 7 AloBot migration must be written down"
    body = draft.read_text()
    assert "expires_at" in body and "per_user_limit" in body and "Phase 7" in body
    async with write_engine.connect() as conn:
        cols = {r.column_name for r in await conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='discount_codes'"))}
    # When this was written the migration was drafted and unapplied, and the
    # assertion was that the copy must NOT have the columns. AloBot has since
    # merged it, so the copy grows them the only legitimate way: by running
    # AloBot's own migrations. What must still hold is that they arrived from
    # there and not from anything this project did.
    assert "expires_at" in cols and "per_user_limit" in cols
    migration = Path("vendor/alobot/alembic/versions")
    applied_upstream = [f for f in migration.glob("*.py") if "per_user_limit" in f.read_text()]
    assert applied_upstream, "the columns exist in the copy but no AloBot migration creates them"
