"""A thin Telegram Bot API client: sendMessage only. This project never polls
for updates - AloBot owns that - and a test asserts the polling method's
name does not appear anywhere under app/."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

PERMANENT_DESCRIPTIONS = ("blocked by the user", "user is deactivated", "chat not found", "bot can't initiate conversation", "bot was kicked")


@dataclass
class SendResult:
    ok: bool
    error_code: int | None = None
    description: str = ""
    retry_after: int | None = None

    @property
    def permanent(self) -> bool:
        if self.error_code == 403:
            return True
        return self.error_code == 400 and any(p in self.description.lower() for p in PERMANENT_DESCRIPTIONS)


class TelegramApi:
    def __init__(self, token: str, base_url: str = "https://api.telegram.org", timeout: float = 10.0) -> None:
        self._url = f"{base_url}/bot{token}/sendMessage"
        self._timeout = timeout

    async def send_message(self, chat_id: int, text: str, parse_mode: str | None = "HTML", reply_markup: dict[str, Any] | None = None) -> SendResult:
        body: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if parse_mode:
            body["parse_mode"] = parse_mode
        if reply_markup:
            body["reply_markup"] = reply_markup
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self._url, json=body)
        except httpx.HTTPError as exc:
            return SendResult(False, None, f"network: {type(exc).__name__}")
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code == 200 and data.get("ok"):
            return SendResult(True)
        retry_after = (data.get("parameters") or {}).get("retry_after")
        return SendResult(False, data.get("error_code", response.status_code), str(data.get("description", response.text))[:200], retry_after)
