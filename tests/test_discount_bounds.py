"""Phase 7 item 4, dashboard half: the expiry date and per-customer cap.

AloBot's `discount_codes` only grows those two columns when its own
migration is applied, which is a separate change with its own go-ahead. So
this side asks the schema rather than a flag in a file: if the columns are
there, the form offers them; if they are not, it says so instead of
accepting a value it could not store.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.alobot.link import link
from app.alobot.seed import seed_alobot_copy
from app.alobot.writes import discounts as discount_writes
from app.db.session import async_session_maker
from tests.conftest import ALOBOT_ADMIN_URL, alobot_admin_execute
from tests.web import logged_in, make_operator


@pytest.fixture
async def seeded():
    await seed_alobot_copy(ALOBOT_ADMIN_URL, customers=6, seed=17)
    await link.connect()


@pytest.fixture
async def with_columns(seeded):
    """The AloBot migration, applied to the COPY only, so this half can be
    built and tested before that change merges."""
    await alobot_admin_execute(
        "ALTER TABLE discount_codes ADD COLUMN IF NOT EXISTS expires_at timestamptz NULL",
        "ALTER TABLE discount_codes ADD COLUMN IF NOT EXISTS per_user_limit integer NULL",
    )
    await link.connect()
    yield
    # Leave the schema as AloBot's own migrations leave it - with the columns -
    # rather than as this test happened to want it. A fixture that hands the
    # next test a different schema is how an ordering-dependent failure is born.
    await alobot_admin_execute(
        "ALTER TABLE discount_codes ADD COLUMN IF NOT EXISTS expires_at timestamptz NULL",
        "ALTER TABLE discount_codes ADD COLUMN IF NOT EXISTS per_user_limit integer NULL",
    )
    await link.connect()


@pytest.fixture
async def without_columns(seeded):
    """An AloBot that has not had the migration applied. Stated explicitly by
    dropping the columns rather than assumed from whatever `vendor/alobot`
    happens to be checked out at: once that migration merged, the copy grew
    the columns and this test silently became a test of nothing."""
    await alobot_admin_execute(
        "ALTER TABLE discount_codes DROP COLUMN IF EXISTS expires_at",
        "ALTER TABLE discount_codes DROP COLUMN IF EXISTS per_user_limit",
    )
    await link.connect()
    yield
    # Put the schema back the way AloBot's own migrations leave it. Without
    # this the next test inherits whichever shape ran last, which is how a
    # passing suite starts failing depending on ordering.
    await alobot_admin_execute(
        "ALTER TABLE discount_codes ADD COLUMN IF NOT EXISTS expires_at timestamptz NULL",
        "ALTER TABLE discount_codes ADD COLUMN IF NOT EXISTS per_user_limit integer NULL",
    )
    await link.connect()


async def test_without_the_alobot_migration_the_fields_are_not_offered(without_columns):
    assert discount_writes.bounds_available() is False
    c = await logged_in("ADMIN")
    async with c:
        page = await c.get("/discounts")
    assert page.status_code == 200
    assert 'name="expires_at"' not in page.text
    assert "مهاجرت" in page.text, "the page should say why the fields are absent"


async def test_with_the_columns_present_the_fields_are_offered_and_stored(with_columns):
    assert discount_writes.bounds_available() is True
    actor = await make_operator()
    async with async_session_maker() as db:
        await discount_writes.create(
            db, actor, code="SPRING", percent=Decimal("15"), usage_limit=None, categories=None, is_public=True,
            expires_at=dt.datetime(2026, 12, 31, tzinfo=dt.timezone.utc), per_user_limit=1,
        )
    async with link.session() as s:
        row = (await s.execute(text("SELECT expires_at, per_user_limit FROM discount_codes WHERE code='SPRING'"))).one()
    assert row.per_user_limit == 1 and row.expires_at.year == 2026


async def test_a_per_customer_cap_below_one_is_refused(with_columns):
    actor = await make_operator()
    async with async_session_maker() as db:
        with pytest.raises(discount_writes.DiscountError, match="حداقل"):
            await discount_writes.create(
                db, actor, code="BAD", percent=Decimal("10"), usage_limit=None, categories=None, is_public=True,
                expires_at=None, per_user_limit=0,
            )


async def test_an_expiry_in_the_past_is_refused_rather_than_silently_dead(with_columns):
    actor = await make_operator()
    async with async_session_maker() as db:
        with pytest.raises(discount_writes.DiscountError, match="گذشته"):
            await discount_writes.create(
                db, actor, code="OLD", percent=Decimal("10"), usage_limit=None, categories=None, is_public=True,
                expires_at=dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc), per_user_limit=None,
            )


async def test_the_form_round_trips_both_fields(with_columns):
    c = await logged_in("ADMIN")
    async with c:
        page = await c.get("/discounts")
        assert 'name="expires_at"' in page.text and 'name="per_user_limit"' in page.text
        made = await c.post(
            "/discounts",
            data={"code": "FORMCODE", "percent": "25", "usage_limit": "", "is_public": "1",
                  "expires_at": "1405/10/10", "per_user_limit": "2"},
            headers={"Origin": "http://test"},
        )
        assert made.status_code == 303
        after = await c.get("/discounts")
    assert "FORMCODE" in after.text
    async with link.session() as s:
        row = (await s.execute(text("SELECT expires_at, per_user_limit FROM discount_codes WHERE code='FORMCODE'"))).one()
    assert row.per_user_limit == 2 and row.expires_at is not None
