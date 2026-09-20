"""Topping up a reseller's balance in AloBot.

AloBot's `resellers.balance` is one `Numeric` column with no ledger behind
it, and AloBot's purchase path reads it, subtracts in Python and commits
without a row lock:

    reseller = await get_reseller(session, telegram_id)
    reseller.balance -= cost
    await session.commit()

A top-up landing inside that window would be overwritten and the money would
be gone with nothing to show for it. The lock belongs in AloBot and is being
added there as its own change; this side does what it can regardless:

1. **Write atomically.** `balance = balance + :amount` in one statement, so
   two writes of this kind can never lose each other, and the window narrows
   to AloBot's own stale read.
2. **Keep a ledger.** Every top-up is recorded here with the balance before
   and after, because AloBot keeps no history of its own.
3. **Read it back and say so when it did not land.** A lost top-up should be
   noticed within seconds by an operator, not discovered by a reseller.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.alobot.link import link
from app.alobot.money import toman_to_rial
from app.alobot.writes import edit
from app.core.logging import get_logger
from app.models import ResellerTopUp
from app.services import alerts
from app.web.format import fa_number

log = get_logger(__name__)


class ResellerError(ValueError):
    pass


async def _read_balance(telegram_id: int) -> Decimal | None:
    async with link.session() as read:
        row = (await read.execute(text("SELECT balance FROM resellers WHERE telegram_id=:t"), {"t": telegram_id})).first()
    return None if row is None else row.balance


async def top_up(db: AsyncSession, actor, *, telegram_id: int, amount_toman: Decimal, note: str | None = None) -> Decimal:
    if amount_toman <= 0:
        raise ResellerError("مبلغ شارژ باید بیشتر از صفر باشد.")
    before = await _read_balance(telegram_id)
    if before is None:
        raise ResellerError("این شناسه نمایندهٔ فروش نیست.")

    expected = before + amount_toman
    async with edit(
        db, actor, action="reseller.topup", entity_type="reseller", entity_id=str(telegram_id),
        before={"balance_toman": str(before)},
    ) as a:
        # One statement, computed in the database: this cannot lose a
        # concurrent top-up the way a read-modify-write would.
        await a.execute(
            text("UPDATE resellers SET balance = balance + :amount WHERE telegram_id = :t"),
            {"amount": amount_toman, "t": telegram_id},
        )
        a.audit_after = {"balance_toman": str(expected), "added_toman": str(amount_toman)}

    observed = await _read_balance(telegram_id)
    landed = observed is not None and observed >= expected
    db.add(
        ResellerTopUp(
            telegram_id=telegram_id,
            amount_irr=toman_to_rial(amount_toman),
            balance_before_irr=toman_to_rial(before),
            balance_after_irr=toman_to_rial(observed if observed is not None else before),
            verified=landed,
            note=(note or None),
            operator_id=getattr(actor, "id", None),
            operator_email=actor.email,
        )
    )
    await db.commit()

    if not landed:
        # The purchase path overwrote it, or the row vanished. Either way the
        # reseller does not have the money and somebody has to look now.
        log.error("reseller.topup_lost", telegram_id=telegram_id, expected=str(expected), observed=str(observed))
        await alerts.alert(
            db,
            f"topup:{telegram_id}",
            f"شارژ {fa_number(int(amount_toman))} تومانی نمایندهٔ {telegram_id} ثبت نشد: "
            f"موجودی باید {fa_number(int(expected))} می‌شد ولی {fa_number(int(observed or 0))} است. دوباره بررسی کنید.",
        )
        raise ResellerError(
            f"شارژ ثبت نشد: موجودی باید {fa_number(int(expected))} تومان می‌شد ولی {fa_number(int(observed or 0))} تومان است. "
            "این شارژ در دفترچهٔ داشبورد ثبت شد و هشدار فرستاده شد؛ قبل از تکرار، موجودی را بررسی کنید."
        )
    return observed
