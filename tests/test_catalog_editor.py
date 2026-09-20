"""Phase 5 task 1: the catalog editor, holding AloBot's own rules."""

from decimal import Decimal

import pytest
from sqlalchemy import text

from app.alobot.link import link
from app.alobot.seed import seed_alobot_copy
from app.alobot.writes import catalog as catalog_writes
from app.db.session import async_session_maker
from tests.alobot_seed import write_engine
from tests.conftest import ALOBOT_ADMIN_URL
from tests.web import make_operator


@pytest.fixture
async def seeded():
    await seed_alobot_copy(ALOBOT_ADMIN_URL, customers=5, seed=3)
    await link.connect()
    return await make_operator(email="cat@x.io", role="ADMIN")


async def _one(sql: str, **params):
    async with write_engine.connect() as conn:
        return (await conn.execute(text(sql), params)).first()


async def test_plan_dimensions_and_title_come_from_alobots_own_rules():
    from pathlib import Path

    source = (Path("vendor/alobot") / "app" / "services" / "catalog.py").read_text()
    assert "DURATIONS = (1, 2)" in source and "USER_COUNTS = (1, 2)" in source
    assert catalog_writes.DURATIONS == (1, 2) and catalog_writes.USER_COUNTS == (1, 2)
    assert catalog_writes.format_plan_title(2, 1) == "۲ ماه ۱ کاربر"


async def test_bind_creates_the_slot_with_a_generated_title_and_rebinds_in_place(seeded):
    actor = seeded
    async with async_session_maker() as db:
        await catalog_writes.bind_slot(db, actor, category="prime", duration_months=2, user_count=2, group_name="1M-1U", price=Decimal("300000"), location_id=None)
        row = await _one("SELECT id, title, group_name, price FROM services WHERE category='prime' AND duration_months=2 AND user_count=2 AND location_id IS NULL")
        assert row.title == "۲ ماه ۲ کاربر" and row.group_name == "1M-1U" and row.price == Decimal("300000.00")
        await catalog_writes.bind_slot(db, actor, category="prime", duration_months=2, user_count=2, group_name="2M-2U", price=Decimal("310000"), location_id=None)
        again = await _one("SELECT id, group_name, price FROM services WHERE category='prime' AND duration_months=2 AND user_count=2 AND location_id IS NULL")
    assert again.id == row.id and again.group_name == "2M-2U" and again.price == Decimal("310000.00")


async def test_bind_refuses_an_unknown_group_a_trial_group_and_bad_dimensions(seeded):
    actor = seeded
    async with async_session_maker() as db:
        with pytest.raises(catalog_writes.CatalogError, match="گروه"):
            await catalog_writes.bind_slot(db, actor, category="normal", duration_months=1, user_count=1, group_name="no-such-group", price=Decimal("1"), location_id=None)
        with pytest.raises(catalog_writes.CatalogError, match="تست"):
            await catalog_writes.bind_slot(db, actor, category="normal", duration_months=1, user_count=1, group_name="Trial", price=Decimal("1"), location_id=None)
        with pytest.raises(catalog_writes.CatalogError):
            await catalog_writes.bind_slot(db, actor, category="normal", duration_months=7, user_count=1, group_name="1M-1U", price=Decimal("1"), location_id=None)
        with pytest.raises(catalog_writes.CatalogError, match="قیمت"):
            await catalog_writes.bind_slot(db, actor, category="normal", duration_months=1, user_count=1, group_name="1M-1U", price=Decimal("0"), location_id=None)


async def test_fixed_needs_a_location_and_the_others_refuse_one(seeded):
    actor = seeded
    async with async_session_maker() as db:
        with pytest.raises(catalog_writes.CatalogError, match="لوکیشن"):
            await catalog_writes.bind_slot(db, actor, category="fixed", duration_months=1, user_count=1, group_name="1M-1U", price=Decimal("1000"), location_id=None)
        with pytest.raises(catalog_writes.CatalogError, match="لوکیشن"):
            await catalog_writes.bind_slot(db, actor, category="normal", duration_months=1, user_count=1, group_name="1M-1U", price=Decimal("1000"), location_id=1)


async def test_every_edit_leaves_an_audit_row_in_our_database(seeded):
    from sqlalchemy import select

    from app.models import AuditLog

    actor = seeded
    async with async_session_maker() as db:
        await catalog_writes.bind_slot(db, actor, category="prime", duration_months=1, user_count=1, group_name="1M-1U", price=Decimal("123000"), location_id=None)
        rows = (await db.execute(select(AuditLog).where(AuditLog.action == "catalog.bind_slot"))).scalars().all()
    assert len(rows) == 1 and rows[0].actor_operator_id == actor.id and rows[0].after["price"] == "123000"


