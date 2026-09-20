"""Phase 4 tasks 5-8 and 10 over HTTP: the payments queue, continuity mode,
financial stats, and the bell."""

import datetime as dt
import re

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
    # The bell's count, selected by its handle rather than by whichever
    # shell's classes the overview happens to be wearing today.
    assert 'data-testid="bell-count">۲<' in home.text, "the bell does not show two"


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


async def test_finance_shows_the_banks_own_balance_beside_what_we_counted():
    """Every bank SMS carries the account balance. Income the dashboard
    counted is only half the picture: what makes a discrepancy visible is the
    bank's own number next to it, with the time it was reported."""
    acct = await _account_with_card()
    await _claim(account_id=acct, alobot_id=1, at=NOW - dt.timedelta(minutes=5))
    await _credit(at=NOW - dt.timedelta(minutes=3))
    async with async_session_maker() as s:
        await settle.settle(s, now=NOW)
        from app.services import finance

        summary = await finance.summary(s, since=NOW - dt.timedelta(days=7))
    # The seeded SMS reports a balance of 8,354,098 rial.
    assert summary["balances"], "no balance was reported for any account"
    name, balance_irr, at = summary["balances"][0]
    assert balance_irr == 8354098 and at is not None

    c = await logged_in("READ_ONLY")
    async with c:
        r = await c.get("/finance?days=7")
    assert "۸۳۵٬۴۰۹" in r.text, "the bank's reported balance is not on the page"


async def test_no_getupdates_anywhere_under_app():
    import pathlib

    hits = [p for p in pathlib.Path("app").rglob("*.py") if "getUpdates" in p.read_text() or "getupdates" in p.read_text().lower()]
    assert hits == [], hits


async def test_phase4_sweeps_are_registered():
    from app.services.sweeps import registry

    assert {"claims.mirror", "claims.settle", "outbox.flush"} <= set(registry.names())


async def test_a_credit_the_matcher_did_not_suggest_can_still_be_attached_by_a_reviewer():
    """The matcher only suggests what fits its rule: same account, same amount
    to the rial, inside five minutes. Everything else it leaves alone, and the
    operator can see both the payment and the money on two different screens
    with no way to join them. Verifying without a transaction would settle the
    payment and leave the credit unclaimed for ever, which is how the books
    drift. The row offers the nearby unspent credits instead."""
    acct = await _account_with_card()
    claim_id = await _claim(account_id=acct, alobot_id=7, at=NOW - dt.timedelta(minutes=40))
    # Twelve minutes late and forty rial short: a real bank fee, outside every
    # rule the matcher has.
    late = await _credit(amount_irr=1249960, at=NOW - dt.timedelta(minutes=28))
    async with async_session_maker() as s:
        await settle.settle(s, now=NOW)

    c = await logged_in("REVIEWER")
    async with c:
        page = await c.get("/payments?tab=review")
        assert page.status_code == 200
        assert f'value="{late}"' in page.text, "the nearby credit is not offered anywhere on the row"
        attached = await c.post(f"/payments/{claim_id}/approve", data={"transaction_id": str(late)}, headers=O)
        assert attached.status_code == 303, attached.text

    async with async_session_maker() as s:
        claim = await s.get(PaymentClaim, claim_id)
        match = (await s.execute(select(ReconciliationMatch).where(ReconciliationMatch.claim_id == claim_id))).scalar_one()
    assert claim.status == "MANUAL_VERIFIED" and claim.verified_by
    assert (match.transaction_id, match.status) == (late, "CONFIRMED")


async def test_a_credit_already_spent_is_not_offered_for_attaching_either():
    a, b, tx = await _ambiguous()
    c = await logged_in("REVIEWER")
    async with c:
        await c.post(f"/payments/{a}/approve", data={"transaction_id": str(tx)}, headers=O)
        page = await c.get("/payments?tab=review")
    assert page.status_code == 200
    assert f'value="{tx}"' not in page.text, "a spent credit is still being offered"
