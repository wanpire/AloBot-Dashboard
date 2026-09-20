"""Blocking a customer out of AloBot's bot.

There is nothing to ask AloBot to do here. `BlockedUserMiddleware` reads
`bot_users.is_blocked` from the database on every update, before any handler
runs, and AloBot's own `block_user` sets that flag and nothing else. Setting
it from here is the same act, seen by AloBot on the person's next tap.

Two of AloBot's rules travel with it, and both are refusals rather than
silent no-ops:

- That middleware exempts admins and resellers, so a flag set on one of them
  changes nothing. Reporting success would be a lie.
- A block closes the bot door only. The person's VPN service keeps running
  until it expires; nothing here touches IBSng, and this project never will.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.alobot.link import link
from app.alobot.writes import edit


class BotUserError(ValueError):
    pass


async def state(telegram_id: int) -> bool | None:
    """True/False if the bot knows this person, None if it has never seen
    them (which is still blockable - see `block`)."""
    async with link.session() as read:
        row = (await read.execute(text("SELECT is_blocked FROM bot_users WHERE telegram_id=:t"), {"t": telegram_id})).first()
    return None if row is None else bool(row.is_blocked)


async def _refuse_if_exempt(telegram_id: int) -> None:
    async with link.session() as read:
        if (await read.execute(text("SELECT 1 FROM admin_users WHERE telegram_id=:t"), {"t": telegram_id})).first():
            raise BotUserError("این شناسه ادمین ربات است و آلوبات ادمین‌ها را از مسدودسازی مستثنا می‌کند؛ مسدود کردنش هیچ اثری ندارد.")
        if (await read.execute(text("SELECT 1 FROM resellers WHERE telegram_id=:t"), {"t": telegram_id})).first():
            raise BotUserError("این شناسه نمایندهٔ فروش است و آلوبات نمایندگان را مستثنا می‌کند؛ مسدود کردنش هیچ اثری ندارد.")


async def block(db: AsyncSession, actor, *, telegram_id: int) -> None:
    await _refuse_if_exempt(telegram_id)
    before = await state(telegram_id)
    if before is True:
        raise BotUserError("این کاربر از قبل مسدود است.")
    async with edit(db, actor, action="botuser.block", entity_type="bot_user", entity_id=str(telegram_id), before={"is_blocked": before}) as a:
        if before is None:
            # Never seen by the bot - AloBot's own block_user creates the row
            # for exactly this case, a receipt sender who never pressed start.
            await a.execute(
                text("INSERT INTO bot_users (telegram_id, is_blocked) VALUES (:t, true) ON CONFLICT (telegram_id) DO UPDATE SET is_blocked = true"),
                {"t": telegram_id},
            )
        else:
            await a.execute(text("UPDATE bot_users SET is_blocked = true WHERE telegram_id = :t"), {"t": telegram_id})
        a.audit_after = {"is_blocked": True}


async def unblock(db: AsyncSession, actor, *, telegram_id: int) -> None:
    before = await state(telegram_id)
    if before is not True:
        raise BotUserError("این کاربر مسدود نیست.")
    async with edit(db, actor, action="botuser.unblock", entity_type="bot_user", entity_id=str(telegram_id), before={"is_blocked": True}) as a:
        await a.execute(text("UPDATE bot_users SET is_blocked = false WHERE telegram_id = :t"), {"t": telegram_id})
        a.audit_after = {"is_blocked": False}
