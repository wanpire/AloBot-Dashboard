"""Phase 4 tasks 2 and 4: claims mirrored from AloBot's pending card payments,
and the settle sweep that turns bank credits into verifications."""

import asyncio
import datetime as dt

import pytest
from sqlalchemy import select, text

from app.alobot.link import link
from app.alobot.seed import seed_alobot_copy
from app.db.session import async_session_maker
from app.models import Device, FinancialAccount, FinancialAccountIdentifier, PaymentCard, PaymentClaim, ReconciliationMatch
from app.services import claims as claims_service
from app.services import ingest as ingest_service
from app.services import settle as settle_service
from tests.alobot_seed import run as alobot_run
from tests.conftest import ALOBOT_ADMIN_URL

NOW = dt.datetime(2026, 9, 20, 12, 0, tzinfo=dt.timezone.utc)


@pytest.fixture
async def seeded():
    counts = await seed_alobot_copy(ALOBOT_ADMIN_URL, customers=30, seed=7)
    await link.connect()
    return counts


async def _account_with_card(card="6037991234567893", last4="5299"):
    async with async_session_maker() as s:
        acct = FinancialAccount(bank_name="ملی", display_name="ملی اصلی", status="ACTIVE")
        s.add(acct)
        await s.flush()
        s.add(FinancialAccountIdentifier(account_id=acct.id, kind="ACCOUNT_LAST4", value=last4))
        s.add(PaymentCard(account_id=acct.id, card_number=card, holder_name="آلو"))
        await s.commit()
        return acct.id


async def test_mirror_creates_one_claim_per_pending_card_payment_and_is_idempotent(seeded):
    acct_id = await _account_with_card()
    async with async_session_maker() as s:
        first = await claims_service.mirror_claims(s, now=NOW)
        second = await claims_service.mirror_claims(s, now=NOW)
        rows = (await s.execute(select(PaymentClaim))).scalars().all()
    pending = 5  # the seed's pending card payments
    assert first["created"] == pending and second["created"] == 0
    assert len(rows) == pending
    claim = rows[0]
    assert claim.target_card_number == "6037991234567893" and claim.target_account_id == acct_id
    assert claim.expected_amount_irr % 10 == 0 and claim.paid_clicked_at is not None and claim.invoice_code


async def test_mirror_tracks_alobots_own_decision_without_touching_ours(seeded):
    await _account_with_card()
    async with async_session_maker() as s:
        await claims_service.mirror_claims(s, now=NOW)
        claim = (await s.execute(select(PaymentClaim).order_by(PaymentClaim.id))).scalars().first()
    await alobot_run("UPDATE payments SET status='approved', resolved_at=now() WHERE id = :id", id=claim.alobot_payment_id)
    async with async_session_maker() as s:
        result = await claims_service.mirror_claims(s, now=NOW)
        refreshed = await s.get(PaymentClaim, claim.id)
    assert result["updated"] == 1
    assert refreshed.alobot_status == "approved" and refreshed.alobot_resolved_at is not None and refreshed.status == "PENDING"


async def test_mirror_without_a_registered_card_leaves_the_target_unresolved(seeded):
    async with async_session_maker() as s:
        await claims_service.mirror_claims(s, now=NOW)
        claim = (await s.execute(select(PaymentClaim))).scalars().first()
    assert claim.target_account_id is None and claim.target_card_number == "6037991234567893"


async def test_mirror_resolves_the_target_once_the_card_is_registered_later(seeded):
    async with async_session_maker() as s:
        await claims_service.mirror_claims(s, now=NOW)
        assert all(c.target_account_id is None for c in (await s.execute(select(PaymentClaim))).scalars().all())
    acct_id = await _account_with_card()
    async with async_session_maker() as s:
        result = await claims_service.mirror_claims(s, now=NOW)
        claims = (await s.execute(select(PaymentClaim))).scalars().all()
    assert result["resolved"] == len(claims) and all(c.target_account_id == acct_id for c in claims)


async def test_mirror_is_a_no_op_when_the_link_is_unavailable(monkeypatch):
    monkeypatch.setattr(link, "problems", ["down"])
    async with async_session_maker() as s:
        assert await claims_service.mirror_claims(s, now=NOW) == {"created": 0, "updated": 0, "resolved": 0, "skipped": "alobot_unavailable"}


# ── settle ─────────────────────────────────────────────────────────────────


async def _claim(amount_irr=1250000, at=NOW, alobot_id=None, account_id=None, telegram_id=100000001):
    async with async_session_maker() as s:
        claim = PaymentClaim(
            alobot_payment_id=alobot_id or int(at.timestamp()) % 1_000_000 + amount_irr, telegram_id=telegram_id, purpose="purchase",
            expected_amount_irr=amount_irr, ibsng_username="alo.x", target_card_number="6037991234567893", target_account_id=account_id,
            paid_clicked_at=at, receipt_file_id="AgAC",
        )
        s.add(claim)
        await s.commit()
        return claim.id


async def _credit(amount_irr=1250000, at=NOW + dt.timedelta(minutes=2), last4="5299"):
    async with async_session_maker() as s:
        device = (await s.execute(select(Device).where(Device.code == "phone-a"))).scalar_one_or_none()
        if device is None:
            device = Device(code="phone-a", display_name="گوشی")
            s.add(device)
            await s.flush()
        msg = f"واریز به حساب 4704{last4}\nمبلغ: {amount_irr:,} ریال\nمانده: 8,354,098\nشماره پیگیری: {int(at.timestamp())}"
        outcome = await ingest_service.ingest(s, device, sender="Bank", message=msg, timestamp_raw=str(int(at.timestamp() * 1000)), checksum=None, now=at)
        return outcome.transaction_id


