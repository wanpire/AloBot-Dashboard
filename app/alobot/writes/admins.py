"""AloBot's bot admins and its payment-reviewer allow-list.

Two different things that look alike: `admin_users` decides who may use the
bot's admin screens at all (three hierarchical tiers), and
`payment_review_admin_ids` in `app_config` narrows who may decide on money.
AloBot reads an EMPTY allow-list as "every admin may review" - deliberately,
so an unconfigured shop never has a payment nobody can approve - so clearing
it widens access rather than closing it, and the screen has to say so.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.alobot.link import link
from app.alobot.writes import edit

LEVELS = ("support", "sales", "full")
LEVEL_LABELS = {"support": "پشتیبانی", "sales": "فروش", "full": "مدیر کامل"}
REVIEWERS_KEY = "payment_review_admin_ids"


class AdminError(ValueError):
    pass


async def listing(db: AsyncSession) -> list:
    async with link.session() as read:
        return list((await read.execute(text("SELECT id, telegram_id, level, created_at FROM admin_users ORDER BY created_at, id"))).all())


async def _full_admin_count(exclude: int | None = None) -> int:
    async with link.session() as read:
        return (await read.execute(text("SELECT count(*) FROM admin_users WHERE level='full' AND telegram_id <> COALESCE(:x, -1)"), {"x": exclude})).scalar_one()


def _check_level(level: str) -> None:
    if level not in LEVELS:
        raise AdminError(f"سطح دسترسی باید یکی از {'، '.join(LEVEL_LABELS.values())} باشد.")


async def add(db: AsyncSession, actor, *, telegram_id: int, level: str) -> None:
    _check_level(level)
    async with link.session() as read:
        exists = (await read.execute(text("SELECT id FROM admin_users WHERE telegram_id=:t"), {"t": telegram_id})).first()
    if exists:
        raise AdminError("این شناسه از قبل ادمین است.")
    async with edit(db, actor, action="botadmin.add", entity_type="admin_user", entity_id=str(telegram_id)) as a:
        await a.execute(text("INSERT INTO admin_users (telegram_id, level, created_at) VALUES (:t, :l, now())"), {"t": telegram_id, "l": level})
        a.audit_after = {"level": level}


async def set_level(db: AsyncSession, actor, *, telegram_id: int, level: str) -> None:
    _check_level(level)
    async with link.session() as read:
        row = (await read.execute(text("SELECT level FROM admin_users WHERE telegram_id=:t"), {"t": telegram_id})).first()
    if row is None:
        raise AdminError("چنین ادمینی نیست.")
    if row.level == "full" and level != "full" and await _full_admin_count(exclude=telegram_id) == 0:
        raise AdminError("آخرین «مدیر کامل» را نمی‌توان پایین آورد؛ اول یک مدیر کامل دیگر بسازید.")
    async with edit(db, actor, action="botadmin.level", entity_type="admin_user", entity_id=str(telegram_id), before={"level": row.level}) as a:
        await a.execute(text("UPDATE admin_users SET level=:l WHERE telegram_id=:t"), {"l": level, "t": telegram_id})
        a.audit_after = {"level": level}


async def remove(db: AsyncSession, actor, *, telegram_id: int) -> None:
    async with link.session() as read:
        row = (await read.execute(text("SELECT level FROM admin_users WHERE telegram_id=:t"), {"t": telegram_id})).first()
    if row is None:
        raise AdminError("چنین ادمینی نیست.")
    if row.level == "full" and await _full_admin_count(exclude=telegram_id) == 0:
        raise AdminError("آخرین «مدیر کامل» را نمی‌توان حذف کرد.")
    async with edit(db, actor, action="botadmin.remove", entity_type="admin_user", entity_id=str(telegram_id), before={"level": row.level}) as a:
        await a.execute(text("DELETE FROM admin_users WHERE telegram_id=:t"), {"t": telegram_id})
        # The allow-list must not keep naming somebody who is no longer an admin.
        current = await _reviewer_ids(a)
        if telegram_id in current:
            await _write_reviewers(a, [t for t in current if t != telegram_id])
        a.audit_after = {"removed": True}


async def _reviewer_ids(session) -> list[int]:
    row = (await session.execute(text("SELECT value FROM app_config WHERE key=:k"), {"k": REVIEWERS_KEY})).first()
    if row is None or not row.value:
        return []
    return [int(part) for part in row.value.split(",") if part.strip()]


async def _write_reviewers(session, ids: list[int]) -> None:
    await session.execute(
        text("INSERT INTO app_config (key, value) VALUES (:k, :v) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"),
        {"k": REVIEWERS_KEY, "v": ",".join(str(t) for t in ids)},
    )


async def reviewers(db: AsyncSession) -> list[int]:
    async with link.session() as read:
        return await _reviewer_ids(read)


async def toggle_reviewer(db: AsyncSession, actor, telegram_id: int) -> list[int]:
    async with link.session() as read:
        known = (await read.execute(text("SELECT id FROM admin_users WHERE telegram_id=:t"), {"t": telegram_id})).first()
        current = await _reviewer_ids(read)
    if known is None:
        raise AdminError("فقط ادمین‌های ربات می‌توانند بررسی‌کنندهٔ پرداخت باشند.")
    updated = [t for t in current if t != telegram_id] if telegram_id in current else current + [telegram_id]
    async with edit(db, actor, action="botadmin.reviewer_toggle", entity_type="app_config", entity_id=REVIEWERS_KEY, before={"ids": current}) as a:
        await _write_reviewers(a, updated)
        a.audit_after = {"ids": updated}
    return updated


async def reset_reviewers(db: AsyncSession, actor) -> None:
    """Back to "every admin may review" - which is wider, not narrower."""
    async with link.session() as read:
        current = await _reviewer_ids(read)
    async with edit(db, actor, action="botadmin.reviewer_reset", entity_type="app_config", entity_id=REVIEWERS_KEY, before={"ids": current}) as a:
        await _write_reviewers(a, [])
        a.audit_after = {"ids": []}
