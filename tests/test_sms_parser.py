"""Phase 3 task 3: the parser, on a corpus that is provisional until real
messages arrive (see tests/sms_corpus/README.md)."""

import json
from pathlib import Path

import pytest

from app.sms import normalize, parse, redact_otp
from app.sms.normalize import parse_amount
from app.sms.patterns import DbPattern, PatternRefused, check_pattern

CORPUS = Path(__file__).parent / "sms_corpus"


def corpus():
    for path in sorted(CORPUS.glob("*.txt")):
        blocks = [b for b in path.read_text().split("\n\n") if b.strip()]
        for block in blocks:
            lines = block.strip().splitlines()
            assert lines[0].startswith("# expect: "), f"{path.name}: block without expectation"
            expect = json.loads(lines[0][len("# expect: "):])
            yield pytest.param("\n".join(lines[1:]), expect, id=f"{path.stem}:{expect.get('classification')}:{lines[1][:20]}")


@pytest.mark.parametrize(("body", "expect"), list(corpus()))
def test_corpus_message_parses_as_expected(body, expect):
    result = parse(body, sender="Bank")
    for key, value in expect.items():
        assert getattr(result, key) == value, (key, result)


def test_normalize_maps_persian_and_arabic_digits_and_letters():
    assert normalize("۱۲۳٤٥٦ كي") == "123456 کی"
    assert normalize("a‌b   c\n\n d") == "a‌b c d"


@pytest.mark.parametrize(("raw", "amount"), [("1,250,000", 1250000), ("۱٬۲۵۰٬۰۰۰", 1250000), ("+5.000.000", 5000000), ("-450,000", 450000), ("300000", 300000)])
def test_parse_amount_handles_separators_signs_and_digits(raw, amount):
    assert parse_amount(raw) == amount


def test_otp_body_is_redacted_before_it_can_be_stored():
    body = "رمز پویا: 482913\nمبلغ: 1,250,000 ریال"
    redacted = redact_otp(body)
    assert "482913" not in redacted and "[OTP" in redacted


def test_unknown_message_never_defaults_to_a_credit():
    result = parse("1,250,000", sender="Bank")
    assert result.classification != "BANK_TRANSACTION" or result.direction != "CREDIT"


def test_db_pattern_adds_a_bank_name_but_never_overrides_a_parsed_amount():
    pattern = DbPattern(id="p", bank_name="بانک مثال", enabled=True, priority=1, detect_re="واریز", amount_re=r"(\d+)", amount_unit="IRR", direction="CREDIT")
    body = "واریز به حساب 47045299\nمبلغ: 1,250,000 ریال"
    result = parse(body, sender="X", patterns=[pattern])
    assert result.amount_irr == 1250000 and result.bank_name == "بانک مثال"


def test_db_pattern_parses_what_the_builtin_parsers_could_not():
    pattern = DbPattern(id="weird", bank_name="بانک عجیب", enabled=True, priority=1, detect_re="WEIRDBANK", amount_re=r"AMT=(\d+)", amount_unit="TOMAN", direction="CREDIT", balance_re=r"BAL=(\d+)", account_re=r"ACC=(\d+)")
    result = parse("WEIRDBANK AMT=125000 BAL=800000 ACC=5299", sender="X", patterns=[pattern])
    assert result.classification == "BANK_TRANSACTION"
    assert (result.amount_irr, result.balance_irr, result.account_hint, result.bank_name) == (1250000, 8000000, "5299", "بانک عجیب")


def test_db_pattern_never_touches_an_otp():
    pattern = DbPattern(id="p", bank_name="x", enabled=True, priority=1, detect_re="مبلغ", amount_re=r"([\d,]+)", amount_unit="IRR", direction="CREDIT")
    result = parse("رمز پویا: 482913\nمبلغ: 1,250,000 ریال", sender="X", patterns=[pattern])
    assert result.classification == "OTP" and result.amount_irr is None


def test_check_pattern_refuses_a_catastrophic_regex_and_a_non_regex():
    with pytest.raises(PatternRefused):
        check_pattern(r"(a+)+$")
    with pytest.raises(PatternRefused):
        check_pattern(r"([0-9")
    assert check_pattern(r"مبلغ\s*([\d,]+)") is None
