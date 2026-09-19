"""Phase 3 task 1: the tables behind the SMS pipeline and the guarantees they carry."""

import datetime as dt

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

NOW = dt.datetime(2026, 9, 20, tzinfo=dt.timezone.utc)


async def test_phase3_tables_exist(session):
    rows = await session.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))
    names = {r[0] for r in rows}
    for t in ("devices", "device_credentials", "financial_accounts", "financial_account_identifiers", "payment_cards",
              "bank_card_prefixes", "bank_sms_patterns", "sms_events", "transaction_candidates"):
        assert t in names, t


async def test_one_active_credential_per_device(session):
    from app.models import Device, DeviceCredential

    device = Device(code="phone-a", display_name="گوشی الف")
    session.add(device)
    await session.flush()
    session.add(DeviceCredential(device_id=device.id, token_hash="h1", token_prefix="aaaa", status="ACTIVE"))
    session.add(DeviceCredential(device_id=device.id, token_hash="h2", token_prefix="bbbb", status="REVOKED", revoked_at=NOW))
    await session.commit()
    session.add(DeviceCredential(device_id=device.id, token_hash="h3", token_prefix="cccc", status="ACTIVE"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_sms_event_dedupe_key_is_unique(session):
    from app.models import Device, SmsEvent

    device = Device(code="phone-a", display_name="گوشی الف")
    session.add(device)
    await session.flush()
    session.add(SmsEvent(device_id=device.id, sender="Bank", body="x", body_hash="bh", dedupe_key="dk", sms_timestamp=NOW, classification="UNKNOWN"))
    await session.commit()
    session.add(SmsEvent(device_id=device.id, sender="Bank", body="y", body_hash="bh2", dedupe_key="dk", sms_timestamp=NOW, classification="UNKNOWN"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_one_transaction_per_sms_event_and_closed_sets(session):
    from app.models import Device, SmsEvent, TransactionCandidate

    device = Device(code="phone-a", display_name="گوشی الف")
    session.add(device)
    await session.flush()
    ev = SmsEvent(device_id=device.id, sender="Bank", body="x", body_hash="bh", dedupe_key="dk", sms_timestamp=NOW, classification="BANK_TRANSACTION")
    session.add(ev)
    await session.flush()
    ev_id = ev.id
    session.add(TransactionCandidate(sms_event_id=ev_id, direction="CREDIT", amount_irr=1000, confidence=1.0, parser_id="x", parser_version="1"))
    await session.commit()
    session.add(TransactionCandidate(sms_event_id=ev_id, direction="CREDIT", amount_irr=1000, confidence=1.0, parser_id="x", parser_version="1"))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()
    with pytest.raises(IntegrityError):
        await session.execute(text("INSERT INTO transaction_candidates (sms_event_id, direction, confidence, parser_id, parser_version) VALUES (:e, 'SIDEWAYS', 1, 'x', '1')"), {"e": ev_id})
    await session.rollback()
    with pytest.raises(IntegrityError):
        await session.execute(text("UPDATE transaction_candidates SET amount_irr = -5 WHERE sms_event_id = :e"), {"e": ev_id})


async def test_payment_card_number_is_unique_and_sixteen_digits(session):
    from app.models import FinancialAccount, PaymentCard

    acct = FinancialAccount(bank_name="ملت", display_name="ملت اصلی", status="ACTIVE")
    session.add(acct)
    await session.flush()
    acct_id = acct.id
    session.add(PaymentCard(account_id=acct_id, card_number="6104337712345678", holder_name="آلو"))
    await session.commit()
    session.add(PaymentCard(account_id=acct_id, card_number="6104337712345678", holder_name="دوباره"))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()
    session.add(PaymentCard(account_id=acct_id, card_number="123", holder_name="کوتاه"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_account_identifier_is_unique_per_kind_and_value(session):
    from app.models import FinancialAccount, FinancialAccountIdentifier

    a = FinancialAccount(bank_name="ملت", display_name="الف", status="ACTIVE")
    b = FinancialAccount(bank_name="ملت", display_name="ب", status="ACTIVE")
    session.add_all([a, b])
    await session.flush()
    session.add(FinancialAccountIdentifier(account_id=a.id, kind="CARD_LAST4", value="5678"))
    await session.commit()
    session.add(FinancialAccountIdentifier(account_id=b.id, kind="CARD_LAST4", value="5678"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_bank_prefix_must_be_four_to_eight_digits(session):
    from app.models import BankCardPrefix

    session.add(BankCardPrefix(prefix="610433", bank_name="ملت"))
    await session.commit()
    session.add(BankCardPrefix(prefix="61", bank_name="کوتاه"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_sms_pattern_defaults_to_disabled(session):
    from app.models import BankSmsPattern

    p = BankSmsPattern(id="mellat-credit", bank_name="ملت", detect_re="واریز", amount_re=r"مبلغ\s*([\d,]+)")
    session.add(p)
    await session.commit()
    await session.refresh(p)
    assert p.enabled is False and p.priority == 100 and p.amount_unit == "IRR" and p.direction == "CREDIT"
