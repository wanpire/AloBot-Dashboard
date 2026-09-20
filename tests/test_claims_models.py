"""Phase 4 task 1: claims and matches, and the two indexes that carry the money rule."""

import datetime as dt

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

NOW = dt.datetime(2026, 9, 20, tzinfo=dt.timezone.utc)


async def _fixture(session):
    from app.models import Device, PaymentClaim, SmsEvent, TransactionCandidate

    device = Device(code="phone-a", display_name="گوشی")
    session.add(device)
    await session.flush()
    events, txs = [], []
    for i in range(2):
        ev = SmsEvent(device_id=device.id, sender="Bank", body="x", body_hash=f"bh{i}", dedupe_key=f"dk{i}", sms_timestamp=NOW, classification="BANK_TRANSACTION")
        session.add(ev)
        await session.flush()
        tx = TransactionCandidate(sms_event_id=ev.id, direction="CREDIT", amount_irr=1250000, confidence=1.0, parser_id="x", parser_version="1", bank_timestamp=NOW)
        session.add(tx)
        await session.flush()
        txs.append(tx.id)
    claims = []
    for i in range(2):
        c = PaymentClaim(alobot_payment_id=100 + i, telegram_id=1, purpose="purchase", expected_amount_irr=1250000, paid_clicked_at=NOW, ibsng_username=f"alo.{i}")
        session.add(c)
        await session.flush()
        claims.append(c.id)
    await session.commit()
    return claims, txs


async def test_claim_is_unique_per_alobot_payment(session):
    from app.models import PaymentClaim

    session.add(PaymentClaim(alobot_payment_id=7, telegram_id=1, purpose="purchase", expected_amount_irr=10, paid_clicked_at=NOW, ibsng_username="a"))
    await session.commit()
    session.add(PaymentClaim(alobot_payment_id=7, telegram_id=1, purpose="purchase", expected_amount_irr=10, paid_clicked_at=NOW, ibsng_username="a"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_one_transaction_settles_at_most_one_claim(session):
    from app.models import ReconciliationMatch

    (c1, c2), (t1, _) = await _fixture(session)
    session.add(ReconciliationMatch(claim_id=c1, transaction_id=t1, status="AUTO_VERIFIED", reason="UNIQUE_EXACT_MATCH"))
    await session.commit()
    session.add(ReconciliationMatch(claim_id=c2, transaction_id=t1, status="CONFIRMED", reason="operator"))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()
    # A SUGGESTED or REJECTED row on the same transaction is fine - only settling is exclusive.
    session.add(ReconciliationMatch(claim_id=c2, transaction_id=t1, status="SUGGESTED", reason="AMBIGUOUS"))
    await session.commit()


async def test_one_claim_is_settled_at_most_once(session):
    from app.models import ReconciliationMatch

    (c1, _), (t1, t2) = await _fixture(session)
    session.add(ReconciliationMatch(claim_id=c1, transaction_id=t1, status="AUTO_VERIFIED", reason="UNIQUE_EXACT_MATCH"))
    await session.commit()
    session.add(ReconciliationMatch(claim_id=c1, transaction_id=t2, status="CONFIRMED", reason="operator"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_claim_status_and_match_status_are_closed_sets(session):
    (c1, _), (t1, _) = await _fixture(session)
    with pytest.raises(IntegrityError):
        await session.execute(text("UPDATE payment_claims SET status = 'MAYBE' WHERE id = :c"), {"c": c1})
    await session.rollback()
    with pytest.raises(IntegrityError):
        await session.execute(text("INSERT INTO reconciliation_matches (claim_id, transaction_id, status, reason) VALUES (:c, :t, 'KINDA', 'x')"), {"c": c1, "t": t1})