async def test_location_lifecycle_and_delete_is_refused_while_plans_are_bound(seeded):
    actor = seeded
    async with async_session_maker() as db:
        loc = await catalog_writes.create_location(db, actor, title="Germany", flag_emoji="🇩🇪")
        await catalog_writes.set_location_active(db, actor, loc, False)
        assert (await _one("SELECT is_active FROM service_locations WHERE id=:id", id=loc)).is_active is False
        await catalog_writes.bind_slot(db, actor, category="fixed", duration_months=1, user_count=1, group_name="DE-1M-1U", price=Decimal("140000"), location_id=loc)
        with pytest.raises(catalog_writes.CatalogError, match="پلن"):
            await catalog_writes.delete_location(db, actor, loc)
        await catalog_writes.unbind_slot(db, actor, category="fixed", duration_months=1, user_count=1, location_id=loc)
        await catalog_writes.delete_location(db, actor, loc)
    assert await _one("SELECT id FROM service_locations WHERE id=:id", id=loc) is None


async def test_categories_are_switched_through_alobots_own_config_key(seeded):
    actor = seeded
    async with async_session_maker() as db:
        await catalog_writes.set_category_enabled(db, actor, "junior", False)
        assert (await _one("SELECT value FROM app_config WHERE key='category_enabled:junior'")).value == "false"
        await catalog_writes.set_category_enabled(db, actor, "junior", True)
        assert (await _one("SELECT value FROM app_config WHERE key='category_enabled:junior'")).value == "true"
        with pytest.raises(catalog_writes.CatalogError):
            await catalog_writes.set_category_enabled(db, actor, "nope", False)


async def test_service_can_be_activated_and_deactivated(seeded):
    actor = seeded
    async with async_session_maker() as db:
        await catalog_writes.set_service_active(db, actor, 1, False)
    assert (await _one("SELECT is_active FROM services WHERE id=1")).is_active is False


# ── bulk price ─────────────────────────────────────────────────────────────


async def test_bulk_price_preview_and_apply_come_from_the_same_expression(seeded):
    actor = seeded
    async with async_session_maker() as db:
        preview = await catalog_writes.bulk_price(db, actor, mode="percent", value=Decimal("10"), category="normal", location_id=None, apply=False)
        before = {r["id"]: r["price"] for r in preview["rows"]}
        assert preview["count"] == len(before) > 0
        assert all(r["new_price"] == (r["price"] * Decimal("1.1")).quantize(Decimal("1")) for r in preview["rows"])
        async with write_engine.connect() as conn:
            unchanged = {r.id: r.price for r in await conn.execute(text("SELECT id, price FROM services WHERE category='normal'"))}
        assert unchanged == {k: v for k, v in before.items()}
        applied = await catalog_writes.bulk_price(db, actor, mode="percent", value=Decimal("10"), category="normal", location_id=None, apply=True)
        async with write_engine.connect() as conn:
            after = {r.id: r.price for r in await conn.execute(text("SELECT id, price FROM services WHERE category='normal'"))}
    assert applied["count"] == preview["count"]
    assert after == {r["id"]: r["new_price"] for r in preview["rows"]}


async def test_bulk_price_rounds_to_whole_toman_and_scopes_by_category(seeded):
    actor = seeded
    async with write_engine.begin() as conn:
        await conn.execute(text("UPDATE services SET price = 95555 WHERE category='normal'"))
        prime_before = (await conn.execute(text("SELECT price FROM services WHERE category='prime' LIMIT 1"))).scalar_one()
    async with async_session_maker() as db:
        await catalog_writes.bulk_price(db, seeded, mode="percent", value=Decimal("10"), category="normal", location_id=None, apply=True)
    async with write_engine.connect() as conn:
        prices = {r.price for r in await conn.execute(text("SELECT price FROM services WHERE category='normal'"))}
        prime_after = (await conn.execute(text("SELECT price FROM services WHERE category='prime' LIMIT 1"))).scalar_one()
    assert prices == {Decimal("105111.00")} and prime_after == prime_before


async def test_a_decrease_that_would_reach_zero_is_refused_before_anything_changes(seeded):
    actor = seeded
    async with write_engine.connect() as conn:
        before = {r.id: r.price for r in await conn.execute(text("SELECT id, price FROM services"))}
    async with async_session_maker() as db:
        with pytest.raises(catalog_writes.CatalogError, match="صفر"):
            await catalog_writes.bulk_price(db, actor, mode="amount", value=Decimal("-99999999"), category=None, location_id=None, apply=True)
    async with write_engine.connect() as conn:
        after = {r.id: r.price for r in await conn.execute(text("SELECT id, price FROM services"))}
    assert after == before


async def test_bulk_price_ignores_inactive_plans_and_audits_the_previous_prices(seeded):
    from sqlalchemy import select

    from app.models import AuditLog

    actor = seeded
    async with write_engine.begin() as conn:
        await conn.execute(text("UPDATE services SET is_active = false WHERE id = 1"))
        frozen = (await conn.execute(text("SELECT price FROM services WHERE id = 1"))).scalar_one()
    async with async_session_maker() as db:
        await catalog_writes.bulk_price(db, actor, mode="amount", value=Decimal("5000"), category=None, location_id=None, apply=True)
        audit = (await db.execute(select(AuditLog).where(AuditLog.action == "catalog.bulk_price"))).scalars().one()
    async with write_engine.connect() as conn:
        assert (await conn.execute(text("SELECT price FROM services WHERE id = 1"))).scalar_one() == frozen
    assert audit.before["prices"] and str(1) not in audit.before["prices"]
