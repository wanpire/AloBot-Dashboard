"""The screens that read AloBot's data. Every one of them asks `link.available`
first and renders the reason when it is not, so a broken or missing link is a
sentence on the page, never a 500."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Request

from app.alobot import queries
from app.alobot.labels import ADMIN_LEVEL_LABELS, BOT_SETTING_LABELS, CATEGORY_LABELS, METHOD_LABELS, PURPOSE_LABELS, STATUS_LABELS, label
from app.alobot.link import link
from app.alobot.time import tehran_day_bounds, tehran_today
from app.core.config import get_settings
from app.web.deps import current_operator, page
from app.web.templating import render

router = APIRouter()

LABELS = {
    "category": CATEGORY_LABELS,
    "status": STATUS_LABELS,
    "method": METHOD_LABELS,
    "purpose": PURPOSE_LABELS,
    "admin_level": ADMIN_LEVEL_LABELS,
    "bot_setting": BOT_SETTING_LABELS,
}


def _ctx(**extra):
    return {"labels": LABELS, "label": label, "alobot_writes": get_settings().alobot_db_writes_enabled, **extra}


def _unavailable(request: Request, page_id: str):
    return render(request, "alobot_unavailable.html", page_id=page_id, problems=link.problems, **_ctx())


def _int(value: str | None, default: int) -> int:
    try:
        return max(1, int(value)) if value else default
    except ValueError:
        return default


@router.get("/")
async def overview(request: Request, operator=Depends(current_operator)):
    notice = request.query_params.get("notice")
    if not link.available:
        return render(request, "alobot_unavailable.html", page_id="overview", problems=link.problems, notice=notice, **_ctx())
    async with link.session() as s:
        data = await queries.overview(s)
    return render(request, "overview.html", page_id="overview", notice=notice, **_ctx(**data))


@router.get("/stats")
async def stats(request: Request, operator=Depends(page("stats"))):
    if not link.available:
        return _unavailable(request, "stats")
    days = min(_int(request.query_params.get("days"), 30), 3660)
    today = tehran_today()
    start, _ = tehran_day_bounds(today - dt.timedelta(days=days - 1))
    _, end = tehran_day_bounds(today)
    async with link.session() as s:
        data = await queries.stats(s, start, end)
    return render(request, "stats.html", page_id="stats", days=days, start=start, end=end, **_ctx(**data))


@router.get("/customers")
async def customers(request: Request, operator=Depends(page("customers"))):
    if not link.available:
        return _unavailable(request, "customers")
    q = request.query_params.get("q") or ""
    async with link.session() as s:
        rows = await queries.search_customers(s, q or None)
    return render(request, "customers.html", page_id="customers", q=q, rows=rows, **_ctx())


@router.get("/customers/{telegram_id}")
async def customer(request: Request, telegram_id: int, operator=Depends(page("customers"))):
    if not link.available:
        return _unavailable(request, "customers")
    async with link.session() as s:
        data = await queries.customer(s, telegram_id)
    if data is None:
        return render(request, "customer.html", page_id="customers", status_code=404, telegram_id=telegram_id, missing=True, **_ctx())
    # AloBot's middleware exempts admins and resellers from blocking, so the
    # page says why there is no control rather than offering a dead one.
    exempt = "ادمین ربات" if data.get("is_admin") else ("نمایندهٔ فروش" if data.get("reseller") else None)
    return render(
        request, "customer.html", page_id="customers", telegram_id=telegram_id, missing=False,
        exempt=exempt, error=request.query_params.get("error"), **_ctx(**data),
    )


@router.get("/orders")
async def orders(request: Request, operator=Depends(page("orders"))):
    if not link.available:
        return _unavailable(request, "orders")
    qp = request.query_params
    filters = {k: (qp.get(k) or None) for k in ("status", "method", "purpose", "category", "q")}
    async with link.session() as s:
        data = await queries.orders(s, page=_int(qp.get("page"), 1), **filters)
    return render(request, "orders.html", page_id="orders", filters=filters, **_ctx(**data))


@router.get("/subscriptions")
async def subscriptions(request: Request, operator=Depends(page("subscriptions"))):
    if not link.available:
        return _unavailable(request, "subscriptions")
    qp = request.query_params
    trial = {"1": True, "0": False}.get(qp.get("trial") or "")
    filters = {"category": qp.get("category") or None, "state": qp.get("state") or None, "q": qp.get("q") or None, "trial": trial}
    async with link.session() as s:
        data = await queries.subscriptions(s, page=_int(qp.get("page"), 1), **filters)
    return render(request, "subscriptions.html", page_id="subscriptions", filters=filters, **_ctx(**data))


@router.get("/resellers")
async def resellers(request: Request, operator=Depends(page("resellers"))):
    if not link.available:
        return _unavailable(request, "resellers")
    async with link.session() as s:
        rows = await queries.resellers(s)
    # AloBot keeps no history of a balance change, so what this project added
    # is only knowable from this project's own ledger.
    from sqlalchemy import select

    from app.db.session import async_session_maker
    from app.models import ResellerTopUp

    async with async_session_maker() as own:
        topups = (await own.execute(select(ResellerTopUp).order_by(ResellerTopUp.id.desc()).limit(20))).scalars().all()
    return render(
        request, "resellers.html", page_id="resellers", rows=rows, topups=topups,
        error=request.query_params.get("error"), **_ctx(),
    )


