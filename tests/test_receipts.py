"""Phase 7 item 2: showing the receipt photo in the review queue.

A receipt exists in exactly one place: as a Telegram `file_id` on AloBot's
`payments` row. AloBot never downloads the bytes - there is no `getFile`
anywhere in its source - it just re-sends the photo to admins by id. A
`file_id` only works for the bot that received it, so showing one here means
calling `getFile` with AloBot's own token and fetching the file.

Nothing is stored. The image is fetched when an operator opens it, held
briefly in memory so a page refresh does not re-fetch, and never written to
disk: it is a customer's bank receipt.
"""

from __future__ import annotations

import datetime as dt

import httpx
import pytest
import respx

from app.core.config import get_settings
from app.db.session import async_session_maker
from app.models import PaymentClaim
from app.services import receipts
from tests.test_claims_sweeps import _account_with_card, _claim
from tests.web import logged_in

NOW = dt.datetime(2026, 9, 20, 12, 0, tzinfo=dt.timezone.utc)
TOKEN = "777:ALOBOT-TOKEN"
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452")


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setattr(get_settings(), "alobot_bot_token", TOKEN)
    receipts.cache_clear()
    yield


def _telegram(file_id: str = "AgAC", path: str = "photos/file_1.jpg", content: bytes = PNG) -> None:
    respx.get(f"https://api.telegram.org/bot{TOKEN}/getFile", params={"file_id": file_id}).mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"file_id": file_id, "file_path": path}})
    )
    respx.get(f"https://api.telegram.org/file/bot{TOKEN}/{path}").mock(
        return_value=httpx.Response(200, content=content, headers={"content-type": "image/jpeg"})
    )


@respx.mock
async def test_a_reviewer_sees_the_receipt_image_itself():
    acct = await _account_with_card()
    claim_id = await _claim(account_id=acct)
    _telegram()
    c = await logged_in("REVIEWER")
    async with c:
        r = await c.get(f"/payments/{claim_id}/receipt")
    assert r.status_code == 200
    assert r.content == PNG
    assert r.headers["content-type"].startswith("image/")
    # A customer's bank receipt is not something to leave in a shared cache.
    assert "private" in r.headers.get("cache-control", "")


@respx.mock
async def test_the_image_is_fetched_once_and_then_served_from_memory():
    acct = await _account_with_card()
    claim_id = await _claim(account_id=acct)
    _telegram()
    c = await logged_in("REVIEWER")
    async with c:
        first = await c.get(f"/payments/{claim_id}/receipt")
        second = await c.get(f"/payments/{claim_id}/receipt")
    assert first.status_code == second.status_code == 200
    assert len(respx.calls) == 2, "a page refresh must not re-fetch the file"


@respx.mock
async def test_an_operator_cannot_ask_for_a_file_id_of_their_own_choosing():
    """The route takes a payment, never a file id. Otherwise the panel would
    be a way to read any file AloBot's bot has ever received."""
    acct = await _account_with_card()
    claim_id = await _claim(account_id=acct)
    _telegram()
    c = await logged_in("REVIEWER")
    async with c:
        r = await c.get(f"/payments/{claim_id}/receipt", params={"file_id": "someone-elses-file"})
    assert r.status_code == 200
    assert respx.calls[0].request.url.params["file_id"] == "AgAC"


async def test_a_payment_with_no_receipt_says_so_rather_than_erroring():
    acct = await _account_with_card()
    claim_id = await _claim(account_id=acct)
    async with async_session_maker() as s:
        claim = await s.get(PaymentClaim, claim_id)
        claim.receipt_file_id = None
        await s.commit()
    c = await logged_in("REVIEWER")
    async with c:
        r = await c.get(f"/payments/{claim_id}/receipt")
    assert r.status_code == 404


async def test_a_read_only_operator_is_refused():
    acct = await _account_with_card()
    claim_id = await _claim(account_id=acct)
    c = await logged_in("READ_ONLY")
    async with c:
        r = await c.get(f"/payments/{claim_id}/receipt")
    assert r.status_code == 403


async def test_without_alobots_token_it_says_which_setting_is_missing(monkeypatch):
    monkeypatch.setattr(get_settings(), "alobot_bot_token", "")
    acct = await _account_with_card()
    claim_id = await _claim(account_id=acct)
    c = await logged_in("REVIEWER")
    async with c:
        r = await c.get(f"/payments/{claim_id}/receipt")
    assert r.status_code == 503
    assert "ALOBOT_BOT_TOKEN" in r.text


@respx.mock
async def test_telegram_refusing_is_reported_without_leaking_the_token(caplog):
    acct = await _account_with_card()
    claim_id = await _claim(account_id=acct)
    respx.get(f"https://api.telegram.org/bot{TOKEN}/getFile").mock(
        return_value=httpx.Response(400, json={"ok": False, "error_code": 400, "description": "wrong file identifier"})
    )
    c = await logged_in("REVIEWER")
    async with c:
        r = await c.get(f"/payments/{claim_id}/receipt")
    assert r.status_code == 502
    assert TOKEN not in r.text
    assert TOKEN not in caplog.text, "the token reached the log"


@respx.mock
async def test_an_absurdly_large_file_is_refused_rather_than_read_into_memory():
    acct = await _account_with_card()
    claim_id = await _claim(account_id=acct)
    respx.get(f"https://api.telegram.org/bot{TOKEN}/getFile").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"file_path": "photos/huge.jpg", "file_size": 99_000_000}})
    )
    c = await logged_in("REVIEWER")
    async with c:
        r = await c.get(f"/payments/{claim_id}/receipt")
    assert r.status_code == 502
