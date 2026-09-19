"""Phase 3 tasks 2 and 4: the only public door, and what happens behind it."""

import datetime as dt
import json

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import async_session_maker
from app.main import app
from app.models import Device, FinancialAccount, FinancialAccountIdentifier, SmsEvent, TransactionCandidate
from app.services import ingest as ingest_service

BANK_SMS = "واریز به حساب 47045299\nمبلغ: 1,250,000 ریال\nمانده: 8,354,098\nشماره پیگیری: 123456\n1405/06/28-14:02"


def client(**kwargs):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", **kwargs)


async def make_device(code="phone-a", active=True):
    async with async_session_maker() as s:
        device = Device(code=code, display_name="گوشی الف", is_active=active)
        s.add(device)
        await s.flush()
        token, _ = await ingest_service.issue_credential(s, device)
        await s.commit()
        return device.id, token


def payload(token, code="phone-a", message=BANK_SMS, sender="Bank", timestamp="1789999999000", **extra):
    return {"apiKey": token, "deviceId": code, "message": message, "sender": sender, "timestamp": timestamp, **extra}


async def post(c, body, **headers):
    return await c.post("/api/v1/sms", json=body, headers=headers)


async def test_unknown_device_bad_key_inactive_device_and_revoked_credential_all_get_the_same_401():
    _, token = await make_device()
    async with client() as c:
        unknown = await post(c, payload(token, code="nope"))
        bad = await post(c, payload("x" * 64))
        await make_device(code="off", active=False)
        _, off_token = await make_device(code="off2", active=False)
        inactive = await post(c, payload(off_token, code="off2"))
    async with async_session_maker() as s:
        device = (await s.execute(select(Device).where(Device.code == "phone-a"))).scalar_one()
        await ingest_service.revoke_credentials(s, device.id)
    async with client() as c:
        revoked = await post(c, payload(token))
    for r in (unknown, bad, inactive, revoked):
        assert r.status_code == 401 and r.json() == {"error": "unauthorized"}
    async with async_session_maker() as s:
        device = (await s.execute(select(Device).where(Device.code == "phone-a"))).scalar_one()
        assert device.last_auth_failure_at is not None


async def test_oversized_body_is_refused_before_it_is_parsed(monkeypatch):
    monkeypatch.setattr(get_settings(), "ingest_max_body_bytes", 512)
    _, token = await make_device()
    async with client() as c:
        r = await c.post("/api/v1/sms", content=json.dumps(payload(token, message="x" * 2000)), headers={"content-type": "application/json"})
    assert r.status_code == 413


async def test_bad_json_and_missing_fields_are_400_not_500():
    _, token = await make_device()
    async with client() as c:
        bad = await c.post("/api/v1/sms", content=b"{not json", headers={"content-type": "application/json"})
        missing = await c.post("/api/v1/sms", json={"apiKey": token, "deviceId": "phone-a"})
    assert bad.status_code == 400 and missing.status_code == 400


async def test_a_bank_sms_becomes_an_event_and_a_transaction():
    device_id, token = await make_device()
    async with client() as c:
        r = await post(c, payload(token))
    body = r.json()
    assert r.status_code == 200, body
    # Not actionable: nobody registered ****5299, so it lands on a PENDING account.
    assert body["ok"] and body["duplicate"] is False and body["classification"] == "BANK_TRANSACTION" and body["actionable"] is False
    async with async_session_maker() as s:
        ev = (await s.execute(select(SmsEvent))).scalar_one()
        tx = (await s.execute(select(TransactionCandidate))).scalar_one()
        device = await s.get(Device, device_id)
        pending = (await s.execute(select(FinancialAccount))).scalar_one()
    assert pending.status == "PENDING" and tx.account_id == pending.id
    assert ev.sender == "Bank" and "1,250,000" in ev.body and ev.sms_timestamp == dt.datetime.fromtimestamp(1789999999, tz=dt.timezone.utc)
    assert (tx.direction, tx.amount_irr, tx.balance_irr, tx.account_hint, tx.reference) == ("CREDIT", 1250000, 8354098, "5299", "123456")
    assert tx.bank_timestamp == ev.sms_timestamp
    assert device.last_seen_at is not None and device.last_success_at is not None


async def test_redelivery_is_acknowledged_without_a_second_row():
    _, token = await make_device()
    async with client() as c:
        first = await post(c, payload(token))
        again = await post(c, payload(token))
        whitespace = await post(c, payload(token, message=BANK_SMS.replace("\n", "  \n ")))
    assert first.json()["duplicate"] is False
    assert again.status_code == 200 and again.json()["duplicate"] is True and again.json()["eventId"] == first.json()["eventId"]
    assert whitespace.json()["duplicate"] is True
    async with async_session_maker() as s:
        assert len((await s.execute(select(SmsEvent))).scalars().all()) == 1


