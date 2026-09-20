"""Bank-side figures: how much of the shop's verification is automatic, how
fast the bank confirms, what landed where, and what nobody claimed."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FinancialAccount, PaymentClaim, ReconciliationMatch, TransactionCandidate


async def summary(session: AsyncSession, since: dt.datetime) -> dict[str, Any]:
    C, M, T = PaymentClaim, ReconciliationMatch, TransactionCandidate
    auto = (await session.execute(select(func.count()).select_from(C).where(C.status == "AUTO_VERIFIED", C.verified_at >= since))).scalar_one()
    manual = (await session.execute(select(func.count()).select_from(C).where(C.status == "MANUAL_VERIFIED", C.verified_at >= since))).scalar_one()
    rejected = (await session.execute(select(func.count()).select_from(C).where(C.status.in_(("REJECTED", "FAKE")), C.decided_at >= since))).scalar_one()
    avg_delta = (await session.execute(select(func.avg(M.time_delta_seconds)).where(M.status == "AUTO_VERIFIED", M.decided_at >= since))).scalar_one()
    settling = select(M.transaction_id).where(M.status.in_(("AUTO_VERIFIED", "CONFIRMED")))
    credits = and_(T.direction == "CREDIT", T.disposition == "ACTIONABLE", T.bank_timestamp >= since, T.amount_irr.is_not(None))
    by_account = (
        await session.execute(
            select(FinancialAccount.display_name, func.count(), func.coalesce(func.sum(T.amount_irr), 0))
            .select_from(T.__table__.outerjoin(FinancialAccount.__table__, FinancialAccount.id == T.account_id))
            .where(credits).group_by(FinancialAccount.display_name).order_by(func.sum(T.amount_irr).desc())
        )
    ).all()
    unmatched = (await session.execute(select(func.count(), func.coalesce(func.sum(T.amount_irr), 0)).where(credits, T.id.not_in(settling)))).one()
    declined = (await session.execute(select(func.count(), func.coalesce(func.sum(T.amount_irr), 0)).where(T.direction == "CREDIT", T.disposition == "DECLINED_INCOME", T.bank_timestamp >= since))).one()
    verified_total = auto + manual
    return {
        "auto": auto, "manual": manual, "rejected": rejected,
        "automation_rate": round(100 * auto / verified_total) if verified_total else None,
        "avg_delta_seconds": int(avg_delta) if avg_delta is not None else None,
        "by_account": [(name or "بدون حساب", n, int(total)) for name, n, total in by_account],
        "unmatched": (unmatched[0], int(unmatched[1])),
        "declined": (declined[0], int(declined[1])),
    }
