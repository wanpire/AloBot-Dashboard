"""Reading and correcting what the parser produced."""

from __future__ import annotations

import datetime as dt
import hashlib
from typing import Any

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Device, FinancialAccount, SmsEvent, TransactionCandidate
from app.models.ingest import DISPOSITIONS
from app.services import ingest as ingest_service
from app.services.audit import audit_row
from app.sms import parse

PAGE_SIZE = 100


class TransactionError(ValueError):
    pass


async def list_transactions(session: AsyncSession, *, direction: str | None = None, disposition: str | None = None, account_id: int | None = None, q: str | None = None, page: int = 1) -> dict[str, Any]:
    stmt = (
        select(TransactionCandidate, SmsEvent, FinancialAccount, Device)
        .join(SmsEvent, SmsEvent.id == TransactionCandidate.sms_event_id)
        .outerjoin(FinancialAccount, FinancialAccount.id == TransactionCandidate.account_id)
        .join(Device, Device.id == SmsEvent.device_id)
    )
    conds = []
    if direction:
        conds.append(TransactionCandidate.direction == direction)
    if disposition:
        conds.append(TransactionCandidate.disposition == disposition)
    if account_id:
        conds.append(TransactionCandidate.account_id == account_id)
    if q:
        needle = q.strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
        conds.append((TransactionCandidate.reference == needle) | (TransactionCandidate.account_hint == needle[-4:]) | SmsEvent.body.ilike(f"%{q.strip()}%"))
    if conds:
        stmt = stmt.where(and_(*conds))
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await session.execute(stmt.order_by(TransactionCandidate.bank_timestamp.desc(), TransactionCandidate.id.desc()).limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE))).all()
    accounts = (await session.execute(select(FinancialAccount).order_by(FinancialAccount.display_name))).scalars().all()
    return {"rows": rows, "total": total, "page": page, "page_size": PAGE_SIZE, "accounts": accounts}


async def unparsed_events(session: AsyncSession, limit: int = 200) -> list[tuple[SmsEvent, Device]]:
    stmt = (
        select(SmsEvent, Device).join(Device, Device.id == SmsEvent.device_id)
        .where(SmsEvent.classification == "UNKNOWN").order_by(SmsEvent.received_at.desc()).limit(limit)
    )
    return list((await session.execute(stmt)).all())


async def assign_account(session: AsyncSession, actor, transaction_id: int, account_id: int | None) -> TransactionCandidate:
    tx = await session.get(TransactionCandidate, transaction_id)
    if tx is None:
        raise TransactionError("چنین تراکنشی نیست.")
    if account_id is not None and await session.get(FinancialAccount, account_id) is None:
        raise TransactionError("چنین حسابی نیست.")
    before = tx.account_id
    tx.account_id = account_id
    session.add(audit_row(action="transaction.assign_account", entity_type="transaction", entity_id=str(tx.id), actor_role=actor.role, actor_operator_id=actor.id, before={"account_id": before}, after={"account_id": account_id}))
    await session.commit()
    return tx


async def set_disposition(session: AsyncSession, actor, transaction_id: int, disposition: str, note: str) -> TransactionCandidate:
    if disposition not in DISPOSITIONS:
        raise TransactionError("وضعیت نامعتبر است.")
    tx = await session.get(TransactionCandidate, transaction_id)
    if tx is None:
        raise TransactionError("چنین تراکنشی نیست.")
    before = tx.disposition
    tx.disposition, tx.disposition_note = disposition, (note.strip() or None)
    session.add(audit_row(action="transaction.disposition", entity_type="transaction", entity_id=str(tx.id), actor_role=actor.role, actor_operator_id=actor.id, before={"disposition": before}, after={"disposition": disposition, "note": tx.disposition_note}))
    await session.commit()
    return tx


async def reparse_unparsed(session: AsyncSession, actor, apply: bool) -> dict[str, int]:
    """Run the current parser (built-ins + enabled patterns) over every
    UNKNOWN event. Dry-run counts what would change; apply writes it."""
    patterns = await ingest_service.load_patterns(session)
    events = (await session.execute(select(SmsEvent).where(SmsEvent.classification == "UNKNOWN"))).scalars().all()
    would = 0
    for ev in events:
        result = parse(ev.body, sender=ev.sender, patterns=patterns)
        if result.classification == "UNKNOWN":
            continue
        would += 1
        if apply:
            ev.classification, ev.parser_id, ev.parser_version = result.classification, result.parser_id, result.parser_version
            if result.matched:
                await ingest_service.persist_candidate(session, ev, result, ev.sms_timestamp)
    if apply:
        session.add(audit_row(action="transactions.reparse", entity_type="sms_events", entity_id="unknown", actor_role=actor.role, actor_operator_id=actor.id, after={"reparsed": would}))
        await session.commit()
    return {"examined": len(events), "changed": would}


async def coverage(session: AsyncSession, since: dt.datetime) -> list[dict[str, Any]]:
    parsed = case((SmsEvent.classification.in_(("BANK_TRANSACTION", "BALANCE")), 1), else_=0)
    stmt = (
        select(
            SmsEvent.sender, func.count().label("total"), func.sum(parsed).label("parsed"),
            func.sum(case((SmsEvent.classification == "OTP", 1), else_=0)).label("otp"),
            func.sum(case((SmsEvent.classification == "PROMOTIONAL", 1), else_=0)).label("promo"),
            func.sum(case((SmsEvent.classification == "UNKNOWN", 1), else_=0)).label("unknown"),
        )
        .where(SmsEvent.received_at >= since)
        .group_by(SmsEvent.sender)
        .order_by(func.count().desc())
    )
    out = []
    for row in (await session.execute(stmt)).all():
        out.append({"sender": row.sender, "total": row.total, "parsed": row.parsed, "otp": row.otp, "promo": row.promo, "unknown": row.unknown, "percent": round(100 * row.parsed / row.total) if row.total else 0})
    return out