async def test_otp_is_stored_redacted_and_makes_no_transaction():
    _, token = await make_device()
    async with client() as c:
        r = await post(c, payload(token, message="رمز پویا: 482913\nمبلغ: 1,250,000 ریال"))
    assert r.json()["classification"] == "OTP" and r.json()["actionable"] is False
    async with async_session_maker() as s:
        ev = (await s.execute(select(SmsEvent))).scalar_one()
        assert "482913" not in ev.body and "[OTP]" in ev.body
        assert (await s.execute(select(TransactionCandidate))).scalars().all() == []


async def test_per_device_rate_limit(monkeypatch):
    monkeypatch.setattr(get_settings(), "ingest_device_rate_per_minute", 3)
    _, token = await make_device()
    async with client() as c:
        codes = [(await post(c, payload(token, message=f"پیام {i}"))).status_code for i in range(5)]
    assert codes == [200, 200, 200, 429, 429]


async def test_per_ip_rate_limit_only_behind_the_trusted_header(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ingest_ip_rate_per_minute", 2)
    monkeypatch.setattr(settings, "trusted_proxy_ip_header", "")
    _, token = await make_device()
    async with client() as c:
        without = [(await post(c, payload(token, message=f"m{i}"), **{"X-Forwarded-For": "1.1.1.1"})).status_code for i in range(4)]
        monkeypatch.setattr(settings, "trusted_proxy_ip_header", "X-Real-IP")
        limited = [(await post(c, payload(token, message=f"n{i}"), **{"X-Real-IP": "2.2.2.2"})).status_code for i in range(4)]
    assert 429 not in without
    assert limited == [200, 200, 429, 429]


async def test_known_identifier_resolves_the_account_and_unknown_makes_a_pending_one():
    _, token = await make_device()
    async with async_session_maker() as s:
        acct = FinancialAccount(bank_name="کشاورزی", display_name="کشاورزی مامان", status="ACTIVE")
        s.add(acct)
        await s.flush()
        s.add(FinancialAccountIdentifier(account_id=acct.id, kind="ACCOUNT_LAST4", value="5299"))
        await s.commit()
        known_id = acct.id
    async with client() as c:
        known = await post(c, payload(token))
        unknown = await post(c, payload(token, message=BANK_SMS.replace("47045299", "99887766")))
    async with async_session_maker() as s:
        txs = {t.account_hint: t for t in (await s.execute(select(TransactionCandidate))).scalars().all()}
        pending = (await s.execute(select(FinancialAccount).where(FinancialAccount.status == "PENDING"))).scalar_one()
    assert txs["5299"].account_id == known_id and known.json()["actionable"] is True
    assert txs["7766"].account_id == pending.id and unknown.json()["actionable"] is False
    assert "7766" in pending.display_name


async def test_balance_chain_infers_the_owner_of_an_unknown_identifier():
    _, token = await make_device()
    async with async_session_maker() as s:
        acct = FinancialAccount(bank_name="کشاورزی", display_name="کشاورزی مامان", status="ACTIVE")
        s.add(acct)
        await s.flush()
        s.add(FinancialAccountIdentifier(account_id=acct.id, kind="ACCOUNT_LAST4", value="5299"))
        await s.commit()
        acct_id = acct.id
    earlier = "واریز به حساب 47045299\nمبلغ: 100,000 ریال\nمانده: 2,854,098"
    later = "واریز به حساب 47045299 پول\nمبلغ: 5,500,000 ریال\nمانده: 8,354,098".replace("47045299", "99887766")
    async with client() as c:
        await post(c, payload(token, message=earlier, timestamp="1789999000000"))
        await post(c, payload(token, message=later, timestamp="1789999999000"))
    async with async_session_maker() as s:
        tx = (await s.execute(select(TransactionCandidate).where(TransactionCandidate.account_hint == "7766"))).scalar_one()
        pending = await s.get(FinancialAccount, tx.account_id)
    assert tx.inferred_account_id == acct_id
    assert pending.inferred_from_account_id == acct_id


@pytest.mark.parametrize(("raw", "expected"), [
    ("1789999999000", dt.datetime(2026, 9, 21, 14, 13, 19, tzinfo=dt.timezone.utc)),
    ("1789999999", dt.datetime(2026, 9, 21, 14, 13, 19, tzinfo=dt.timezone.utc)),
    ("2026-09-21T14:13:19Z", dt.datetime(2026, 9, 21, 14, 13, 19, tzinfo=dt.timezone.utc)),
    ("2026-09-21T17:43:19+03:30", dt.datetime(2026, 9, 21, 14, 13, 19, tzinfo=dt.timezone.utc)),
])
def test_timestamps_from_android_and_iphone_shortcuts_are_understood(raw, expected):
    assert ingest_service.parse_timestamp(raw) == expected


def test_garbage_timestamp_yields_none_so_the_caller_can_fall_back():
    assert ingest_service.parse_timestamp("yesterday") is None
