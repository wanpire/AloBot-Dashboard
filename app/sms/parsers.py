"""The built-in parsers, in the order they run.

OTP first, because an OTP message must be redacted before anything else
looks at it. Then promotional noise. Then the generic Iranian bank
transaction parser, which reads direction from phrases and signs, amount
after «مبلغ» or after the direction word or as a signed number, balance
after «مانده/موجودی», the account from «حساب/کارت» (last four digits), and
the reference after «پیگیری/ارجاع». Then balance-only messages, then
unknown.

Every rule here was written from common conventions, not from this shop's
real messages - the corpus in tests/sms_corpus says so. Real bank formats
arrive after the first deployment and land either as new parsers here or
as operator patterns (app.sms.patterns).
"""

from __future__ import annotations

import re

from app.sms.normalize import parse_amount
from app.sms.types import ParseResult

VERSION = "0.1-provisional"

OTP_RE = re.compile(r"رمز\s*(?:پویا|یکبار|یک\s*بار|دوم|عبور|اینترنتی)|کد\s*(?:تایید|تأیید|فعال\s*سازی|ورود|امنیتی)|\bOTP\b|verification\s*code|one[- ]time", re.I)
PROMO_RE = re.compile(r"جشنواره|قرعه\s*کشی|باشگاه\s*مشتریان|لغو\s*۱۱|لغو\s*11|لغو11|تبلیغ|پیشنهاد\s*ویژه|کد\s*تخفیف", re.I)
CREDIT_RE = re.compile(r"واریز|دریافت|افزایش\s*موجودی|انتقال(?:\s*وجه)?\s*به|credit|deposit", re.I)
DEBIT_RE = re.compile(r"برداشت|خرید|پرداخت|کارمزد|کسر|انتقال(?:\s*وجه)?\s*از|debit|withdraw|purchase", re.I)
DATE_TIME_RE = re.compile(r"\d{2,4}/\d{1,2}/\d{1,2}(?:[-\s]+\d{1,2}:\d{2}(?::\d{2})?)?|\b\d{1,2}:\d{2}(?::\d{2})?\b")
NUMBER = r"[+\-−]?\d{1,3}(?:[,٬.]\d{3})+|[+\-−]?\d+"
AMOUNT_AFTER_LABEL_RE = re.compile(r"مبلغ\s*[:：]?\s*(" + NUMBER + r")\s*(ریال|تومان)?")
AMOUNT_AFTER_DIRECTION_RE = re.compile(r"(?:واریز|برداشت|خرید|پرداخت|دریافت|کارمزد|انتقال(?:\s*وجه)?(?:\s*(?:به|از))?(?:\s*(?:حساب|کارت)\s*[\d*]+)?)\s*[:：]?\s*(" + NUMBER + r")\s*(ریال|تومان)?")
SIGNED_AMOUNT_RE = re.compile(r"(?<![\d/:])([+\-−]\d{1,3}(?:[,٬.]\d{3})+|[+\-−]\d{4,})(?![\d/:])\s*(ریال|تومان)?")
BALANCE_RE = re.compile(r"(?:مانده|موجودی)(?:\s*(?:حساب|کارت|فعلی|شما))*\s*[:：]?\s*(" + NUMBER + r")\s*(ریال|تومان)?")
ACCOUNT_RE = re.compile(r"(?:حساب|کارت|سپرده)\s*(?:شماره\s*)?[:：]?\s*([\d*xX×\-]{4,26})")
REFERENCE_RE = re.compile(r"(?:پیگیری|ارجاع|شناسه\s*(?:تراکنش|واریز|پرداخت)|رسید)\s*[:：]?\s*(\d{4,20})")


def _rial(amount: int, unit: str | None) -> int:
    return amount * 10 if unit == "تومان" else amount


def parse_otp(text: str) -> ParseResult | None:
    if OTP_RE.search(text):
        return ParseResult("OTP", parser_id="otp", parser_version=VERSION, confidence=1.0)
    return None


def parse_promo(text: str) -> ParseResult | None:
    if PROMO_RE.search(text) and not AMOUNT_AFTER_LABEL_RE.search(text):
        return ParseResult("PROMOTIONAL", parser_id="promo", parser_version=VERSION, confidence=0.8)
    return None


def _direction(text: str, sign: str | None) -> tuple[str, str]:
    credit, debit = bool(CREDIT_RE.search(text)), bool(DEBIT_RE.search(text))
    if credit and not debit:
        return "CREDIT", "credit_phrase"
    if debit and not credit:
        return "DEBIT", "debit_phrase"
    if sign == "+":
        return "CREDIT", "sign"
    if sign == "-":
        return "DEBIT", "sign"
    if credit and debit:
        return "UNKNOWN", "conflicting_phrases"
    return "UNKNOWN", "none"


def parse_generic(text: str) -> ParseResult | None:
    body = DATE_TIME_RE.sub(" ", text)
    evidence: dict[str, object] = {}
    amount = sign = None
    if m := AMOUNT_AFTER_LABEL_RE.search(body):
        amount, unit, evidence["amount_from"] = parse_amount(m.group(1)), m.group(2), "label"
        sign = m.group(1)[0] if m.group(1)[0] in "+-−" else None
    elif m := AMOUNT_AFTER_DIRECTION_RE.search(body):
        amount, unit, evidence["amount_from"] = parse_amount(m.group(1)), m.group(2), "direction_word"
        sign = m.group(1)[0] if m.group(1)[0] in "+-−" else None
    elif m := SIGNED_AMOUNT_RE.search(body):
        amount, unit, evidence["amount_from"] = parse_amount(m.group(1)), m.group(2), "sign"
        sign = "-" if m.group(1)[0] in "-−" else "+"
    else:
        unit = None
    if amount is None:
        return None
    direction, source = _direction(body, sign)
    if direction == "UNKNOWN" and evidence.get("amount_from") != "label":
        return None  # a bare number is not a transaction
    evidence["direction_from"] = source
    result = ParseResult(
        "BANK_TRANSACTION", direction=direction, amount_irr=_rial(amount, unit), parser_id="generic-fa",
        parser_version=VERSION, evidence=evidence,
    )
    if bm := BALANCE_RE.search(body):
        result.balance_irr = _rial(parse_amount(bm.group(1)) or 0, bm.group(2))
    if am := ACCOUNT_RE.search(body):
        digits = re.sub(r"\D", "", am.group(1))
        if len(digits) >= 4:
            result.account_hint = digits[-4:]
            evidence["account_raw"] = am.group(1)
            evidence["account_kind"] = "card" if am.group(0).startswith("کارت") else "account"
    if rm := REFERENCE_RE.search(body):
        result.reference = rm.group(1)
    result.confidence = 0.9 if (source.endswith("phrase") and evidence["amount_from"] == "label") else 0.7 if direction != "UNKNOWN" else 0.4
    if direction == "UNKNOWN":
        result.warnings.append("direction could not be determined")
    return result


def parse_balance(text: str) -> ParseResult | None:
    if m := BALANCE_RE.search(DATE_TIME_RE.sub(" ", text)):
        return ParseResult(
            "BALANCE", balance_irr=_rial(parse_amount(m.group(1)) or 0, m.group(2)), parser_id="balance",
            parser_version=VERSION, confidence=0.8,
        )
    return None


def parse_unknown(text: str) -> ParseResult:
    return ParseResult("UNKNOWN", parser_id="unknown", parser_version=VERSION, confidence=0.0)


ORDER = (parse_otp, parse_promo, parse_generic, parse_balance)
