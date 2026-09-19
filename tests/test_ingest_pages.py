"""Phase 3 tasks 5-9: the pages that run the pipeline - devices, accounts and
cards, banks, transactions, coverage."""

import re

from sqlalchemy import select

from app.db.session import async_session_maker
from app.models import BankSmsPattern, Device, FinancialAccount, PaymentCard, SmsEvent, TransactionCandidate
from app.services import ingest as ingest_service
from app.services.accounts import luhn_ok
from tests.web import logged_in

O = {"Origin": "http://test"}
BANK_SMS = "واریز به حساب 47045299\nمبلغ: 1,250,000 ریال\nمانده: 8,354,098"


async def _device_with_token(code="phone-a"):
    async with async_session_maker() as s:
        d = Device(code=code, display_name="گوشی")
        s.add(d)
        await s.flush()
        token, _ = await ingest_service.issue_credential(s, d)
        await s.commit()
        return d.id, token


async def _ingest(token, message=BANK_SMS, code="phone-a", ts="1789999999000"):
    import httpx

    from app.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        return await c.post("/api/v1/sms", json={"apiKey": token, "deviceId": code, "message": message, "sender": "Bank", "timestamp": ts})


# ── Devices ────────────────────────────────────────────────────────────────


async def test_create_device_shows_the_token_once_and_stores_only_its_hash():
    c = await logged_in("ADMIN")
    async with c:
        r = await c.post("/devices", data={"code": "phone-a", "display_name": "گوشی الف"}, headers=O)
        assert r.status_code == 200, r.text
        token = re.search(r'data-token="([0-9a-f]{64})"', r.text).group(1)
        listing = await c.get("/devices")
    assert token not in listing.text and "phone-a" in listing.text
    async with async_session_maker() as s:
        device = (await s.execute(select(Device).where(Device.code == "phone-a"))).scalar_one()
        assert await ingest_service.authenticate(s, "phone-a", token, now=__import__("datetime").datetime.now(__import__("datetime").timezone.utc)) is not None
        assert device.display_name == "گوشی الف"


async def test_rotate_invalidates_the_old_token_and_revoke_needs_no_second_click_to_bite():
    import datetime as dt

    device_id, old = await _device_with_token()
    now = dt.datetime.now(dt.timezone.utc)
    c = await logged_in("ADMIN")
    async with c:
        rotated = await c.post(f"/devices/{device_id}/rotate", data={"confirm": "1"}, headers=O)
        assert rotated.status_code == 200, rotated.text
        new = re.search(r'data-token="([0-9a-f]{64})"', rotated.text).group(1)
        async with async_session_maker() as s:
            assert await ingest_service.authenticate(s, "phone-a", old, now) is None
            assert await ingest_service.authenticate(s, "phone-a", new, now) is not None
        revoked = await c.post(f"/devices/{device_id}/revoke", data={"confirm": "1"}, headers=O)
        assert revoked.status_code == 303
    async with async_session_maker() as s:
        assert await ingest_service.authenticate(s, "phone-a", new, now) is None


async def test_rotate_and_revoke_refuse_without_the_confirmation_word():
    device_id, _ = await _device_with_token()
    c = await logged_in("ADMIN")
    async with c:
        r = await c.post(f"/devices/{device_id}/rotate", data={}, headers=O)
    assert r.status_code == 400 and "تایید" in r.text


async def test_devices_page_shows_last_seen_and_last_success():
    _, token = await _device_with_token()
    await _ingest(token)
    c = await logged_in("READ_ONLY")
    async with c:
        r = await c.get("/devices")
    assert r.status_code == 200 and "آخرین پیامک موفق" in r.text and "۱۴۰۵" in r.text


# ── Accounts and cards ─────────────────────────────────────────────────────


def test_luhn():
    assert luhn_ok("6104337712345676") is False
    assert luhn_ok("4111111111111111") is True
    assert luhn_ok("123") is False


