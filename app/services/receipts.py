"""Fetching a payment's receipt photo from Telegram, on demand.

The receipt is not stored anywhere. AloBot keeps only a `file_id` on its
`payments` row and re-sends the photo to admins by that id; nothing in AloBot
ever downloads the bytes. A `file_id` is only usable by the bot that received
it, so this calls `getFile` with AloBot's own token and fetches the file.

Three deliberate limits:

- **Nothing reaches disk.** A bank receipt carries a customer's name and
  account. It is held in memory for a few minutes so that refreshing a page
  does not re-fetch it, and that is all.
- **The size is checked before the body is read**, so a surprising file
  cannot be pulled into memory.
- **The token never leaves this module.** It is in the URL, so the URL is
  never logged and never put in an error shown to an operator.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

API = "https://api.telegram.org"
MAX_BYTES = 10 * 1024 * 1024
CACHE_TTL_SECONDS = 300
CACHE_MAX_ENTRIES = 32
TIMEOUT = 15.0

MISSING_TOKEN = "برای نمایش رسید باید ALOBOT_BOT_TOKEN تنظیم شود؛ فایل رسید فقط با توکن خود آلوبات قابل دریافت است."


class ReceiptError(RuntimeError):
    """Something went wrong at Telegram's end. The message is safe to show:
    it never contains the token."""


@dataclass
class Receipt:
    content: bytes
    content_type: str


_cache: dict[str, tuple[float, Receipt]] = {}


def cache_clear() -> None:
    _cache.clear()


def _cached(file_id: str) -> Receipt | None:
    entry = _cache.get(file_id)
    if entry is None:
        return None
    stored_at, receipt = entry
    if time.monotonic() - stored_at > CACHE_TTL_SECONDS:
        _cache.pop(file_id, None)
        return None
    return receipt


def _remember(file_id: str, receipt: Receipt) -> None:
    if len(_cache) >= CACHE_MAX_ENTRIES:
        oldest = min(_cache, key=lambda key: _cache[key][0])
        _cache.pop(oldest, None)
    _cache[file_id] = (time.monotonic(), receipt)


async def fetch(file_id: str) -> Receipt:
    cached = _cached(file_id)
    if cached is not None:
        return cached

    token = get_settings().alobot_bot_token
    if not token:
        raise PermissionError(MISSING_TOKEN)

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            described = await client.get(f"{API}/bot{token}/getFile", params={"file_id": file_id})
        except httpx.HTTPError as exc:
            raise ReceiptError(f"تلگرام پاسخ نداد: {type(exc).__name__}") from None
        data = described.json() if described.headers.get("content-type", "").startswith("application/json") else {}
        if described.status_code != 200 or not data.get("ok"):
            description = str(data.get("description", ""))[:120]
            log.warning("receipt.getfile_failed", status=described.status_code, detail=description)
            raise ReceiptError(f"تلگرام فایل را نداد: {description or described.status_code}")

        result = data.get("result") or {}
        path = result.get("file_path")
        if not path:
            raise ReceiptError("تلگرام مسیر فایل را برنگرداند.")
        size = result.get("file_size")
        if isinstance(size, int) and size > MAX_BYTES:
            log.warning("receipt.too_large", size=size)
            raise ReceiptError("فایل رسید بزرگ‌تر از حد مجاز است.")

        try:
            downloaded = await client.get(f"{API}/file/bot{token}/{path}")
        except httpx.HTTPError as exc:
            raise ReceiptError(f"دریافت فایل ناموفق بود: {type(exc).__name__}") from None
        if downloaded.status_code != 200:
            log.warning("receipt.download_failed", status=downloaded.status_code)
            raise ReceiptError(f"دریافت فایل ناموفق بود: {downloaded.status_code}")
        if len(downloaded.content) > MAX_BYTES:
            raise ReceiptError("فایل رسید بزرگ‌تر از حد مجاز است.")

    receipt = Receipt(downloaded.content, downloaded.headers.get("content-type", "image/jpeg").split(";")[0])
    _remember(file_id, receipt)
    return receipt
