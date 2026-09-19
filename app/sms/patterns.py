"""Operator-written patterns for a bank's SMS shape - additive, never
overriding: a pattern may parse a message the built-in parsers could not,
or add a bank name to one they did; it never changes an amount the built-in
parser read, and it never sees an OTP or promotional message at all.

Every regex runs through the `regex` module with a time budget, and
`check_pattern` tries a pattern on bait input before it can be enabled so
a catastrophic expression is refused at the form, not discovered at 3am."""

from __future__ import annotations

from dataclasses import dataclass

import regex

from app.sms.normalize import parse_amount
from app.sms.types import ParseResult

TIMEOUT_SECONDS = 0.05
MAX_LENGTH = 500
BAIT = ("a" * 2000 + "!", "1" * 2000 + "!", "مبلغ " + "1,000," * 300 + "x", ("ab" * 500) + "!")


class PatternRefused(ValueError):
    pass


@dataclass(frozen=True)
class DbPattern:
    id: str
    bank_name: str
    enabled: bool
    priority: int
    detect_re: str
    amount_re: str
    amount_unit: str = "IRR"
    direction: str = "CREDIT"
    balance_re: str | None = None
    account_re: str | None = None
    reference_re: str | None = None


def check_pattern(expression: str) -> None:
    """Raise PatternRefused when the expression is not a regex, is too long,
    or blows its time budget on bait input."""
    if not expression or len(expression) > MAX_LENGTH:
        raise PatternRefused("الگو باید بین ۱ و ۵۰۰ نویسه باشد.")
    try:
        compiled = regex.compile(expression)
    except regex.error as exc:
        raise PatternRefused(f"الگو معتبر نیست: {exc}") from None
    for bait in BAIT:
        try:
            compiled.search(bait, timeout=TIMEOUT_SECONDS)
        except TimeoutError:
            raise PatternRefused("الگو روی ورودی طولانی بیش از حد کند است و پذیرفته نمی‌شود.") from None


def _search(expression: str | None, text: str) -> str | None:
    if not expression:
        return None
    try:
        m = regex.search(expression, text, timeout=TIMEOUT_SECONDS)
    except (regex.error, TimeoutError):
        return None
    if not m:
        return None
    return m.group(1) if m.groups() else m.group(0)


def apply_patterns(text: str, result: ParseResult, patterns: list[DbPattern]) -> ParseResult:
    if result.classification in ("OTP", "PROMOTIONAL"):
        return result
    for pattern in sorted((p for p in patterns if p.enabled), key=lambda p: p.priority):
        if _search(pattern.detect_re, text) is None:
            continue
        if result.classification == "BANK_TRANSACTION" and result.amount_irr is not None:
            if result.bank_name is None:
                result.bank_name = pattern.bank_name
                result.evidence["bank_from_pattern"] = pattern.id
            return result
        raw_amount = _search(pattern.amount_re, text)
        amount = parse_amount(raw_amount) if raw_amount else None
        if amount is None:
            continue
        factor = 10 if pattern.amount_unit == "TOMAN" else 1
        raw_balance = _search(pattern.balance_re, text)
        balance = parse_amount(raw_balance) if raw_balance else None
        raw_account = _search(pattern.account_re, text)
        account = "".join(ch for ch in raw_account if ch.isdigit())[-4:] if raw_account else None
        return ParseResult(
            "BANK_TRANSACTION",
            direction=pattern.direction,
            amount_irr=amount * factor,
            balance_irr=balance * factor if balance is not None else None,
            account_hint=account or None,
            reference=_search(pattern.reference_re, text),
            bank_name=pattern.bank_name,
            confidence=0.75,
            parser_id=f"pattern:{pattern.id}",
            parser_version="db",
            evidence={"pattern": pattern.id},
        )
    return result
