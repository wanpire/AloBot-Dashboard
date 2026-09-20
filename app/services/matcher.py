"""The decision: may this claim be auto-verified against this bank credit?

Pure - takes no database handle, no clock of its own - so the rule can be
tested exhaustively and nothing else in the project may re-derive it.

The product rule: AUTO_VERIFY only an isolated 1↔1 pair. Exactly one eligible
credit for the claim and exactly one eligible claim for that credit; same
account, same amount to the Rial, inside the window. Everything else is
SUGGEST (a human decides) or WAIT (the SMS may still be on its way). Never
auto-reject, never auto-fake. Time distance inside the window is NOT a
tiebreaker: two claims that both fit one credit both wait for a person.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field

AUTO_MATCH_WINDOW = dt.timedelta(minutes=5)
CONTINUITY_WINDOW = dt.timedelta(hours=24)
WAITING_PERIOD = dt.timedelta(minutes=10)


@dataclass(frozen=True)
class Claim:
    id: int
    expected_amount_irr: int
    target_account_id: int | None
    account_status: str | None
    paid_clicked_at: dt.datetime
    status: str = "PENDING"
    continuity: bool = False


@dataclass(frozen=True)
class Credit:
    id: int
    amount_irr: int | None
    account_id: int | None
    bank_timestamp: dt.datetime | None
    consumed: bool = False
    direction: str = "CREDIT"
    disposition: str = "ACTIONABLE"


@dataclass
class Decision:
    claim_id: int
    decision: str  # AUTO_VERIFY | SUGGEST | WAIT
    reason: str
    transaction_id: int | None = None
    time_delta_seconds: int | None = None
    candidate_transaction_ids: list[int] = field(default_factory=list)
    competing_claim_ids: list[int] = field(default_factory=list)


def _window(claim: Claim) -> dt.timedelta:
    return CONTINUITY_WINDOW if claim.status == "FULFILLED_UNRECONCILED" or claim.continuity else AUTO_MATCH_WINDOW


def _eligible(claim: Claim, credit: Credit) -> bool:
    if credit.consumed or credit.direction != "CREDIT" or credit.disposition != "ACTIONABLE":
        return False
    if credit.amount_irr is None or credit.bank_timestamp is None:
        return False
    if claim.target_account_id is None or credit.account_id != claim.target_account_id:
        return False
    if credit.amount_irr != claim.expected_amount_irr:
        return False
    return abs(credit.bank_timestamp - claim.paid_clicked_at) <= _window(claim)


def evaluate(claims: list[Claim], credits: list[Credit], now: dt.datetime) -> dict[int, Decision]:
    live = [c for c in claims if c.status in ("PENDING", "FULFILLED_UNRECONCILED")]
    edges: dict[int, list[Credit]] = defaultdict(list)
    claims_per_credit: dict[int, list[int]] = defaultdict(list)
    for claim in live:
        if claim.target_account_id is None or claim.account_status != "ACTIVE":
            continue
        for credit in credits:
            if _eligible(claim, credit):
                edges[claim.id].append(credit)
                claims_per_credit[credit.id].append(claim.id)

    out: dict[int, Decision] = {}
    for claim in sorted(live, key=lambda c: c.id):
        if claim.target_account_id is None:
            out[claim.id] = Decision(claim.id, "SUGGEST", "UNMAPPED_CARD")
            continue
        if claim.account_status != "ACTIVE":
            out[claim.id] = Decision(claim.id, "SUGGEST", "ACCOUNT_NOT_ACTIVE")
            continue
        candidates = sorted(edges.get(claim.id, []), key=lambda c: c.id)
        ids = [c.id for c in candidates]
        if not candidates:
            if now < claim.paid_clicked_at + WAITING_PERIOD:
                out[claim.id] = Decision(claim.id, "WAIT", "AWAITING_BANK_SMS")
            else:
                out[claim.id] = Decision(claim.id, "SUGGEST", "NO_TRANSACTION_AFTER_10M")
            continue
        if len(candidates) > 1:
            out[claim.id] = Decision(claim.id, "SUGGEST", "AMBIGUOUS_TRANSACTIONS", candidate_transaction_ids=ids)
            continue
        credit = candidates[0]
        rivals = sorted(cid for cid in claims_per_credit[credit.id] if cid != claim.id)
        if rivals:
            out[claim.id] = Decision(claim.id, "SUGGEST", "AMBIGUOUS_CLAIMS", candidate_transaction_ids=ids, competing_claim_ids=rivals)
            continue
        delta = int(abs((credit.bank_timestamp - claim.paid_clicked_at).total_seconds()))
        out[claim.id] = Decision(claim.id, "AUTO_VERIFY", "UNIQUE_EXACT_MATCH", transaction_id=credit.id, time_delta_seconds=delta, candidate_transaction_ids=ids)
    return out
