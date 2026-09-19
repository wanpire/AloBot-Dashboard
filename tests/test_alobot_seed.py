"""Phase 2 task 4: synthetic AloBot data for demos and tests - never on a
non-local host."""

import pytest
from sqlalchemy import text

from app.alobot.seed import SeedRefused, seed_alobot_copy
from tests.alobot_seed import write_engine
from tests.conftest import ALOBOT_ADMIN_URL


async def test_seed_refuses_any_host_but_localhost():
    with pytest.raises(SeedRefused):
        await seed_alobot_copy("postgresql+asyncpg://u:p@db.example.com:5432/alobot")


async def test_seed_fills_every_table_the_screens_read_with_every_payment_state():
    counts = await seed_alobot_copy(ALOBOT_ADMIN_URL, customers=30, seed=7)
    assert counts["bot_users"] == 30
    for table in ("payments", "vpn_users", "services", "service_locations", "groups", "resellers",
                  "discount_codes", "discount_code_usages", "tutorial_platforms", "tutorial_protocols",
                  "tutorial_guides", "download_links", "openvpn_profiles", "app_config", "admin_users"):
        assert counts[table] > 0, table
    async with write_engine.connect() as conn:
        statuses = {r[0] for r in await conn.execute(text("SELECT DISTINCT status FROM payments"))}
        methods = {r[0] for r in await conn.execute(text("SELECT DISTINCT method FROM payments"))}
        purposes = {r[0] for r in await conn.execute(text("SELECT DISTINCT purpose FROM payments"))}
    assert {"pending", "approved", "rejected", "revoked"} <= statuses
    assert {"card", "crypto"} <= methods
    assert {"purchase", "renew", "upgrade"} <= purposes


async def test_seed_is_deterministic_and_idempotent():
    a = await seed_alobot_copy(ALOBOT_ADMIN_URL, customers=10, seed=1)
    b = await seed_alobot_copy(ALOBOT_ADMIN_URL, customers=10, seed=1)
    assert a == b
    async with write_engine.connect() as conn:
        usernames = [r[0] for r in await conn.execute(text("SELECT ibsng_username FROM vpn_users ORDER BY id"))]
    assert usernames == sorted(set(usernames), key=usernames.index) and len(usernames) == b["vpn_users"]
