"""The settle sweep: ask the matcher about every open claim, write what it
decided. An auto-verification is one match row plus one conditional UPDATE
on the claim; a race between two sweeps is decided by the partial unique
index on the match table, and the loser rolls back and moves on."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import FinancialAccount, PaymentClaim, ReconciliationMatch, TransactionCandidate
from app.services import outbox
from app.services import settings as settings_service
from app.services.matcher import Claim, Credit, Decision, evaluate
from app.web.format import fa_number

log = get_logger(__name__)
LOOKBACK = dt.timedelta(days=3)


async def _load(session: AsyncSession, now: dt.datetime) -> tuple[list[PaymentClaim], list[Claim], list[Credit]]:
    rows = (await session.execute(select(PaymentClaim).where(PaymentClaim.status.in_(("PENDING", "FULFILLED_UNRECONCILED"))))).scalars().all()
    if not rows:
        return [], [], []
    accounts = {a.id: a for a in (await session.execute(select(FinancialAccount))).scalars().all()}
    claims = [
        Claim(
            id=c.id, expected_amount_irr=c.expected_amount_irr, target_account_id=c.target_account_id,
            account_status=accounts[c.target_account_id].status if c.target_account_id in accounts else None,
            paid_clicked_at=c.paid_clicked_at, status=c.status, continuity=c.continuity,
        )
        for c in rows
    ]
    consumed = {
        m.transaction_id
        for m in (await session.execute(select(ReconciliationMatch.transaction_id).where(ReconciliationMatch.status.in_(("AUTO_VERIFIED", "CONFIRMED"))))).all()
    }
    since = min(c.paid_clicked_at for c in rows) - dt.timedelta(hours=24)
    txs = (
        await session.execute(
            select(TransactionCandidate).where(
                TransactionCandidate.direction == "CREDIT", TransactionCandidate.bank_timestamp >= since, TransactionCandidate.amount_irr.is_not(None)
            )
        )
    ).scalars().all()
    credits = [
        Credit(id=t.id, amount_irr=t.amount_irr, account_id=t.account_id, bank_timestamp=t.bank_timestamp, consumed=t.id in consumed, direction=t.direction, disposition=t.disposition)
        for t in txs
    ]
    return rows, claims, credits


async def _auto_verify(session: AsyncSession, claim: PaymentClaim, decision: Decision, now: dt.datetime, notify_customers: bool) -> bool:
    """One transaction: the settling match row and the claim's transition.
    Two sweeps racing on the same pair both get here; the partial unique
    index lets exactly one commit, and the loser rolls back and moves on.
    The claim's fields are copied first: after a rollback the ORM object is
    expired and must not be read."""
    claim_id, chat_id, amount, code = claim.id, claim.telegram_id, claim.expected_amount_irr, claim.invoice_code or claim.alobot_payment_id
    session.add(
        ReconciliationMatch(
            claim_id=claim_id, transaction_id=decision.transaction_id, status="AUTO_VERIFIED", reason=decision.reason,
            time_delta_seconds=decision.time_delta_seconds, decided_by="matcher", decided_at=now,
        )
    )
    try:
        await session.flush()
        result = await session.execute(
            text(
                "UPDATE payment_claims SET status = 'AUTO_VERIFIED', verified_at = :now, verified_by = 'matcher', suspect_reason = NULL, "
                "candidate_transaction_ids = NULL, last_evaluated_at = :now WHERE id = :id AND status IN ('PENDING', 'FULFILLED_UNRECONCILED')"
            ),
            {"now": now, "id": claim_id},
        )
        if result.rowcount != 1:
            raise IntegrityError("claim moved on", None, Exception("lost the claim transition"))
        if notify_customers:
            await outbox.enqueue(
                session, dedupe_key=f"verify:{claim_id}", chat_id=chat_id,
                text=f"✅ واریز {fa_number(amount // 10)} تومان شما تایید شد. سفارش {code} در حال آماده‌سازی است.",
                commit=False,
            )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        log.info("settle.lost_race", claim_id=claim_id, transaction_id=decision.transaction_id)
        return False
    log.info("settle.auto_verified", claim_id=claim_id, transaction_id=decision.transaction_id, delta=decision.time_delta_seconds)
    return True


async def _record_suggestion(session: AsyncSession, claim_id: int, decision: Decision, now: dt.datetime) -> None:
    claim = await session.get(PaymentClaim, claim_id)  # fresh: an earlier rollback may have expired the loaded one
    if claim is None:
        return
    claim.suspect_reason = decision.reason
    claim.candidate_transaction_ids = decision.candidate_transaction_ids or None
    claim.last_evaluated_at = now
    existing = {
        m.transaction_id
        for m in (await session.execute(select(ReconciliationMatch).where(ReconciliationMatch.claim_id == claim.id, ReconciliationMatch.status == "SUGGESTED"))).scalars().all()
    }
    for tx_id in decision.candidate_transaction_ids:
        if tx_id not in existing:
            session.add(ReconciliationMatch(claim_id=claim.id, transaction_id=tx_id, status="SUGGESTED", reason=decision.reason))
    await session.commit()


async def settle(session: AsyncSession, now: dt.datetime | None = None) -> dict[str, Any]:
    now = now or dt.datetime.now(dt.timezone.utc)
    rows, claims, credits = await _load(session, now)
    if not rows:
        return {"evaluated": 0, "auto_verified": 0, "suggested": 0, "waiting": 0}
    decisions = evaluate(claims, credits, now)
    notify_customers = bool(await settings_service.get(session, "notify", "customers_enabled"))
    counts = {"evaluated": len(decisions), "auto_verified": 0, "suggested": 0, "waiting": 0}
    by_id = {c.id: c for c in rows}
    for claim_id, decision in decisions.items():
        claim = by_id[claim_id]
        if decision.decision == "AUTO_VERIFY":
            if await _auto_verify(session, claim, decision, now, notify_customers):
                counts["auto_verified"] += 1
            continue
        await _record_suggestion(session, claim_id, decision, now)
        counts["suggested" if decision.decision == "SUGGEST" else "waiting"] += 1
    return counts
