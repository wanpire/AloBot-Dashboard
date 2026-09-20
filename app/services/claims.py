"""Claims: AloBot's pending card payments, mirrored here so the bank's word
can be attached to them. Read-only towards AloBot; AloBot's own decision is
carried in `alobot_status` and never confused with ours."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alobot.codes import encode_id
from app.alobot.link import link
from app.alobot.money import toman_to_rial
from app.core.logging import get_logger
from app.models import PaymentCard, PaymentClaim
from app.services import continuity

log = get_logger(__name__)


async def _alobot_card_number(alobot_session) -> str | None:
    cfg = link.t("app_config")
    value = (await alobot_session.execute(select(cfg.c.value).where(cfg.c.key == "card_number"))).scalar_one_or_none()
    if not value:
        return None
    digits = "".join(ch for ch in str(value).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")) if ch.isdigit())
    return digits if len(digits) == 16 else None


async def _account_for_card(session: AsyncSession, card_number: str | None) -> int | None:
    if card_number is None:
        return None
    card = (await session.execute(select(PaymentCard).where(PaymentCard.card_number == card_number))).scalar_one_or_none()
    return card.account_id if card else None


async def mirror_claims(session: AsyncSession, now: dt.datetime) -> dict[str, Any]:
    """One claim per pending AloBot card payment; AloBot's status tracked on
    the ones we already have. Idempotent: keyed on the AloBot payment id."""
    if not link.available:
        return {"created": 0, "updated": 0, "resolved": 0, "skipped": "alobot_unavailable"}
    p = link.t("payments")
    known = {c.alobot_payment_id: c for c in (await session.execute(select(PaymentClaim))).scalars().all()}
    unresolved_ids = [pid for pid, c in known.items() if c.alobot_status == "pending"]
    created = updated = resolved = 0
    continuity_on = await continuity.is_active(session, now)
    async with link.session() as alobot:
        card_number = await _alobot_card_number(alobot)
        account_id = await _account_for_card(session, card_number)
    # A card registered AFTER a claim was mirrored: resolve the open claims
    # that still point at that card by number, so they can be matched.
    for claim in known.values():
        if claim.target_account_id is None and claim.target_card_number and claim.status in ("PENDING", "FULFILLED_UNRECONCILED"):
            resolved_to = await _account_for_card(session, claim.target_card_number)
            if resolved_to is not None:
                claim.target_account_id = resolved_to
                resolved += 1
    async with link.session() as alobot:
        stmt = select(
            p.c.id, p.c.telegram_id, p.c.purpose, p.c.amount, p.c.ibsng_username, p.c.receipt_file_id, p.c.status,
            p.c.created_at, p.c.resolved_at, p.c.invoice_number,
        ).where(p.c.method == "card")
        if unresolved_ids:
            stmt = stmt.where((p.c.status == "pending") | (p.c.id.in_(unresolved_ids)))
        else:
            stmt = stmt.where(p.c.status == "pending")
        rows = (await alobot.execute(stmt)).all()
    for row in rows:
        claim = known.get(row.id)
        if claim is None:
            if row.status != "pending":
                continue
            session.add(
                PaymentClaim(
                    alobot_payment_id=row.id, telegram_id=row.telegram_id, purpose=row.purpose or "purchase",
                    expected_amount_irr=toman_to_rial(row.amount), ibsng_username=row.ibsng_username or "",
                    invoice_code=row.invoice_number or encode_id(row.id), target_card_number=card_number, target_account_id=account_id,
                    paid_clicked_at=row.created_at, receipt_file_id=row.receipt_file_id, alobot_status=row.status,
                    status="FULFILLED_UNRECONCILED" if continuity_on else "PENDING", continuity=continuity_on,
                )
            )
            created += 1
        elif claim.alobot_status != row.status:
            claim.alobot_status = row.status
            claim.alobot_resolved_at = row.resolved_at
            updated += 1
    await session.commit()
    if created or updated or resolved:
        log.info("claims.mirrored", created=created, updated=updated, resolved=resolved)
    return {"created": created, "updated": updated, "resolved": resolved}