async def test_create_account_with_identifier_then_a_card_and_refuse_a_bad_card():
    c = await logged_in("ADMIN")
    async with c:
        r = await c.post("/accounts", data={"bank_name": "ملت", "display_name": "ملت اصلی", "owner_label": "پیمان", "identifier_kind": "ACCOUNT_LAST4", "identifier_value": "5299"}, headers=O)
        assert r.status_code == 303, r.text
        async with async_session_maker() as s:
            acct = (await s.execute(select(FinancialAccount))).scalar_one()
        bad = await c.post(f"/accounts/{acct.id}/cards", data={"card_number": "6104337712345676", "holder_name": "x"}, headers=O)
        assert bad.status_code == 400 and "Luhn" in bad.text or "معتبر" in bad.text
        ok = await c.post(f"/accounts/{acct.id}/cards", data={"card_number": "4111 1111 1111 1111", "holder_name": "پیمان"}, headers=O)
        assert ok.status_code == 303, ok.text
        page = await c.get("/accounts")
    assert "4111111111111111" in page.text and "5299" in page.text
    async with async_session_maker() as s:
        card = (await s.execute(select(PaymentCard))).scalar_one()
    assert card.account_id == acct.id and card.holder_name == "پیمان"


async def test_pending_account_can_be_accepted_muted_or_declined_and_the_page_says_what_each_does():
    _, token = await _device_with_token()
    await _ingest(token)
    async with async_session_maker() as s:
        pending = (await s.execute(select(FinancialAccount).where(FinancialAccount.status == "PENDING"))).scalar_one()
    c = await logged_in("ADMIN")
    async with c:
        page = await c.get("/accounts")
        assert "در انتظار تایید" in page.text and "5299" in page.text
        r = await c.post(f"/accounts/{pending.id}/status", data={"status": "ACTIVE"}, headers=O)
        assert r.status_code == 303
        bad = await c.post(f"/accounts/{pending.id}/status", data={"status": "GONE"}, headers=O)
        assert bad.status_code == 400
    async with async_session_maker() as s:
        assert (await s.get(FinancialAccount, pending.id)).status == "ACTIVE"


# ── Banks ──────────────────────────────────────────────────────────────────


async def test_bank_prefixes_resolve_by_longest_match_and_the_test_card_tool_says_so():
    c = await logged_in("ADMIN")
    async with c:
        await c.post("/banks/prefixes", data={"prefix": "6104", "bank_name": "ملت"}, headers=O)
        await c.post("/banks/prefixes", data={"prefix": "610433", "bank_name": "ملت ویژه"}, headers=O)
        r = await c.post("/banks/test-card", data={"card_number": "6104337712345678"}, headers=O)
    assert r.status_code == 200 and "ملت ویژه" in r.text


async def test_pattern_is_created_disabled_tested_in_the_sandbox_and_a_catastrophic_one_is_refused():
    c = await logged_in("ADMIN")
    async with c:
        created = await c.post("/banks/patterns", data={"id": "weird", "bank_name": "بانک عجیب", "detect_re": "WEIRDBANK", "amount_re": r"AMT=(\d+)", "amount_unit": "TOMAN", "direction": "CREDIT", "balance_re": "", "account_re": "", "reference_re": "", "priority": "10"}, headers=O)
        assert created.status_code == 303, created.text
        async with async_session_maker() as s:
            p = await s.get(BankSmsPattern, "weird")
            assert p.enabled is False
        sandbox = await c.post("/banks/patterns/weird/test", data={"sample": "WEIRDBANK AMT=125000"}, headers=O)
        assert sandbox.status_code == 200 and "۱٬۲۵۰٬۰۰۰" in sandbox.text
        bad = await c.post("/banks/patterns", data={"id": "boom", "bank_name": "x", "detect_re": "(a+)+$", "amount_re": r"(\d+)", "amount_unit": "IRR", "direction": "CREDIT", "priority": "10"}, headers=O)
        assert bad.status_code == 400 and "کند" in bad.text
        enabled = await c.post("/banks/patterns/weird/enabled", data={"enabled": "1"}, headers=O)
        assert enabled.status_code == 303
    async with async_session_maker() as s:
        assert (await s.get(BankSmsPattern, "weird")).enabled is True


