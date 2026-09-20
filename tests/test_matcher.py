"""Phase 4 task 3: the decision engine. Pure: no database, no clock of its own."""

import datetime as dt

from app.services.matcher import Claim, Credit, evaluate

T0 = dt.datetime(2026, 9, 20, 12, 0, tzinfo=dt.timezone.utc)
MIN = dt.timedelta(minutes=1)


def claim(id, amount=1250000, account=1, account_status="ACTIVE", at=T0, status="PENDING", continuity=False):
    return Claim(id=id, expected_amount_irr=amount, target_account_id=account, account_status=account_status, paid_clicked_at=at, status=status, continuity=continuity)


def credit(id, amount=1250000, account=1, at=T0 + 2 * MIN, consumed=False, direction="CREDIT", disposition="ACTIONABLE"):
    return Credit(id=id, amount_irr=amount, account_id=account, bank_timestamp=at, consumed=consumed, direction=direction, disposition=disposition)


def test_an_isolated_exact_pair_inside_five_minutes_auto_verifies():
    d = evaluate([claim(1)], [credit(10)], now=T0 + 3 * MIN)
    assert d[1].decision == "AUTO_VERIFY" and d[1].transaction_id == 10 and d[1].reason == "UNIQUE_EXACT_MATCH"


def test_five_minutes_is_inclusive_and_symmetric():
    assert evaluate([claim(1)], [credit(10, at=T0 + dt.timedelta(minutes=5))], now=T0).get(1).decision == "AUTO_VERIFY"
    assert evaluate([claim(1)], [credit(10, at=T0 - dt.timedelta(minutes=5))], now=T0).get(1).decision == "AUTO_VERIFY"
    assert evaluate([claim(1)], [credit(10, at=T0 + dt.timedelta(minutes=5, seconds=1))], now=T0 + 20 * MIN).get(1).decision != "AUTO_VERIFY"


def test_amount_must_be_exact_to_the_rial():
    d = evaluate([claim(1, amount=1250000)], [credit(10, amount=1250010)], now=T0 + 20 * MIN)
    assert d[1].decision == "SUGGEST" and d[1].reason == "NO_TRANSACTION_AFTER_10M"


def test_account_must_match_and_be_active():
    assert evaluate([claim(1, account=1)], [credit(10, account=2)], now=T0 + 20 * MIN)[1].reason == "NO_TRANSACTION_AFTER_10M"
    assert evaluate([claim(1, account=None)], [credit(10)], now=T0)[1].reason == "UNMAPPED_CARD"
    assert evaluate([claim(1, account_status="PENDING")], [credit(10)], now=T0)[1].reason == "ACCOUNT_NOT_ACTIVE"


def test_two_claims_for_one_credit_both_stay_suggested_even_when_one_is_closer():
    d = evaluate([claim(1, at=T0), claim(2, at=T0 + MIN)], [credit(10, at=T0 + dt.timedelta(seconds=5))], now=T0 + 3 * MIN)
    assert d[1].decision == "SUGGEST" and d[1].reason == "AMBIGUOUS_CLAIMS"
    assert d[2].decision == "SUGGEST" and d[2].reason == "AMBIGUOUS_CLAIMS"


def test_two_credits_for_one_claim_is_ambiguous():
    d = evaluate([claim(1)], [credit(10), credit(11, at=T0 + 3 * MIN)], now=T0 + 4 * MIN)
    assert d[1].decision == "SUGGEST" and d[1].reason == "AMBIGUOUS_TRANSACTIONS" and set(d[1].candidate_transaction_ids) == {10, 11}


def test_two_claims_two_credits_that_cross_all_stay_suggested():
    d = evaluate([claim(1), claim(2)], [credit(10), credit(11)], now=T0 + 4 * MIN)
    assert {x.decision for x in d.values()} == {"SUGGEST"}


def test_result_does_not_depend_on_ordering():
    a = evaluate([claim(1), claim(2, amount=99)], [credit(11, amount=99), credit(10)], now=T0 + 4 * MIN)
    b = evaluate([claim(2, amount=99), claim(1)], [credit(10), credit(11, amount=99)], now=T0 + 4 * MIN)
    assert {k: (v.decision, v.transaction_id) for k, v in a.items()} == {k: (v.decision, v.transaction_id) for k, v in b.items()}
    assert a[1].transaction_id == 10 and a[2].transaction_id == 11


def test_consumed_debit_and_declined_credits_are_not_eligible():
    assert evaluate([claim(1)], [credit(10, consumed=True)], now=T0)[1].decision == "WAIT"
    assert evaluate([claim(1)], [credit(10, direction="DEBIT")], now=T0)[1].decision == "WAIT"
    assert evaluate([claim(1)], [credit(10, disposition="DECLINED_INCOME")], now=T0)[1].decision == "WAIT"


def test_wait_for_ten_minutes_then_say_no_transaction():
    assert evaluate([claim(1)], [], now=T0 + 9 * MIN)[1] .decision == "WAIT"
    assert evaluate([claim(1)], [], now=T0 + 9 * MIN)[1].reason == "AWAITING_BANK_SMS"
    late = evaluate([claim(1)], [], now=T0 + 11 * MIN)[1]
    assert late.decision == "SUGGEST" and late.reason == "NO_TRANSACTION_AFTER_10M"


def test_a_continuity_claim_gets_a_day_not_five_minutes():
    d = evaluate([claim(1, continuity=True)], [credit(10, at=T0 + dt.timedelta(hours=6))], now=T0 + dt.timedelta(hours=7))
    assert d[1].decision == "AUTO_VERIFY"
    normal = evaluate([claim(1)], [credit(10, at=T0 + dt.timedelta(hours=6))], now=T0 + dt.timedelta(hours=7))
    assert normal[1].decision == "SUGGEST"


def test_non_pending_claims_are_ignored():
    assert evaluate([claim(1, status="REJECTED")], [credit(10)], now=T0) == {}
