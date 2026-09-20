"""The catalog editor: categories, locations, the plan matrix, bulk pricing.

Every rule here is AloBot's own (`app/services/catalog.py` in the linked
checkout) and a test pins the constants against that source, so the day
AloBot changes its plan dimensions this fails loudly instead of writing rows
its bot will not sell.

Two rules are deliberately STRICTER than AloBot's `bind_plan_slot`, which is
called only from screens that already got them right: a fixed-location plan
must name a location and every other category must not, and a price must be
positive (AloBot's own order path refuses a non-positive amount, so a zero
plan is a button that can never be pressed).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.alobot.link import link
from app.alobot.writes import edit

CATEGORIES = ("normal", "prime", "fixed", "junior")
DURATIONS = (1, 2)
USER_COUNTS = (1, 2)
PRICE_MODES = ("percent", "amount")

_PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


class CatalogError(ValueError):
    """A refusal in words an operator can act on. Nothing was written."""


def to_persian_digits(value: str) -> str:
    return value.translate(_PERSIAN_DIGITS)


def format_plan_title(duration_months: int, user_count: int) -> str:
    """AloBot's `format_plan_title`: the stored title, never admin-typed,
    and never carrying the price."""
    return to_persian_digits(f"{duration_months} ماه {user_count} کاربر")


async def _check_group(session: AsyncSession, group_name: str) -> None:
    row = (await session.execute(text("SELECT is_trial FROM groups WHERE name = :name"), {"name": group_name})).first()
    if row is None:
        raise CatalogError(f"گروه «{group_name}» در آلوبات نیست؛ اول گروه‌ها را از IBSng همگام کنید.")
    if row.is_trial:
        raise CatalogError(f"گروه «{group_name}» گروه سرویس تست است و فروختنی نیست.")


def _check_shape(category: str, duration_months: int, user_count: int, location_id: int | None, price: Decimal) -> None:
    if category not in CATEGORIES:
        raise CatalogError("نوع سرویس نامعتبر است.")
    if duration_months not in DURATIONS or user_count not in USER_COUNTS:
        raise CatalogError(f"مدت باید یکی از {DURATIONS} و تعداد کاربر یکی از {USER_COUNTS} باشد.")
    if category == "fixed" and location_id is None:
        raise CatalogError("پلن «لوکیشن ثابت» باید به یک لوکیشن بسته شود.")
    if category != "fixed" and location_id is not None:
        raise CatalogError("فقط «لوکیشن ثابت» لوکیشن می‌گیرد؛ بقیهٔ انواع سرویس بدون لوکیشن‌اند.")
    if price <= 0:
        raise CatalogError("قیمت باید بزرگ‌تر از صفر باشد؛ آلوبات سفارش با مبلغ صفر را رد می‌کند.")


async def bind_slot(db: AsyncSession, actor, *, category: str, duration_months: int, user_count: int, group_name: str, price: Decimal, location_id: int | None) -> None:
    """Create-or-update one slot of the plan matrix, exactly as AloBot's own
    editor does: one Service row per (category, location, duration, users)."""
    _check_shape(category, duration_months, user_count, location_id, price)
    where_loc = "location_id IS NULL" if location_id is None else "location_id = :location_id"
    params: dict[str, Any] = {"category": category, "duration": duration_months, "users": user_count, "location_id": location_id, "group_name": group_name, "price": price, "title": format_plan_title(duration_months, user_count)}
    async with link.session() as read:
        await _check_group(read, group_name)
        before = (await read.execute(text(f"SELECT id, group_name, price FROM services WHERE category=:category AND duration_months=:duration AND user_count=:users AND {where_loc}"), params)).first()
    async with edit(db, actor, action="catalog.bind_slot", entity_type="service", entity_id=f"{category}/{location_id or '-'}/{duration_months}m/{user_count}u", before={"group": before.group_name, "price": str(before.price)} if before else None) as a:
        if before is None:
            await a.execute(
                text("INSERT INTO services (title, category, location_id, duration_months, user_count, group_name, price, is_active, sort_order, created_at, updated_at) "
                     "VALUES (:title, :category, :location_id, :duration, :users, :group_name, :price, true, 0, now(), now())"),
                params,
            )
        else:
            await a.execute(text("UPDATE services SET title=:title, group_name=:group_name, price=:price, updated_at=now() WHERE id=:id"), {**params, "id": before.id})
        a.audit_after = {"group": group_name, "price": str(price)}


async def unbind_slot(db: AsyncSession, actor, *, category: str, duration_months: int, user_count: int, location_id: int | None) -> None:
    where_loc = "location_id IS NULL" if location_id is None else "location_id = :location_id"
    params = {"category": category, "duration": duration_months, "users": user_count, "location_id": location_id}
    async with edit(db, actor, action="catalog.unbind_slot", entity_type="service", entity_id=f"{category}/{location_id or '-'}/{duration_months}m/{user_count}u") as a:
        result = await a.execute(text(f"DELETE FROM services WHERE category=:category AND duration_months=:duration AND user_count=:users AND {where_loc}"), params)
        if result.rowcount == 0:
            raise CatalogError("این خانه از ماتریس پلن‌ها خالی است.")
        a.audit_after = {"removed": result.rowcount}


async def set_service_active(db: AsyncSession, actor, service_id: int, active: bool) -> None:
    async with edit(db, actor, action="catalog.service_active", entity_type="service", entity_id=str(service_id)) as a:
        result = await a.execute(text("UPDATE services SET is_active=:active, updated_at=now() WHERE id=:id"), {"active": active, "id": service_id})
        if result.rowcount == 0:
            raise CatalogError("چنین سرویسی نیست.")
        a.audit_after = {"is_active": active}


# ── Locations ──────────────────────────────────────────────────────────────


async def create_location(db: AsyncSession, actor, *, title: str, flag_emoji: str) -> int:
    """AloBot stores location titles in English (its admin screens are
    English-only because Telegram's RTL layout scrambles mixed text)."""
    title, flag_emoji = title.strip(), flag_emoji.strip()
    if not title or not flag_emoji:
        raise CatalogError("نام لوکیشن و پرچم لازم است.")
    new_id: list[int] = []
    async with edit(db, actor, action="catalog.location_create", entity_type="service_location", entity_id=title) as a:
        row = (await a.execute(text("INSERT INTO service_locations (title, flag_emoji, is_active, sort_order) VALUES (:title, :flag, true, 0) RETURNING id"), {"title": title, "flag": flag_emoji})).one()
        new_id.append(row.id)
        a.audit_after = {"id": row.id, "title": title, "flag": flag_emoji}
    return new_id[0]


async def set_location_active(db: AsyncSession, actor, location_id: int, active: bool) -> None:
    async with edit(db, actor, action="catalog.location_active", entity_type="service_location", entity_id=str(location_id)) as a:
        result = await a.execute(text("UPDATE service_locations SET is_active=:active WHERE id=:id"), {"active": active, "id": location_id})
        if result.rowcount == 0:
            raise CatalogError("چنین لوکیشنی نیست.")
        a.audit_after = {"is_active": active}


async def delete_location(db: AsyncSession, actor, location_id: int) -> None:
    """Refused while any plan still points at it - AloBot's own rule; a
    location that disappears under a bound plan leaves a service nobody can
    describe."""
    async with link.session() as read:
        bound = (await read.execute(text("SELECT count(*) FROM services WHERE location_id = :id"), {"id": location_id})).scalar_one()
    if bound:
        raise CatalogError(f"این لوکیشن هنوز {bound} پلن بسته‌شده دارد؛ اول آن‌ها را بردارید.")
    async with edit(db, actor, action="catalog.location_delete", entity_type="service_location", entity_id=str(location_id)) as a:
        result = await a.execute(text("DELETE FROM service_locations WHERE id=:id"), {"id": location_id})
        if result.rowcount == 0:
            raise CatalogError("چنین لوکیشنی نیست.")
        a.audit_after = {"deleted": True}


# ── Categories ─────────────────────────────────────────────────────────────


async def set_category_enabled(db: AsyncSession, actor, category: str, enabled: bool) -> None:
    """AloBot keeps this in `app_config` under `category_enabled:<name>` and
    reads anything but the exact string "false" as on."""
    if category not in CATEGORIES:
        raise CatalogError("نوع سرویس نامعتبر است.")
    key, value = f"category_enabled:{category}", "true" if enabled else "false"
    async with edit(db, actor, action="catalog.category_enabled", entity_type="app_config", entity_id=key) as a:
        await a.execute(
            text("INSERT INTO app_config (key, value) VALUES (:key, :value) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"),
            {"key": key, "value": value},
        )
        a.audit_after = {"enabled": enabled}


# ── Bulk price ─────────────────────────────────────────────────────────────


def _new_price_sql(mode: str) -> str:
    """ONE expression, used by the preview and by the apply. A preview
    computed a second way is a preview that can lie."""
    if mode == "percent":
        return "ROUND(price * (100 + CAST(:value AS NUMERIC)) / 100)"
    return "ROUND(price + CAST(:value AS NUMERIC))"


def _scope(category: str | None, location_id: int | None) -> tuple[str, dict[str, Any]]:
    clauses, params = ["is_active = true"], {}
    if category:
        if category not in CATEGORIES:
            raise CatalogError("نوع سرویس نامعتبر است.")
        clauses.append("category = :category")
        params["category"] = category
    if location_id is not None:
        clauses.append("location_id = :location_id")
        params["location_id"] = location_id
    return " AND ".join(clauses), params


async def bulk_price(db: AsyncSession, actor, *, mode: str, value: Decimal, category: str | None, location_id: int | None, apply: bool) -> dict[str, Any]:
    """Move every active plan in scope, up or down, by a percentage or a
    fixed amount. Prices land on whole Toman - an amount that is not whole
    Toman cannot be quoted to a customer or matched against a bank SMS.

    A change that would take any plan that is currently sellable to zero or
    below is refused WHOLE, before anything is written, and says how many
    plans it would have broken."""
    if mode not in PRICE_MODES:
        raise CatalogError("نوع تغییر قیمت نامعتبر است.")
    if value == 0:
        raise CatalogError("مقدار تغییر نمی‌تواند صفر باشد.")
    where, params = _scope(category, location_id)
    params["value"] = value
    expr = _new_price_sql(mode)

    async with link.session() as read:
        rows = (await read.execute(text(f"SELECT id, title, category, location_id, price, {expr} AS new_price FROM services WHERE {where} ORDER BY id"), params)).all()
        broken = [r for r in rows if r.price > 0 and r.new_price <= 0]
    if broken:
        raise CatalogError(f"این تغییر قیمت {len(broken)} پلن را به صفر یا کمتر می‌رساند و انجام نشد.")

    preview = [{"id": r.id, "title": r.title, "category": r.category, "location_id": r.location_id, "price": r.price, "new_price": r.new_price} for r in rows]
    if not apply:
        return {"count": len(preview), "rows": preview, "applied": False}

    before = {str(r.id): str(r.price) for r in rows}
    async with edit(db, actor, action="catalog.bulk_price", entity_type="services", entity_id=f"{category or 'all'}/{location_id or 'all'}", before={"prices": before}) as a:
        result = await a.execute(text(f"UPDATE services SET price = {expr}, updated_at = now() WHERE {where}"), params)
        a.audit_after = {"mode": mode, "value": str(value), "changed": result.rowcount}
    return {"count": len(preview), "rows": preview, "applied": True}