async def test_test_sms_tool_runs_the_whole_parser():
    c = await logged_in("ADMIN")
    async with c:
        r = await c.post("/banks/test-sms", data={"sample": BANK_SMS}, headers=O)
    assert r.status_code == 200 and "BANK_TRANSACTION" in r.text and "۱٬۲۵۰٬۰۰۰" in r.text


# ── Transactions and coverage ──────────────────────────────────────────────


async def test_transactions_page_lists_filters_assigns_and_declines():
    _, token = await _device_with_token()
    await _ingest(token)
    await _ingest(token, message="برداشت از کارت 6104****1234\nمبلغ: 300,000 ریال", ts="1789999998000")
    async with async_session_maker() as s:
        acct = FinancialAccount(bank_name="ملت", display_name="ملت اصلی", status="ACTIVE")
        s.add(acct)
        await s.commit()
        credit = (await s.execute(select(TransactionCandidate).where(TransactionCandidate.direction == "CREDIT"))).scalar_one()
    c = await logged_in("ADMIN")
    async with c:
        page = await c.get("/transactions?direction=CREDIT")
        assert page.status_code == 200 and "۱٬۲۵۰٬۰۰۰" in page.text and "۳۰۰٬۰۰۰" not in page.text
        assigned = await c.post(f"/transactions/{credit.id}/account", data={"account_id": str(acct.id)}, headers=O)
        assert assigned.status_code == 303
        declined = await c.post(f"/transactions/{credit.id}/disposition", data={"disposition": "DECLINED_INCOME", "note": "قرض"}, headers=O)
        assert declined.status_code == 303
    async with async_session_maker() as s:
        tx = await s.get(TransactionCandidate, credit.id)
    assert tx.account_id == acct.id and tx.disposition == "DECLINED_INCOME" and tx.disposition_note == "قرض"


async def test_unparsed_view_and_reparse_after_a_pattern_is_enabled():
    _, token = await _device_with_token()
    await _ingest(token, message="WEIRDBANK AMT=125000 BAL=800000 ACC=5299")
    c = await logged_in("ADMIN")
    async with c:
        unparsed = await c.get("/transactions?view=unparsed")
        assert "WEIRDBANK" in unparsed.text
        await c.post("/banks/patterns", data={"id": "weird", "bank_name": "بانک عجیب", "detect_re": "WEIRDBANK", "amount_re": r"AMT=(\d+)", "amount_unit": "TOMAN", "direction": "CREDIT", "balance_re": r"BAL=(\d+)", "account_re": r"ACC=(\d+)", "priority": "10"}, headers=O)
        await c.post("/banks/patterns/weird/enabled", data={"enabled": "1"}, headers=O)
        dry = await c.post("/transactions/reparse", data={"mode": "dry-run"}, headers=O)
        assert dry.status_code == 200 and "۱" in dry.text
        async with async_session_maker() as s:
            assert (await s.execute(select(TransactionCandidate))).scalars().all() == []
        applied = await c.post("/transactions/reparse", data={"mode": "apply"}, headers=O)
        assert applied.status_code == 200
    async with async_session_maker() as s:
        tx = (await s.execute(select(TransactionCandidate))).scalar_one()
        ev = await s.get(SmsEvent, tx.sms_event_id)
    assert tx.amount_irr == 1250000 and tx.bank_name == "بانک عجیب" and ev.classification == "BANK_TRANSACTION"


async def test_coverage_counts_parsed_and_unparsed_per_sender():
    _, token = await _device_with_token()
    await _ingest(token)
    await _ingest(token, message="سلام فردا جلسه", ts="1789999990000")
    c = await logged_in("READ_ONLY")
    async with c:
        r = await c.get("/transactions?view=coverage")
    assert r.status_code == 200 and "Bank" in r.text and "۵۰٪" in r.text
