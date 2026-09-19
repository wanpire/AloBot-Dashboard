"""Persian bank SMS → structured transaction. `parse` is the one entry point."""

from __future__ import annotations

import re

from app.sms.normalize import normalize
from app.sms.parsers import ORDER, parse_unknown
from app.sms.patterns import DbPattern, apply_patterns
from app.sms.types import ParseResult

_OTP_DIGITS = re.compile(r"\d{4,8}")


def redact_otp(body: str) -> str:
    """Every 4-8 digit run becomes [OTP]. The whole message is kept otherwise,
    so an operator can still see which bank sent what kind of OTP."""
    return _OTP_DIGITS.sub("[OTP]", body)


def parse(body: str, sender: str = "", patterns: list[DbPattern] | None = None) -> ParseResult:
    text = normalize(body)
    result: ParseResult | None = None
    for parser in ORDER:
        result = parser(text)
        if result is not None:
            break
    if result is None:
        result = parse_unknown(text)
    return apply_patterns(text, result, patterns or [])


__all__ = ["DbPattern", "ParseResult", "normalize", "parse", "redact_otp"]
