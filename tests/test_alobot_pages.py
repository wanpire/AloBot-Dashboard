"""Phase 2 tasks 5-10: every AloBot-backed screen renders from the copy, in
Persian, and never writes."""

import datetime as dt

import pytest
from sqlalchemy import event, text

from app.alobot.link import link
from app.alobot.seed import seed_alobot_copy
from app.alobot.time import tehran_day_bounds
from app.db.session import alobot_engine
from app.web.format import fa_number
from tests.alobot_seed import write_engine
from tests.conftest import ALOBOT_ADMIN_URL
from tests.web import logged_in


@pytest.fixture
async def seeded():
    counts = await seed_alobot_copy(ALOBOT_ADMIN_URL, customers=30, seed=7)
    await link.connect()
    return counts


async def _scalar(sql: str, **params):
    async with write_engine.connect() as conn:
        return (await conn.execute(text(sql), params)).scalar_one()


def test_tehran_day_bounds_cover_exactly_one_local_day():
    start, end = tehran_day_bounds(dt.date(2026, 9, 20))
    assert start == dt.datetime(2026, 9, 19, 20, 30, tzinfo=dt.timezone.utc)  # 00:00 Tehran = 20:30 UTC previous day
    assert end - start == dt.timedelta(days=1)


async def test_overview_shows_the_pending_queue_and_todays_figures(seeded):
    pending = await _scalar("SELECT COUNT(*) FROM payments WHERE status='pending' AND method='card'")
    c = await logged_in("READ_ONLY")
    async with c:
        r = await c.get("/")
    assert r.status_code == 200
    assert "پرداخت‌های کارت در انتظار" in r.text
    assert fa_number(pending) in r.text
    assert "alo." in r.text  # the pending list names the account


async def test_stats_match_the_database_for_the_chosen_period(seeded):
    start, _ = tehran_day_bounds(dt.date(2026, 6, 1))
    revenue = await _scalar("SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='approved' AND resolved_at >= :s", s=start)
    orders = await _scalar("SELECT COUNT(*) FROM payments WHERE status='approved' AND resolved_at >= :s", s=start)
    c = await logged_in("READ_ONLY")
    async with c:
        r = await c.get("/stats?days=365")
    assert r.status_code == 200
    assert fa_number(orders) in r.text
    assert fa_number(int(revenue)) in r.text
    assert "هم‌اکنون" in r.text  # running totals are labelled as a snapshot


async def test_customers_search_by_username_and_by_telegram_id(seeded):
    c = await logged_in("ADMIN")
    async with c:
        by_name = await c.get("/customers?q=ali_")
        by_id = await c.get("/customers?q=100000003")
        card = await c.get("/customers/100000003")
    assert by_name.status_code == 200 and "ali_" in by_name.text
    assert by_id.status_code == 200 and "۱۰۰۰۰۰۰۰۳" in by_id.text
    assert card.status_code == 200 and "اکانت‌ها" in card.text and "پرداخت‌ها" in card.text


async def test_customers_is_hidden_from_read_only():
    c = await logged_in("READ_ONLY")
    async with c:
        assert (await c.get("/customers")).status_code == 403


async def test_orders_filter_by_status_and_find_by_invoice_code(seeded):
    code = await _scalar("SELECT invoice_number FROM payments WHERE invoice_number IS NOT NULL ORDER BY id LIMIT 1")
    tg = await _scalar("SELECT telegram_id FROM payments WHERE invoice_number = :c", c=code)
    c = await logged_in("ADMIN")
    async with c:
        pending = await c.get("/orders?status=pending")
        found = await c.get(f"/orders?q={code}")
    assert pending.status_code == 200 and "در انتظار" in pending.text
    assert found.status_code == 200 and code in found.text and str(tg) in found.text.replace("۰", "0").replace("۱", "1").replace("۲", "2").replace("۳", "3").replace("۴", "4").replace("۵", "5").replace("۶", "6").replace("۷", "7").replace("۸", "8").replace("۹", "9")


async def test_subscriptions_list_shows_expiry_or_says_it_is_unknown(seeded):
    c = await logged_in("ADMIN")
    async with c:
        r = await c.get("/subscriptions")
        trials = await c.get("/subscriptions?trial=1")
    assert r.status_code == 200 and "نامشخص" in r.text
    assert trials.status_code == 200 and "Trial" in trials.text


async def test_resellers_catalog_discounts_tutorials_and_bot_settings_render(seeded):
    c = await logged_in("READ_ONLY")
    async with c:
        pages = {p: await c.get(f"/{p}") for p in ("resellers", "catalog", "discounts", "tutorials")}
    for name, r in pages.items():
        assert r.status_code == 200, name
    assert "۱٬۲۵۰٬۰۰۰" in pages["resellers"].text
    # The catalog is a plan matrix now, not a list of titles: category
    # headings, the IBSng group each slot is bound to, and its price.
    assert "ماتریس پلن" in pages["catalog"].text and "پرایم" in pages["catalog"].text
    assert "Prime-1M-1U" in pages["catalog"].text
    assert "WELCOME10" in pages["discounts"].text
    assert "اندروید" in pages["tutorials"].text and "OpenVPN" in pages["tutorials"].text


async def test_bot_settings_page_is_admin_only_and_labels_alobot_keys(seeded):
    ro = await logged_in("READ_ONLY")
    async with ro:
        assert (await ro.get("/botsettings")).status_code == 403
    admin = await logged_in("ADMIN")
    async with admin:
        r = await admin.get("/botsettings")
    assert r.status_code == 200 and "تایید خودکار" in r.text and "6037991234567893" in r.text


async def test_write_screens_carry_the_read_only_banner_until_integration(seeded):
    c = await logged_in("ADMIN")
    async with c:
        r = await c.get("/catalog")
    assert "فقط‌خواندنی" in r.text


async def test_unavailable_link_renders_a_reason_not_an_error(seeded, monkeypatch):
    monkeypatch.setattr(link, "problems", ["AloBot column payments.amount is missing"])
    c = await logged_in("ADMIN")
    async with c:
        r = await c.get("/orders")
    assert r.status_code == 200 and "payments.amount" in r.text and "متصل نیست" in r.text


async def test_every_alobot_page_issues_only_selects(seeded):
    statements: list[str] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.strip().upper())

    event.listen(alobot_engine.sync_engine, "before_cursor_execute", capture)
    try:
        c = await logged_in("ADMIN")
        async with c:
            for path in ("/", "/stats", "/customers?q=ali", "/customers/100000001", "/orders", "/subscriptions",
                         "/resellers", "/catalog", "/discounts", "/tutorials", "/botsettings"):
                assert (await c.get(path)).status_code == 200, path
    finally:
        event.remove(alobot_engine.sync_engine, "before_cursor_execute", capture)
    assert statements, "no AloBot queries ran"
    assert all(s.startswith("SELECT") or s.startswith("WITH") for s in statements), [s[:40] for s in statements if not s.startswith("SELECT")]
