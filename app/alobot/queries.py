"""Every read of AloBot's tables, as plain SELECTs over the reflected tables.

Each function takes the read-only session and returns dicts and rows the
templates can render. Nothing here writes, and the role behind the session
could not write anyway; `tests/test_alobot_pages.py` records every statement
the engine sees and asserts they are all SELECTs.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import Row, and_, case, cast, func, or_, select, String
from sqlalchemy.ext.asyncio import AsyncSession

from app.alobot.codes import decode_id
from app.alobot.link import link
from app.alobot.time import tehran_day_bounds, tehran_today

PAGE_SIZE = 50


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _rows(result) -> list[Row]:
    return list(result.all())


async def overview(session: AsyncSession, now: dt.datetime | None = None) -> dict[str, Any]:
    now = now or _now()
    p, v, b = link.t("payments"), link.t("vpn_users"), link.t("bot_users")
    start, end = tehran_day_bounds(tehran_today(now))
    today = and_(p.c.resolved_at >= start, p.c.resolved_at < end)

    async def scalar(stmt):
        return (await session.execute(stmt)).scalar_one()

    pending_card = await scalar(select(func.count()).select_from(p).where(p.c.status == "pending", p.c.method == "card"))
    approved_today = await scalar(select(func.count()).select_from(p).where(p.c.status == "approved", today))
    revenue_today = await scalar(select(func.coalesce(func.sum(p.c.amount), 0)).where(p.c.status == "approved", today))
    active_accounts = await scalar(select(func.count()).select_from(v).where(v.c.expires_at > now))
    expiring_3d = await scalar(
        select(func.count()).select_from(v).where(v.c.expires_at > now, v.c.expires_at <= now + dt.timedelta(days=3))
    )
    unknown_expiry = await scalar(select(func.count()).select_from(v).where(v.c.expires_at.is_(None), v.c.is_trial.is_(False)))
    trials_today = await scalar(
        select(func.count()).select_from(v).where(v.c.is_trial.is_(True), v.c.created_at >= start, v.c.created_at < end)
    )
    new_customers_today = await scalar(
        select(func.count()).select_from(b).where(b.c.first_seen_at >= start, b.c.first_seen_at < end)
    )
    pending_list = _rows(
        await session.execute(
            select(p.c.id, p.c.telegram_id, p.c.amount, p.c.ibsng_username, p.c.purpose, p.c.created_at, p.c.receipt_file_id)
            .where(p.c.status == "pending", p.c.method == "card")
            .order_by(p.c.created_at.asc())
            .limit(10)
        )
    )
    recent_approved = _rows(
        await session.execute(
            select(p.c.id, p.c.telegram_id, p.c.amount, p.c.method, p.c.purpose, p.c.auto_approved, p.c.resolved_at, p.c.invoice_number)
            .where(p.c.status == "approved")
            .order_by(p.c.resolved_at.desc())
            .limit(10)
        )
    )
    return {
        "pending_card": pending_card,
        "approved_today": approved_today,
        "revenue_today": Decimal(revenue_today),
        "active_accounts": active_accounts,
        "expiring_3d": expiring_3d,
        "unknown_expiry": unknown_expiry,
        "trials_today": trials_today,
        "new_customers_today": new_customers_today,
        "pending_list": pending_list,
        "recent_approved": recent_approved,
    }


async def stats(session: AsyncSession, start: dt.datetime, end: dt.datetime) -> dict[str, Any]:
    p, v, b, s, r, u = (link.t(n) for n in ("payments", "vpn_users", "bot_users", "services", "resellers", "discount_code_usages"))
    in_period = and_(p.c.resolved_at >= start, p.c.resolved_at < end)
    approved = and_(p.c.status == "approved", in_period)

    async def scalar(stmt):
        return (await session.execute(stmt)).scalar_one()

    async def grouped(column, where):
        rows = _rows(await session.execute(select(column, func.count()).where(where).group_by(column).order_by(column)))
        return [(k, n) for k, n in rows]

    by_category = _rows(
        await session.execute(
            select(s.c.category, func.count(), func.coalesce(func.sum(p.c.amount), 0))
            .select_from(p.join(s, s.c.id == p.c.service_id))
            .where(approved)
            .group_by(s.c.category)
            .order_by(s.c.category)
        )
    )
    now = _now()
    return {
        "orders": await scalar(select(func.count()).select_from(p).where(approved)),
        "revenue": Decimal(await scalar(select(func.coalesce(func.sum(p.c.amount), 0)).where(approved))),
        "by_method": await grouped(p.c.method, approved),
        "by_purpose": await grouped(p.c.purpose, approved),
        "by_category": [(c, n, Decimal(a)) for c, n, a in by_category],
        "auto_approved": await scalar(select(func.count()).select_from(p).where(approved, p.c.auto_approved.is_(True))),
        "rejected": await scalar(select(func.count()).select_from(p).where(p.c.status == "rejected", in_period)),
        "revoked": await scalar(select(func.count()).select_from(p).where(p.c.status == "revoked", in_period)),
        "discount_uses": await scalar(select(func.count()).select_from(u).where(u.c.used_at >= start, u.c.used_at < end)),
        "discount_given": Decimal(
            await scalar(
                select(func.coalesce(func.sum(u.c.original_amount - u.c.amount), 0)).where(u.c.used_at >= start, u.c.used_at < end)
            )
        ),
        "new_customers": await scalar(select(func.count()).select_from(b).where(b.c.first_seen_at >= start, b.c.first_seen_at < end)),
        "snapshot": {
            "customers": await scalar(select(func.count()).select_from(b)),
            "blocked": await scalar(select(func.count()).select_from(b).where(b.c.is_blocked.is_(True))),
            "accounts": await scalar(select(func.count()).select_from(v)),
            "active_accounts": await scalar(select(func.count()).select_from(v).where(v.c.expires_at > now)),
            "trial_accounts": await scalar(select(func.count()).select_from(v).where(v.c.is_trial.is_(True))),
            "resellers": await scalar(select(func.count()).select_from(r)),
            "reseller_balance": Decimal(await scalar(select(func.coalesce(func.sum(r.c.balance), 0)))),
        },
    }


def _digits(q: str) -> str:
    return q.strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))


async def search_customers(session: AsyncSession, q: str | None, limit: int = PAGE_SIZE) -> list[Row]:
    b, v, p = link.t("bot_users"), link.t("vpn_users"), link.t("payments")
    accounts = select(v.c.telegram_id, func.count().label("accounts")).group_by(v.c.telegram_id).subquery()
    paid = (
        select(p.c.telegram_id, func.count().label("orders"), func.coalesce(func.sum(p.c.amount), 0).label("spent"))
        .where(p.c.status == "approved")
        .group_by(p.c.telegram_id)
        .subquery()
    )
    stmt = (
        select(
            b.c.telegram_id, b.c.username, b.c.first_seen_at, b.c.is_blocked,
            func.coalesce(accounts.c.accounts, 0).label("accounts"),
            func.coalesce(paid.c.orders, 0).label("orders"),
            func.coalesce(paid.c.spent, 0).label("spent"),
        )
        .select_from(b.outerjoin(accounts, accounts.c.telegram_id == b.c.telegram_id).outerjoin(paid, paid.c.telegram_id == b.c.telegram_id))
        .order_by(b.c.first_seen_at.desc())
        .limit(limit)
    )
    if q:
        needle = _digits(q)
        stmt = stmt.where(or_(cast(b.c.telegram_id, String).like(f"{needle}%"), b.c.username.ilike(f"%{q.strip().lstrip('@')}%")))
    return _rows(await session.execute(stmt))


async def customer(session: AsyncSession, telegram_id: int) -> dict[str, Any] | None:
    b, v, p, s, r = (link.t(n) for n in ("bot_users", "vpn_users", "payments", "services", "resellers"))
    user = (await session.execute(select(b).where(b.c.telegram_id == telegram_id))).first()
    reseller = (await session.execute(select(r).where(r.c.telegram_id == telegram_id))).first()
    accounts = _rows(
        await session.execute(
            select(v.c.id, v.c.ibsng_username, v.c.ibsng_group, v.c.is_trial, v.c.expires_at, v.c.created_at, s.c.title.label("service"), s.c.category)
            .select_from(v.outerjoin(s, s.c.id == v.c.service_id))
            .where(v.c.telegram_id == telegram_id)
            .order_by(v.c.created_at.desc())
        )
    )
    payments = _rows(
        await session.execute(
            select(
                p.c.id, p.c.method, p.c.status, p.c.purpose, p.c.amount, p.c.original_amount, p.c.auto_approved,
                p.c.ibsng_username, p.c.invoice_number, p.c.created_at, p.c.resolved_at, p.c.receipt_file_id,
                s.c.title.label("service"), s.c.category,
            )
            .select_from(p.outerjoin(s, s.c.id == p.c.service_id))
            .where(p.c.telegram_id == telegram_id)
            .order_by(p.c.created_at.desc())
        )
    )
    if user is None and not accounts and not payments:
        return None
    # Whether AloBot's blocked-user middleware would exempt this person: it
    # lets every admin and every reseller through before it looks at the flag.
    a = link.t("admin_users")
    is_admin = (await session.execute(select(a.c.id).where(a.c.telegram_id == telegram_id))).first() is not None
    return {"user": user, "reseller": reseller, "accounts": accounts, "payments": payments, "is_admin": is_admin}


async def orders(
    session: AsyncSession,
    *,
    status: str | None = None,
    method: str | None = None,
    purpose: str | None = None,
    category: str | None = None,
    q: str | None = None,
    page: int = 1,
) -> dict[str, Any]:
    p, s, b = link.t("payments"), link.t("services"), link.t("bot_users")
    base = select(
        p.c.id, p.c.telegram_id, p.c.method, p.c.status, p.c.purpose, p.c.amount, p.c.original_amount, p.c.auto_approved,
        p.c.ibsng_username, p.c.invoice_number, p.c.created_at, p.c.resolved_at, p.c.receipt_file_id,
        s.c.title.label("service"), s.c.category, b.c.username,
    ).select_from(p.outerjoin(s, s.c.id == p.c.service_id).outerjoin(b, b.c.telegram_id == p.c.telegram_id))
    conds = []
    if status:
        conds.append(p.c.status == status)
    if method:
        conds.append(p.c.method == method)
    if purpose:
        conds.append(p.c.purpose == purpose)
    if category:
        conds.append(s.c.category == category)
    if q:
        needle = q.strip()
        digits = _digits(needle)
        ors = [p.c.ibsng_username.ilike(f"%{needle}%"), b.c.username.ilike(f"%{needle.lstrip('@')}%")]
        if digits.isdigit():
            ors.append(cast(p.c.telegram_id, String).like(f"{digits}%"))
        if decode_id(needle) is not None:
            ors.append(p.c.invoice_number == needle)
        conds.append(or_(*ors))
    where = and_(*conds) if conds else None
    total = (await session.execute(select(func.count()).select_from(p.outerjoin(s, s.c.id == p.c.service_id).outerjoin(b, b.c.telegram_id == p.c.telegram_id)).where(where) if where is not None else select(func.count()).select_from(p))).scalar_one()
    stmt = base.order_by(p.c.created_at.desc()).limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)
    if where is not None:
        stmt = stmt.where(where)
    by_status = _rows(await session.execute(select(p.c.status, func.count()).group_by(p.c.status)))
    return {"rows": _rows(await session.execute(stmt)), "total": total, "page": page, "page_size": PAGE_SIZE, "by_status": dict(by_status)}


async def subscriptions(
    session: AsyncSession, *, category: str | None = None, trial: bool | None = None, state: str | None = None, q: str | None = None, page: int = 1
) -> dict[str, Any]:
    v, s, b = link.t("vpn_users"), link.t("services"), link.t("bot_users")
    now = _now()
    state_expr = case(
        (v.c.expires_at.is_(None), "unknown"), (v.c.expires_at > now, "active"), else_="expired"
    ).label("state")
    base = select(
        v.c.id, v.c.telegram_id, v.c.ibsng_username, v.c.ibsng_group, v.c.is_trial, v.c.expires_at, v.c.created_at,
        s.c.title.label("service"), s.c.category, b.c.username, state_expr,
    ).select_from(v.outerjoin(s, s.c.id == v.c.service_id).outerjoin(b, b.c.telegram_id == v.c.telegram_id))
    conds = []
    if category:
        conds.append(s.c.category == category)
    if trial is not None:
        conds.append(v.c.is_trial.is_(trial))
    if state == "active":
        conds.append(v.c.expires_at > now)
    elif state == "expired":
        conds.append(v.c.expires_at <= now)
    elif state == "unknown":
        conds.append(v.c.expires_at.is_(None))
    if q:
        needle = q.strip()
        digits = _digits(needle)
        ors = [v.c.ibsng_username.ilike(f"%{needle}%"), b.c.username.ilike(f"%{needle.lstrip('@')}%")]
        if digits.isdigit():
            ors.append(cast(v.c.telegram_id, String).like(f"{digits}%"))
        conds.append(or_(*ors))
    stmt = base.order_by(v.c.created_at.desc()).limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)
    count = select(func.count()).select_from(v.outerjoin(s, s.c.id == v.c.service_id).outerjoin(b, b.c.telegram_id == v.c.telegram_id))
    if conds:
        stmt = stmt.where(and_(*conds))
        count = count.where(and_(*conds))
    return {"rows": _rows(await session.execute(stmt)), "total": (await session.execute(count)).scalar_one(), "page": page, "page_size": PAGE_SIZE}


async def resellers(session: AsyncSession) -> list[Row]:
    r, b, v = link.t("resellers"), link.t("bot_users"), link.t("vpn_users")
    accounts = select(v.c.telegram_id, func.count().label("accounts")).group_by(v.c.telegram_id).subquery()
    return _rows(
        await session.execute(
            select(r.c.id, r.c.telegram_id, r.c.balance, r.c.commission_percent, r.c.created_at, b.c.username, func.coalesce(accounts.c.accounts, 0).label("accounts"))
            .select_from(r.outerjoin(b, b.c.telegram_id == r.c.telegram_id).outerjoin(accounts, accounts.c.telegram_id == r.c.telegram_id))
            .order_by(r.c.created_at)
        )
    )


async def catalog(session: AsyncSession) -> dict[str, Any]:
    s, loc, g = link.t("services"), link.t("service_locations"), link.t("groups")
    services = _rows(
        await session.execute(
            select(s, loc.c.title.label("location"), loc.c.flag_emoji)
            .select_from(s.outerjoin(loc, loc.c.id == s.c.location_id))
            .order_by(s.c.category, s.c.location_id, s.c.duration_months, s.c.user_count)
        )
    )
    locations = _rows(await session.execute(select(loc).order_by(loc.c.sort_order, loc.c.id)))
    groups = _rows(await session.execute(select(g).order_by(g.c.name)))
    return {"services": services, "locations": locations, "groups": groups}


async def discount_codes(session: AsyncSession) -> list[Row]:
    d, u = link.t("discount_codes"), link.t("discount_code_usages")
    usage = (
        select(u.c.discount_code_id, func.count().label("uses"), func.coalesce(func.sum(u.c.original_amount - u.c.amount), 0).label("given"))
        .group_by(u.c.discount_code_id)
        .subquery()
    )
    return _rows(
        await session.execute(
            select(d, func.coalesce(usage.c.uses, 0).label("uses"), func.coalesce(usage.c.given, 0).label("given"))
            .select_from(d.outerjoin(usage, usage.c.discount_code_id == d.c.id))
            .order_by(d.c.is_active.desc(), d.c.created_at.desc())
        )
    )


async def tutorials(session: AsyncSession) -> dict[str, Any]:
    pl, pr, gd, dl, ov, loc = (link.t(n) for n in ("tutorial_platforms", "tutorial_protocols", "tutorial_guides", "download_links", "openvpn_profiles", "service_locations"))
    platforms = _rows(await session.execute(select(pl).order_by(pl.c.sort_order, pl.c.id)))
    protocols = _rows(await session.execute(select(pr).order_by(pr.c.sort_order, pr.c.id)))
    guides = _rows(
        await session.execute(
            select(gd.c.id, gd.c.category, gd.c.is_active, gd.c.media_type, pl.c.label.label("platform"), pr.c.label.label("protocol"), loc.c.title.label("location"), func.length(func.coalesce(gd.c.body_html, "")).label("body_len"))
            .select_from(gd.join(pl, pl.c.id == gd.c.platform_id).join(pr, pr.c.id == gd.c.protocol_id).outerjoin(loc, loc.c.id == gd.c.location_id))
            .order_by(pl.c.sort_order, pr.c.sort_order, gd.c.category)
        )
    )
    links = _rows(
        await session.execute(
            select(dl.c.id, dl.c.url, pl.c.label.label("platform"), pr.c.label.label("protocol"))
            .select_from(dl.join(pl, pl.c.id == dl.c.platform_id).join(pr, pr.c.id == dl.c.protocol_id))
            .order_by(pl.c.sort_order, pr.c.sort_order)
        )
    )
    profiles = _rows(
        await session.execute(
            select(ov.c.id, ov.c.name, ov.c.category, ov.c.file_type, ov.c.is_active, pl.c.label.label("platform"), loc.c.title.label("location"))
            .select_from(ov.outerjoin(pl, pl.c.id == ov.c.platform_id).outerjoin(loc, loc.c.id == ov.c.location_id))
            .order_by(ov.c.id)
        )
    )
    return {"platforms": platforms, "protocols": protocols, "guides": guides, "links": links, "profiles": profiles}


async def bot_settings(session: AsyncSession) -> dict[str, str]:
    c = link.t("app_config")
    return {k: v for k, v in _rows(await session.execute(select(c.c.key, c.c.value).order_by(c.c.key)))}
