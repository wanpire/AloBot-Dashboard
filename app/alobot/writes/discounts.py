"""Discount codes. AloBot's own normalisation and scope rules
(`app/services/discounts.py`): the code is upper-cased, and a scope that
covers every category is stored as NULL so a category added later is
included rather than silently excluded."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.alobot.link import link
from app.alobot.writes import edit
from app.alobot.writes.catalog import CATEGORIES


class DiscountError(ValueError):
    pass


def normalize_code(code: str) -> str:
    return code.strip().upper()


def stored_categories(categories: list[str] | None) -> str | None:
    if categories and set(categories) != set(CATEGORIES):
        return ",".join(categories)
    return None


BOUND_COLUMNS = ("expires_at", "per_user_limit")


def bounds_available() -> bool:
    """Whether AloBot's `discount_codes` has the expiry and per-customer
    columns yet. Asked of the reflected schema rather than a flag in a file,
    because the answer is a fact about the database this deployment is
    pointed at, and a flag can be wrong."""
    if not link.available:
        return False
    try:
        columns = set(link.t("discount_codes").c.keys())
    except KeyError:
        return False
    return all(column in columns for column in BOUND_COLUMNS)


def _check_bounds(expires_at, per_user_limit, *, now=None) -> None:
    if expires_at is None and per_user_limit is None:
        return
    if not bounds_available():
        raise DiscountError(
            "این نسخهٔ آلوبات هنوز ستون‌های «مهلت» و «سقف هر مشتری» را ندارد؛ تا اجرای مهاجرت، این دو فیلد قابل ذخیره نیستند."
        )
    if per_user_limit is not None and per_user_limit < 1:
        raise DiscountError("سقف هر مشتری باید حداقل ۱ باشد، یا خالی برای بدون سقف.")
    if expires_at is not None:
        import datetime as _dt

        if expires_at <= (now or _dt.datetime.now(_dt.timezone.utc)):
            raise DiscountError("مهلت کد نمی‌تواند در گذشته باشد؛ کدی که همین حالا منقضی است بهتر است اصلاً ساخته نشود.")


def _check(code: str, percent: Decimal, usage_limit: int | None, categories: list[str] | None) -> None:
    if not code:
        raise DiscountError("کد تخفیف خالی است.")
    if not (0 < percent <= 100):
        raise DiscountError("درصد تخفیف باید بین ۱ تا ۱۰۰ باشد.")
    if usage_limit is not None and usage_limit < 1:
        raise DiscountError("سقف استفاده باید دست‌کم ۱ باشد، یا خالی برای نامحدود.")
    for category in categories or []:
        if category not in CATEGORIES:
            raise DiscountError(f"نوع سرویس «{category}» وجود ندارد.")


async def create(
    db: AsyncSession, actor, *, code: str, percent: Decimal, usage_limit: int | None, categories: list[str] | None,
    is_public: bool, expires_at=None, per_user_limit: int | None = None,
) -> None:
    code = normalize_code(code)
    _check(code, percent, usage_limit, categories)
    _check_bounds(expires_at, per_user_limit)
    params: dict[str, Any] = {"code": code, "percent": percent, "usage_limit": usage_limit, "categories": stored_categories(categories), "is_public": is_public}
    # The two bound columns are only named in the statement when AloBot has
    # them, so this file works against both schemas without a flag.
    extra_columns = ", expires_at, per_user_limit" if bounds_available() else ""
    extra_values = ", :expires_at, :per_user_limit" if bounds_available() else ""
    if bounds_available():
        params |= {"expires_at": expires_at, "per_user_limit": per_user_limit}
    try:
        async with edit(db, actor, action="discount.create", entity_type="discount_code", entity_id=code) as a:
            await a.execute(
                text(f"INSERT INTO discount_codes (code, percent, usage_limit, used_count, categories, is_active, is_public, created_at{extra_columns}) "
                     f"VALUES (:code, :percent, :usage_limit, 0, :categories, true, :is_public, now(){extra_values})"),
                params,
            )
            a.audit_after = {**params, "percent": str(percent), "expires_at": str(expires_at) if expires_at else None}
    except IntegrityError:
        raise DiscountError(f"کد «{code}» قبلاً ثبت شده است.") from None


async def update(db: AsyncSession, actor, code_id: int, *, percent: Decimal, usage_limit: int | None, categories: list[str] | None, is_public: bool) -> None:
    _check("x", percent, usage_limit, categories)
    async with link.session() as read:
        before = (await read.execute(text("SELECT code, percent, usage_limit, categories, is_public FROM discount_codes WHERE id=:id"), {"id": code_id})).first()
    if before is None:
        raise DiscountError("چنین کدی نیست.")
    async with edit(db, actor, action="discount.update", entity_type="discount_code", entity_id=before.code, before={"percent": str(before.percent), "usage_limit": before.usage_limit, "categories": before.categories, "is_public": before.is_public}) as a:
        await a.execute(
            text("UPDATE discount_codes SET percent=:percent, usage_limit=:usage_limit, categories=:categories, is_public=:is_public WHERE id=:id"),
            {"percent": percent, "usage_limit": usage_limit, "categories": stored_categories(categories), "is_public": is_public, "id": code_id},
        )
        a.audit_after = {"percent": str(percent), "usage_limit": usage_limit, "categories": stored_categories(categories), "is_public": is_public}


async def set_active(db: AsyncSession, actor, code_id: int, active: bool) -> None:
    async with edit(db, actor, action="discount.active", entity_type="discount_code", entity_id=str(code_id)) as a:
        result = await a.execute(text("UPDATE discount_codes SET is_active=:active WHERE id=:id"), {"active": active, "id": code_id})
        if result.rowcount == 0:
            raise DiscountError("چنین کدی نیست.")
        a.audit_after = {"is_active": active}


async def delete(db: AsyncSession, actor, code_id: int) -> None:
    """Only while nobody has used it. A used code's history lives in
    `discount_code_usages`, and the sales report reads those rows back by
    code - deleting the code would leave a report nobody can explain."""
    async with link.session() as read:
        row = (await read.execute(text("SELECT code, used_count FROM discount_codes WHERE id=:id"), {"id": code_id})).first()
        if row is None:
            raise DiscountError("چنین کدی نیست.")
        used = (await read.execute(text("SELECT count(*) FROM discount_code_usages WHERE discount_code_id=:id"), {"id": code_id})).scalar_one()
    if used or row.used_count:
        raise DiscountError(f"این کد {max(used, row.used_count)} بار مصرف شده و حذف نمی‌شود؛ غیرفعالش کنید.")
    async with edit(db, actor, action="discount.delete", entity_type="discount_code", entity_id=row.code) as a:
        await a.execute(text("DELETE FROM discount_codes WHERE id=:id"), {"id": code_id})
        a.audit_after = {"deleted": True}


async def usages(db: AsyncSession, code_id: int) -> list:
    async with link.session() as read:
        return list((await read.execute(
            text("SELECT u.id, u.telegram_id, u.code, u.percent, u.original_amount, u.amount, u.used_at, u.payment_id "
                 "FROM discount_code_usages u WHERE u.discount_code_id = :id ORDER BY u.used_at DESC LIMIT 200"),
            {"id": code_id},
        )).all())
