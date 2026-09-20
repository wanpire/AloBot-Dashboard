"""Phase 4 tasks 5-8 and 10 over HTTP: the payments queue, continuity mode,
financial stats, and the bell."""

import datetime as dt

from sqlalchemy import select

from app.db.session import async_session_maker
from app.models import PaymentClaim, ReconciliationMatch
from app.services import settle
from tests.test_claims_sweeps import _account_with_card, _claim, _credit
from tests.web import logged_in

O = {"Origin": "http://test"}
NOW = dt.datetime(2026, 9, 20, 12, 0, tzinfo=dt.timezone.utc)


async def _ambiguous():
    acct = await _account_with_card()
    a = await _claim(account_id=acct, alobot_id=1, at=NOW - dt.timedelta(minutes=20))
    b = await _claim(account_id=acct, alobot_id=2, at=NOW - dt.timedelta(minutes=19))
    tx = await _credit(at=NOW - dt.timedelta(minutes=18))
    async with async_session_maker() as s:
        await settle.settle(s, now=NOW)
    return a, b, tx


async def test_review_tab_lists_ambiguous_claims_with_their_candidates_and_the_bell_counts_them():
    a, b, tx = await _ambiguous()
    c = await logged_in("REVIEWER")
    async with c:
        r = await c.get("/payments?tab=review")
        home = await c.get("/")
    assert r.status_code == 200
    assert "AMBIGUOUS_CLAIMS" in r.text and f'name="transaction_id" value="{tx}"' in r.text
    assert 'class="badge badge--count">۲<' in home.text


async def test_reviewer_can_approve_from_the_page_and_the_row_moves_tabs():
    a, b, tx = await _ambiguous()
    c = await logged_in("REVIEWER")
    async with c:
        r = await c.post(f"/payments/{a}/approve", data={"transaction_id": str(tx)}, headers=O)
        assert r.status_code == 303, r.text
        manual = await c.get("/payments?tab=manual")
        review_tab = await c.get("/payments?tab=review")
    assert f"/payments/{a}/" in manual.text and f'/payments/{a}/approve' not in review_tab.text
    async with async_session_maker() as s:
        assert (await s.get(PaymentClaim, a)).status == "MANUAL_VERIFIED"


async def test_refusals_are_the_servers_sentence_with_a_400():
    a, b, tx = await _ambiguous()
    c = await logged_in("REVIEWER")
    async with c:
        await c.post(f"/payments/{a}/approve", data={"transaction_id": str(tx)}, headers=O)
        r = await c.post(f"/payments/{b}/approve", data={"transaction_id": str(tx)}, headers=O)
    assert r.status_code == 400 and "قبلاً" in r.text


async def test_search_finds_a_claim_by_invoice_code_telegram_id_and_reference():
    a, b, tx = await _ambiguous()
    async with async_session_maker() as s:
        claim = await s.get(PaymentClaim, a)
        claim.invoice_code = "ABC234"
        await s.commit()
    c = await logged_in("READ_ONLY")
    async with c:
        by_code = await c.get("/payments?tab=all&q=ABC234")
        by_tg = await c.get("/payments?tab=all&q=100000001")
        by_ref = await c.get(f"/payments?tab=all&q={int((NOW - dt.timedelta(minutes=18)).timestamp())}")
    assert "ABC234" in by_code.text and by_code.text.count("payment-row") == 1
    assert by_tg.text.count("payment-row") == 2
    assert by_ref.text.count("payment-row") == 2  # both claims list the same candidate credit


async def test_auto_verified_tab_is_segmented_by_purpose():
    acct = await _account_with_card()
    await _claim(account_id=acct, alobot_id=1, at=NOW - dt.timedelta(minutes=5))
    await _credit(at=NOW - dt.timedelta(minutes=4))
    async with async_session_maker() as s:
        await settle.settle(s, now=NOW)
    c = await logged_in("READ_ONLY")
    async with c:
        purchases = await c.get("/payments?tab=auto&purpose=purchase")
        renewals = await c.get("/payments?tab=auto&purpose=renew")
    assert purchases.text.count("payment-row") == 1 and renewals.text.count("payment-row") == 0


async def test_continuity_mode_needs_a_reason_and_a_bounded_duration_and_shows_a_banner_everywhere():
    c = await logged_in("ADMIN")
    async with c:
        bad = await c.post("/payments/continuity", data={"minutes": "9999", "reason": "x"}, headers=O)
        assert bad.status_code == 400
        ok = await c.post("/payments/continuity", data={"minutes": "60", "reason": "گوشی رله خاموش است"}, headers=O)
        assert ok.status_code == 303
        anywhere = await c.get("/devices")
        assert "حالت تداوم فعال است" in anywhere.text and "گوشی رله خاموش است" in anywhere.text
        off = await c.post("/payments/continuity/off", data={}, headers=O)
        assert off.status_code == 303
        after = await c.get("/devices")
    assert "حالت تداوم فعال است" not in after.text


async def test_reviewer_cannot_switch_continuity():
    c = await logged_in("REVIEWER")
    async with c:
        r = await c.post("/payments/continuity", data={"minutes": "60", "reason": "x"}, headers=O)
    assert r.status_code == 403


async def test_finance_page_reports_automation_rate_and_time_to_credit():
    acct = await _account_with_card()
    await _claim(account_id=acct, alobot_id=1, at=NOW - dt.timedelta(minutes=5))
    await _credit(at=NOW - dt.timedelta(minutes=3))
    async with async_session_maker() as s:
        await settle.settle(s, now=NOW)
    c = await logged_in("READ_ONLY")
    async with c:
        r = await c.get("/finance?days=7")
    assert r.status_code == 200
    assert "نرخ تایید خودکار" in r.text and "۱۰۰٪" in r.text and "۱۲۰" in r.text  # 120 seconds claim→credit


async def test_no_getupdates_anywhere_under_app():
    import pathlib

    hits = [p for p in pathlib.Path("app").rglob("*.py") if "getUpdates" in p.read_text() or "getupdates" in p.read_text().lower()]
    assert hits == [], hits


async def test_phase4_sweeps_are_registered():
    from app.services.sweeps import registry

    assert {"claims.mirror", "claims.settle", "outbox.flush"} <= set(registry.names())
