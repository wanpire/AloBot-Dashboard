"""What an operator may do to a claim. Every action is a conditional
transition - the claim's expected status is in the WHERE - so a decision
taken twice, or by two people at once, lands once and the second gets a
sentence instead of a second write."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import and_, case, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FinancialAccount, PaymentClaim, ReconciliationMatch, TransactionCandidate
from app.models.claims import SETTLING_MATCH_STATUSES
from app.services import outbox
from app.services import settings as settings_service
from app.services.audit import audit_row

PAGE_SIZE = 50
OPEN = ("PENDING", "FULFILLED_UNRECONCILED")

REVIEW_TEMPLATES: dict[str, tuple[str, str]] = {
    "contact_support": ("تماس با پشتیبانی", "رسید شما رسیده ولی واریز در حساب ما دیده نشد؛ لطفاً با پشتیبانی تماس بگیرید."),
    "receipt_unclear": ("رسید ناخوانا", "رسید ارسالی خوانا نیست؛ لطفاً تصویر واضح‌تری از رسید بفرستید."),
    "amount_mismatch": ("مبلغ متفاوت", "مبلغ واریزی با مبلغ فاکتور یکسان نیست؛ لطفاً با پشتیبانی تماس بگیرید."),
}

ERR_DECIDED = "برای این پرداخت قبلاً تصمیم گرفته شده است."
ERR_TX_TAKEN = "این تراکنش قبلاً یک پرداخت دیگر را تسویه کرده است."


class ReviewError(ValueError):
    pass


async def _lock(session: AsyncSession, claim_id: int) -> PaymentClaim:
    claim = await session.get(PaymentClaim, claim_id, with_for_update=True)
    if claim is None:
        raise ReviewError("چنین پرداختی نیست.")
    return claim


def _audit(actor, action: str, claim: PaymentClaim, **after) -> Any:
    return audit_row(action=action, entity_type="payment_claim", entity_id=str(claim.id), actor_role=actor.role, actor_operator_id=actor.id, before={"status": claim.status}, after=after or None)


async def _transition(session: AsyncSession, claim: PaymentClaim, *, to: str, allowed: tuple[str, ...], now: dt.datetime, actor, verified: bool, note: str | None = None) -> None:
    values = {
        "status": to,
        "decided_at": now,
        "decided_by": actor.email,
        "verified_at": now if verified else None,
        "verified_by": actor.email if verified else None,
    }
    if note is not None:
        values["note"] = note
    if to != "PENDING":
        values["suspect_reason"] = None
    result = await session.execute(update(PaymentClaim).where(PaymentClaim.id == claim.id, PaymentClaim.status.in_(allowed)).values(**values))
    if result.rowcount != 1:
        await session.rollback()
        raise ReviewError(ERR_DECIDED)


async def approve_with_transaction(session: AsyncSession, actor, claim_id: int, transaction_id: int, now: dt.datetime) -> None:
    claim = await _lock(session, claim_id)
    if claim.status not in OPEN:
        raise ReviewError(ERR_DECIDED)
    tx = await session.get(TransactionCandidate, transaction_id)
    if tx is None or tx.direction != "CREDIT":
        raise ReviewError("چنین واریزی نیست.")
    delta = int(abs((tx.bank_timestamp - claim.paid_clicked_at).total_seconds())) if tx.bank_timestamp else None
    session.add(ReconciliationMatch(claim_id=claim.id, transaction_id=tx.id, status="CONFIRMED", reason="operator", time_delta_seconds=delta, decided_by=actor.email, decided_at=now))
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise ReviewError(ERR_TX_TAKEN) from None
    before = claim.status
    await _transition(session, claim, to="MANUAL_VERIFIED", allowed=OPEN, now=now, actor=actor, verified=True)
    session.add(audit_row(action="claim.approve", entity_type="payment_claim", entity_id=str(claim.id), actor_role=actor.role, actor_operator_id=actor.id, before={"status": before}, after={"status": "MANUAL_VERIFIED", "transaction_id": tx.id}))
    await session.commit()


async def verify_manually(session: AsyncSession, actor, claim_id: int, note: str, now: dt.datetime) -> None:
    if not note.strip():
        raise ReviewError("تایید بدون تراکنش بانکی دلیل می‌خواهد.")
    claim = await _lock(session, claim_id)
    before = claim.status
    await _transition(session, claim, to="MANUAL_VERIFIED", allowed=OPEN, now=now, actor=actor, verified=True, note=note.strip())
    session.add(audit_row(action="claim.verify_manual", entity_type="payment_claim", entity_id=str(claim.id), actor_role=actor.role, actor_operator_id=actor.id, before={"status": before}, after={"status": "MANUAL_VERIFIED", "note": note.strip()}))
    await session.commit()


async def _decide(session: AsyncSession, actor, claim_id: int, to: str, note: str, now: dt.datetime, action: str) -> None:
    claim = await _lock(session, claim_id)
    before = claim.status
    await _transition(session, claim, to=to, allowed=OPEN, now=now, actor=actor, verified=False, note=note.strip() or None)
    session.add(audit_row(action=action, entity_type="payment_claim", entity_id=str(claim.id), actor_role=actor.role, actor_operator_id=actor.id, before={"status": before}, after={"status": to, "note": note.strip() or None}))
    await session.commit()


async def reject(session: AsyncSession, actor, claim_id: int, note: str, now: dt.datetime) -> None:
    await _decide(session, actor, claim_id, "REJECTED", note, now, "claim.reject")


async def mark_fake(session: AsyncSession, actor, claim_id: int, note: str, now: dt.datetime) -> None:
    await _decide(session, actor, claim_id, "FAKE", note, now, "claim.fake")


async def park(session: AsyncSession, actor, claim_id: int, now: dt.datetime) -> None:
    claim = await _lock(session, claim_id)
    if claim.status not in OPEN:
        raise ReviewError(ERR_DECIDED)
    claim.parked_at = now
    session.add(_audit(actor, "claim.park", claim))
    await session.commit()


async def unpark(session: AsyncSession, actor, claim_id: int, now: dt.datetime) -> None:
    claim = await _lock(session, claim_id)
    claim.parked_at = None
    session.add(_audit(actor, "claim.unpark", claim))
    await session.commit()


async def reopen(session: AsyncSession, actor, claim_id: int, now: dt.datetime) -> None:
    """Back to PENDING; a settling match is marked REJECTED so the credit is
    free again. The audit row is the history."""
    claim = await _lock(session, claim_id)
    if claim.status in OPEN:
        raise ReviewError("این پرداخت باز است.")
    before = claim.status
    await session.execute(
        text("UPDATE reconciliation_matches SET status = 'REJECTED', decided_by = :who, decided_at = :now WHERE claim_id = :id AND status IN ('AUTO_VERIFIED', 'CONFIRMED')"),
        {"who": actor.email, "now": now, "id": claim.id},
    )
    await _transition(session, claim, to="PENDING", allowed=("MANUAL_VERIFIED", "AUTO_VERIFIED", "REJECTED", "FAKE"), now=now, actor=actor, verified=False)
    session.add(audit_row(action="claim.reopen", entity_type="payment_claim", entity_id=str(claim.id), actor_role=actor.role, actor_operator_id=actor.id, before={"status": before}, after={"status": "PENDING"}))
    await session.commit()


async def message(session: AsyncSession, actor, claim_id: int, template: str, now: dt.datetime) -> None:
    if template not in REVIEW_TEMPLATES:
        raise ReviewError("چنین پیام آماده‌ای نیست.")
    if not await settings_service.get(session, "notify", "customers_enabled"):
        raise ReviewError("ارسال پیام به مشتریان خاموش است (تا فاز یکپارچه‌سازی)؛ در تنظیمات داشبورد روشن می‌شود.")
    claim = await _lock(session, claim_id)
    _, body = REVIEW_TEMPLATES[template]
    await outbox.enqueue(session, dedupe_key=f"msg:{claim.id}:{template}", chat_id=claim.telegram_id, text=body, commit=False)
    claim.messaged_at, claim.messaged_template = now, template
    session.add(_audit(actor, "claim.message", claim, template=template))
    await session.commit()


# ── Queues ─────────────────────────────────────────────────────────────────

C = PaymentClaim
TAB_CONDITIONS = {
    "review": and_(C.status == "PENDING", C.parked_at.is_(None), C.messaged_at.is_(None), C.suspect_reason.is_not(None), C.suspect_reason != "AWAITING_BANK_SMS"),
    "waiting": and_(C.status == "PENDING", C.parked_at.is_(None), C.messaged_at.is_(None), or_(C.suspect_reason.is_(None), C.suspect_reason == "AWAITING_BANK_SMS")),
    "parked": and_(C.status == "PENDING", C.parked_at.is_not(None)),
    "messaged": and_(C.status == "PENDING", C.parked_at.is_(None), C.messaged_at.is_not(None)),
    "continuity": C.status == "FULFILLED_UNRECONCILED",
    "auto": C.status == "AUTO_VERIFIED",
    "manual": C.status == "MANUAL_VERIFIED",
    "rejected": C.status.in_(("REJECTED", "FAKE")),
}
TAB_LABELS = {
    "review": "در انتظار بررسی", "waiting": "در انتظار پیامک بانک", "parked": "کنار گذاشته", "messaged": "پیام داده‌شده",
    "continuity": "حالت تداوم", "auto": "تایید خودکار", "manual": "تایید دستی", "rejected": "رد شده / جعلی", "all": "همه",
}


async def tab_counts(session: AsyncSession) -> dict[str, int]:
    cols = [func.sum(case((cond, 1), else_=0)).label(tab) for tab, cond in TAB_CONDITIONS.items()]
    row = (await session.execute(select(func.count().label("all"), *cols))).one()
    return {k: int(v or 0) for k, v in row._mapping.items()}


async def review_count(session: AsyncSession) -> int:
    return (await session.execute(select(func.count()).select_from(C).where(TAB_CONDITIONS["review"]))).scalar_one()


async def list_claims(session: AsyncSession, *, tab: str = "review", q: str | None = None, purpose: str | None = None, page: int = 1) -> dict[str, Any]:
    stmt = select(C).order_by(C.paid_clicked_at.desc(), C.id.desc())
    conds = []
    if tab in TAB_CONDITIONS:
        conds.append(TAB_CONDITIONS[tab])
    if purpose:
        conds.append(C.purpose == purpose)
    if q:
        needle = q.strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
        via_reference = select(ReconciliationMatch.claim_id).join(TransactionCandidate, TransactionCandidate.id == ReconciliationMatch.transaction_id).where(TransactionCandidate.reference == needle)
        conds.append(or_(C.invoice_code == q.strip(), text("payment_claims.telegram_id::text LIKE :tg").bindparams(tg=f"{needle}%"), C.ibsng_username.ilike(f"%{q.strip()}%"), C.id.in_(via_reference)))
    if conds:
        stmt = stmt.where(and_(*conds))
    total = (await session.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    claims = (await session.execute(stmt.limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE))).scalars().all()
    ids = [c.id for c in claims]
    matches = (await session.execute(select(ReconciliationMatch, TransactionCandidate).join(TransactionCandidate, TransactionCandidate.id == ReconciliationMatch.transaction_id).where(ReconciliationMatch.claim_id.in_(ids)))).all() if ids else []
    accounts = {a.id: a for a in (await session.execute(select(FinancialAccount))).scalars().all()}
    by_claim: dict[int, list] = {i: [] for i in ids}
    for m, t in matches:
        by_claim[m.claim_id].append((m, t))
    rows = [{"claim": c, "matches": by_claim[c.id], "account": accounts.get(c.target_account_id)} for c in claims]
    # A credit that already settled a claim cannot settle another one: the
    # partial unique index refuses it. The screen has to know that too, or it
    # offers a button whose only outcome is a refusal.
    tx_ids = {t.id for _, t in matches}
    spent = set(
        (
            await session.execute(
                select(ReconciliationMatch.transaction_id).where(
                    ReconciliationMatch.transaction_id.in_(tx_ids), ReconciliationMatch.status.in_(SETTLING_MATCH_STATUSES)
                )
            )
        ).scalars().all()
    ) if tx_ids else set()
    return {"rows": rows, "total": total, "page": page, "page_size": PAGE_SIZE, "spent_transactions": spent}
