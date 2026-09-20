"""Phase 4 task 6, service half: what an operator may do to a claim, each a
conditional transition that refuses when the claim has moved on."""

import datetime as dt

import pytest
from sqlalchemy import select

from app.db.session import async_session_maker
from app.models import BotNotification, FinancialAccount, PaymentClaim, ReconciliationMatch
from app.services import review
from app.services import settings as settings_service
from tests.test_claims_sweeps import _account_with_card, _claim, _credit
from tests.web import make_operator

NOW = dt.datetime(2026, 9, 20, 12, 0, tzinfo=dt.timezone.utc)


async def _setup():
    acct = await _account_with_card()
    claim_id = await _claim(account_id=acct)
    tx_id = await _credit()
    actor = await make_operator(email="rev@x.io", role="REVIEWER")
    return claim_id, tx_id, actor


async def test_approve_with_a_transaction_confirms_the_match_and_settles_the_claim():
    claim_id, tx_id, actor = await _setup()
    async with async_session_maker() as s:
        await review.approve_with_transaction(s, actor, claim_id, tx_id, now=NOW)
        claim = await s.get(PaymentClaim, claim_id)
        match = (await s.execute(select(ReconciliationMatch))).scalar_one()
    assert claim.status == "MANUAL_VERIFIED" and claim.verified_by == "rev@x.io" and claim.decided_by == "rev@x.io"
    assert (match.status, match.transaction_id, match.decided_by) == ("CONFIRMED", tx_id, "rev@x.io")


async def test_a_consumed_transaction_cannot_be_approved_twice():
    claim_id, tx_id, actor = await _setup()
    other = await _claim(account_id=None, alobot_id=77, at=NOW + dt.timedelta(minutes=1))
    async with async_session_maker() as s:
        await review.approve_with_transaction(s, actor, claim_id, tx_id, now=NOW)
    async with async_session_maker() as s:
        with pytest.raises(review.ReviewError, match="قبلاً"):
            await review.approve_with_transaction(s, actor, other, tx_id, now=NOW)
        with pytest.raises(review.ReviewError, match="تصمیم"):
            await review.approve_with_transaction(s, actor, claim_id, tx_id, now=NOW)


async def test_manual_verification_needs_a_note_and_records_no_bank_backing():
    claim_id, _, actor = await _setup()
    async with async_session_maker() as s:
        with pytest.raises(review.ReviewError):
            await review.verify_manually(s, actor, claim_id, note="  ", now=NOW)
        await review.verify_manually(s, actor, claim_id, note="رسید دیده شد", now=NOW)
        claim = await s.get(PaymentClaim, claim_id)
        matches = (await s.execute(select(ReconciliationMatch).where(ReconciliationMatch.status.in_(("CONFIRMED", "AUTO_VERIFIED"))))).scalars().all()
    assert claim.status == "MANUAL_VERIFIED" and claim.note == "رسید دیده شد" and matches == []


async def test_reject_fake_park_unpark_and_reopen():
    claim_id, tx_id, actor = await _setup()
    async with async_session_maker() as s:
        await review.park(s, actor, claim_id, now=NOW)
        assert (await s.get(PaymentClaim, claim_id)).parked_at is not None
        await review.unpark(s, actor, claim_id, now=NOW)
        assert (await s.get(PaymentClaim, claim_id)).parked_at is None
        await review.reject(s, actor, claim_id, note="مبلغ نمی‌خواند", now=NOW)
        assert (await s.get(PaymentClaim, claim_id)).status == "REJECTED"
        await review.reopen(s, actor, claim_id, now=NOW)
        assert (await s.get(PaymentClaim, claim_id)).status == "PENDING"
        await review.approve_with_transaction(s, actor, claim_id, tx_id, now=NOW)
        await review.reopen(s, actor, claim_id, now=NOW)
        claim = await s.get(PaymentClaim, claim_id)
        reopened = (claim.status, claim.verified_at)
        match_status = (await s.execute(select(ReconciliationMatch))).scalar_one().status
        await review.mark_fake(s, actor, claim_id, note="رسید فتوشاپ", now=NOW)
        assert (await s.get(PaymentClaim, claim_id)).status == "FAKE"
    assert reopened == ("PENDING", None)
    assert match_status == "REJECTED"  # reopening frees the transaction


async def test_message_needs_customer_notifications_on_and_then_queues_once():
    claim_id, _, actor = await _setup()
    async with async_session_maker() as s:
        with pytest.raises(review.ReviewError, match="خاموش"):
            await review.message(s, actor, claim_id, "contact_support", now=NOW)
        await settings_service.set_many(s, {("notify", "customers_enabled"): True}, actor_email="t", actor_role="ADMIN")
        await review.message(s, actor, claim_id, "contact_support", now=NOW)
        with pytest.raises(review.ReviewError):
            await review.message(s, actor, claim_id, "no_such_template", now=NOW)
        claim = await s.get(PaymentClaim, claim_id)
        rows = (await s.execute(select(BotNotification))).scalars().all()
    assert claim.messaged_at is not None and claim.messaged_template == "contact_support"
    assert len(rows) == 1 and rows[0].chat_id == 100000001 and rows[0].dedupe_key == f"msg:{claim_id}:contact_support"


async def test_tab_counts_sort_claims_into_the_queues():
    acct = await _account_with_card()
    waiting = await _claim(account_id=acct, alobot_id=1)
    review_me = await _claim(account_id=acct, alobot_id=2, at=NOW - dt.timedelta(minutes=30))
    parked = await _claim(account_id=acct, alobot_id=3, at=NOW - dt.timedelta(minutes=40))
    actor = await make_operator()
    from app.services import settle

    async with async_session_maker() as s:
        await settle.settle(s, now=NOW + dt.timedelta(minutes=2))
        await review.park(s, actor, parked, now=NOW)
        counts = await review.tab_counts(s)
    assert counts["review"] == 1 and counts["waiting"] == 1 and counts["parked"] == 1 and counts["all"] == 3


async def test_a_credit_that_already_settled_a_claim_is_no_longer_offered_to_another():
    """The database refuses the second settlement, so the screen must not
    invite it: the listing marks the credit as spent and the route still
    refuses if someone posts the form anyway."""
    acct = await _account_with_card()
    first = await _claim(account_id=acct, alobot_id=1)
    second = await _claim(account_id=acct, alobot_id=2, at=NOW + dt.timedelta(minutes=1))
    tx = await _credit()
    actor = await make_operator()
    from app.services import settle

    async with async_session_maker() as s:
        await settle.settle(s, now=NOW + dt.timedelta(minutes=3))  # ambiguous: both suggested
        listing = await review.list_claims(s, tab="review")
        assert listing["spent_transactions"] == set(), "nothing is settled yet"
        await review.approve_with_transaction(s, actor, first, tx, now=NOW + dt.timedelta(minutes=4))

    async with async_session_maker() as s:
        listing = await review.list_claims(s, tab="review")
        assert listing["spent_transactions"] == {tx}
        with pytest.raises(review.ReviewError, match="تسویه"):
            await review.approve_with_transaction(s, actor, second, tx, now=NOW + dt.timedelta(minutes=5))