async def test_settle_auto_verifies_an_isolated_pair_and_records_the_match():
    acct = await _account_with_card()
    claim_id = await _claim(account_id=acct)
    tx_id = await _credit()
    async with async_session_maker() as s:
        result = await settle_service.settle(s, now=NOW + dt.timedelta(minutes=3))
        claim = await s.get(PaymentClaim, claim_id)
        match = (await s.execute(select(ReconciliationMatch))).scalar_one()
    assert result["auto_verified"] == 1
    assert claim.status == "AUTO_VERIFIED" and claim.verified_by == "matcher" and claim.verified_at is not None and claim.suspect_reason is None
    assert (match.claim_id, match.transaction_id, match.status, match.reason) == (claim_id, tx_id, "AUTO_VERIFIED", "UNIQUE_EXACT_MATCH")
    assert match.time_delta_seconds == 120


async def test_settle_is_idempotent_and_a_consumed_credit_cannot_settle_a_second_claim():
    acct = await _account_with_card()
    first = await _claim(account_id=acct, alobot_id=1)
    await _credit()
    async with async_session_maker() as s:
        await settle_service.settle(s, now=NOW + dt.timedelta(minutes=3))
        again = await settle_service.settle(s, now=NOW + dt.timedelta(minutes=4))
    assert again["auto_verified"] == 0
    second = await _claim(account_id=acct, alobot_id=2, at=NOW + dt.timedelta(minutes=1))
    async with async_session_maker() as s:
        await settle_service.settle(s, now=NOW + dt.timedelta(minutes=20))
        c2 = await s.get(PaymentClaim, second)
    assert c2.status == "PENDING" and c2.suspect_reason == "NO_TRANSACTION_AFTER_10M"


async def test_two_claims_for_one_credit_both_go_to_review_with_suggested_matches():
    acct = await _account_with_card()
    a = await _claim(account_id=acct, alobot_id=1)
    b = await _claim(account_id=acct, alobot_id=2, at=NOW + dt.timedelta(minutes=1))
    tx = await _credit()
    async with async_session_maker() as s:
        result = await settle_service.settle(s, now=NOW + dt.timedelta(minutes=3))
        ca, cb = await s.get(PaymentClaim, a), await s.get(PaymentClaim, b)
        suggested = (await s.execute(select(ReconciliationMatch).where(ReconciliationMatch.status == "SUGGESTED"))).scalars().all()
    assert result["auto_verified"] == 0 and result["suggested"] == 2
    assert ca.suspect_reason == "AMBIGUOUS_CLAIMS" and cb.suspect_reason == "AMBIGUOUS_CLAIMS"
    assert ca.candidate_transaction_ids == [tx] and cb.candidate_transaction_ids == [tx]
    assert {(m.claim_id, m.transaction_id) for m in suggested} == {(a, tx), (b, tx)}


async def test_waiting_claims_are_marked_as_waiting_not_suspect():
    acct = await _account_with_card()
    claim_id = await _claim(account_id=acct)
    async with async_session_maker() as s:
        await settle_service.settle(s, now=NOW + dt.timedelta(minutes=2))
        claim = await s.get(PaymentClaim, claim_id)
    assert claim.status == "PENDING" and claim.suspect_reason == "AWAITING_BANK_SMS"


async def test_two_concurrent_settles_produce_exactly_one_settling_match():
    acct = await _account_with_card()
    claim_id = await _claim(account_id=acct)
    await _credit()

    async def run():
        async with async_session_maker() as s:
            return await settle_service.settle(s, now=NOW + dt.timedelta(minutes=3))

    results = await asyncio.gather(run(), run())
    async with async_session_maker() as s:
        settling = (await s.execute(select(ReconciliationMatch).where(ReconciliationMatch.status.in_(("AUTO_VERIFIED", "CONFIRMED"))))).scalars().all()
        claim = await s.get(PaymentClaim, claim_id)
    assert len(settling) == 1 and claim.status == "AUTO_VERIFIED"
    assert sum(r["auto_verified"] for r in results) == 1


async def test_settle_enqueues_a_customer_notice_only_when_customer_notifications_are_on():
    from app.models import BotNotification
    from app.services import settings as settings_service

    acct = await _account_with_card()
    claim_id = await _claim(account_id=acct)
    await _credit()
    async with async_session_maker() as s:
        await settle_service.settle(s, now=NOW + dt.timedelta(minutes=3))
        assert (await s.execute(select(BotNotification))).scalars().all() == []
    acct2 = acct
    claim2 = await _claim(account_id=acct2, alobot_id=99, amount_irr=990000, at=NOW + dt.timedelta(minutes=10))
    await _credit(amount_irr=990000, at=NOW + dt.timedelta(minutes=12))
    async with async_session_maker() as s:
        await settings_service.set_many(s, {("notify", "customers_enabled"): True}, actor_email="t", actor_role="ADMIN")
        await settle_service.settle(s, now=NOW + dt.timedelta(minutes=13))
        rows = (await s.execute(select(BotNotification))).scalars().all()
    assert [r.dedupe_key for r in rows] == [f"verify:{claim2}"] and rows[0].chat_id == 100000001
