"""The screens that edit AloBot's data, plus the three that edit ours
(broadcast, bot texts, keyboards).

Every AloBot edit is ADMIN-only and goes through `app.alobot.writes`, which
refuses unless the deployment has both a write-capable connection and the
flag. When it refuses, the page renders with the refusal as a sentence - the
read-only banner is the same one the reading screens carry.
"""

from __future__ import annotations

import datetime as dt
import uuid
from urllib.parse import quote
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.alobot import queries
from app.alobot.labels import CATEGORY_LABELS, label
from app.alobot.link import link
from app.alobot.writes import WritesDisabled, writes
from app.alobot.writes import admins as admin_writes
from app.alobot.writes import botsettings as botsettings_writes
from app.alobot.writes import botusers as botusers_writes
from app.alobot.writes import resellers as reseller_writes
from app.alobot.writes import catalog as catalog_writes
from app.alobot.writes import content as content_writes
from app.alobot.writes import cron as cron_writes
from app.alobot.writes import discounts as discount_writes
from app.services import bot_content, broadcast
from app.web.deps import get_db, page, require_role
from app.web.templating import render

router = APIRouter()
ADMIN = require_role("ADMIN")

EDIT_ERRORS = (
    WritesDisabled, catalog_writes.CatalogError, discount_writes.DiscountError, content_writes.ContentError,
    botsettings_writes.SettingsError, admin_writes.AdminError, cron_writes.CronError, bot_content.ContentError,
    botusers_writes.BotUserError, reseller_writes.ResellerError,
    broadcast.BroadcastError,
)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _decimal(raw: str, field: str) -> Decimal:
    try:
        return Decimal(str(raw).strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")))
    except (InvalidOperation, ValueError):
        raise catalog_writes.CatalogError(f"«{field}» باید یک عدد باشد.") from None


def _expiry(raw: str) -> dt.datetime | None:
    """A Jalali date an operator typed, read as the END of that day in
    Tehran: "valid until 1405/10/10" means the tenth still works."""
    if not str(raw or "").strip():
        return None
    from app.alobot.time import TEHRAN
    from app.web.format import parse_jalali_date

    day = parse_jalali_date(raw)
    return dt.datetime.combine(day, dt.time(23, 59, 59), tzinfo=TEHRAN).astimezone(dt.timezone.utc)


def _int_or_none(raw: str) -> int | None:
    raw = str(raw or "").strip()
    return int(raw) if raw.isdigit() else None


def _unavailable(request: Request, page_id: str):
    return render(request, "alobot_unavailable.html", page_id=page_id, problems=link.problems, labels={}, label=label, alobot_writes=False)


# ── Catalog ────────────────────────────────────────────────────────────────


async def _catalog_context(db: AsyncSession) -> dict[str, Any]:
    async with link.session() as read:
        data = await queries.catalog(read)
        slots: dict[tuple[str, int | None], dict[tuple[int, int], Any]] = {}
        by_key = {(s.category, s.location_id, s.duration_months, s.user_count): s for s in data["services"]}
        scopes = [(c, None) for c in catalog_writes.CATEGORIES if c != "fixed"] + [("fixed", loc.id) for loc in data["locations"]]
        for category, location_id in scopes:
            slots[(category, location_id)] = {
                (d, u): by_key.get((category, location_id, d, u))
                for d in catalog_writes.DURATIONS for u in catalog_writes.USER_COUNTS
            }
        enabled = {}
        for category in catalog_writes.CATEGORIES:
            row = (await read.execute(__import__("sqlalchemy").text("SELECT value FROM app_config WHERE key=:k"), {"k": f"category_enabled:{category}"})).first()
            enabled[category] = row is None or row.value != "false"
    return {
        **data, "slots": slots, "scopes": scopes, "enabled": enabled, "categories": catalog_writes.CATEGORIES,
        "category_labels": CATEGORY_LABELS, "durations": catalog_writes.DURATIONS, "user_counts": catalog_writes.USER_COUNTS,
        "sellable_groups": [g for g in data["groups"] if not g.is_trial],
    }


async def _catalog_page(request: Request, db: AsyncSession, status_code: int = 200, **ctx):
    if not link.available:
        return _unavailable(request, "catalog")
    return render(request, "catalog.html", page_id="catalog", status_code=status_code, labels={"category": CATEGORY_LABELS}, label=label, alobot_writes=writes.available, **await _catalog_context(db), **ctx)


@router.get("/catalog")
async def catalog_page(request: Request, operator=Depends(page("catalog")), db: AsyncSession = Depends(get_db)):
    return await _catalog_page(request, db, notice=request.query_params.get("notice"))


async def _do(request: Request, db: AsyncSession, page_fn, coro):
    try:
        await coro
    except EDIT_ERRORS as exc:
        return await page_fn(request, db, 400, error=str(exc))
    return None


@router.post("/catalog/slot")
async def catalog_bind(request: Request, operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    form = await request.form()
    try:
        location_id = _int_or_none(form.get("location_id"))
        if form.get("group_name") == "":
            await catalog_writes.unbind_slot(db, operator, category=str(form["category"]), duration_months=int(form["duration_months"]), user_count=int(form["user_count"]), location_id=location_id)
        else:
            await catalog_writes.bind_slot(
                db, operator, category=str(form["category"]), duration_months=int(form["duration_months"]), user_count=int(form["user_count"]),
                group_name=str(form["group_name"]), price=_decimal(form.get("price", ""), "قیمت"), location_id=location_id,
            )
    except EDIT_ERRORS as exc:
        return await _catalog_page(request, db, 400, error=str(exc))
    return RedirectResponse("/catalog?notice=saved", status_code=303)


@router.post("/catalog/category")
async def catalog_category(request: Request, category: str = Form(""), enabled: str = Form("1"), operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    failed = await _do(request, db, _catalog_page, catalog_writes.set_category_enabled(db, operator, category, enabled == "1"))
    return failed or RedirectResponse("/catalog?notice=saved", status_code=303)


@router.post("/catalog/service/{service_id}/active")
async def catalog_service_active(request: Request, service_id: int, active: str = Form("1"), operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    failed = await _do(request, db, _catalog_page, catalog_writes.set_service_active(db, operator, service_id, active == "1"))
    return failed or RedirectResponse("/catalog?notice=saved", status_code=303)


@router.post("/catalog/locations")
async def catalog_location_create(request: Request, title: str = Form(""), flag_emoji: str = Form(""), operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    failed = await _do(request, db, _catalog_page, catalog_writes.create_location(db, operator, title=title, flag_emoji=flag_emoji))
    return failed or RedirectResponse("/catalog?notice=saved", status_code=303)


@router.post("/catalog/locations/{location_id}/active")
async def catalog_location_active(request: Request, location_id: int, active: str = Form("1"), operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    failed = await _do(request, db, _catalog_page, catalog_writes.set_location_active(db, operator, location_id, active == "1"))
    return failed or RedirectResponse("/catalog?notice=saved", status_code=303)


@router.post("/catalog/locations/{location_id}/delete")
async def catalog_location_delete(request: Request, location_id: int, operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    failed = await _do(request, db, _catalog_page, catalog_writes.delete_location(db, operator, location_id))
    return failed or RedirectResponse("/catalog?notice=saved", status_code=303)


@router.post("/catalog/bulk-price")
async def catalog_bulk_price(request: Request, mode: str = Form("percent"), value: str = Form("0"), category: str = Form(""), location_id: str = Form(""), apply: str = Form(""), operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    try:
        outcome = await catalog_writes.bulk_price(db, operator, mode=mode, value=_decimal(value, "مقدار"), category=category or None, location_id=_int_or_none(location_id), apply=(apply == "1"))
    except EDIT_ERRORS as exc:
        return await _catalog_page(request, db, 400, error=str(exc))
    if outcome["applied"]:
        return RedirectResponse("/catalog?notice=priced", status_code=303)
    return await _catalog_page(request, db, bulk=outcome, bulk_form={"mode": mode, "value": value, "category": category, "location_id": location_id})


# ── Discounts ──────────────────────────────────────────────────────────────


async def _discounts_page(request: Request, db: AsyncSession, status_code: int = 200, **ctx):
    if not link.available:
        return _unavailable(request, "discounts")
    async with link.session() as read:
        rows = await queries.discount_codes(read)
    return render(request, "discounts.html", page_id="discounts", status_code=status_code, rows=rows, categories=catalog_writes.CATEGORIES, category_labels=CATEGORY_LABELS, labels={"category": CATEGORY_LABELS}, label=label, alobot_writes=writes.available, discount_bounds=discount_writes.bounds_available(), **ctx)


@router.get("/discounts")
async def discounts_page(request: Request, operator=Depends(page("discounts")), db: AsyncSession = Depends(get_db)):
    usages = None
    code_id = _int_or_none(request.query_params.get("usages"))
    if code_id:
        usages = {"code_id": code_id, "rows": await discount_writes.usages(db, code_id)}
    return await _discounts_page(request, db, notice=request.query_params.get("notice"), usages=usages)


@router.post("/discounts")
async def discounts_create(request: Request, operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    form = await request.form()
    categories = [c for c in catalog_writes.CATEGORIES if form.get(f"cat_{c}")]
    try:
        expires_at = _expiry(str(form.get("expires_at", "")))
    except ValueError as exc:
        return await _discounts_page(request, db, status_code=400, error=str(exc))
    failed = await _do(request, db, _discounts_page, discount_writes.create(
        db, operator, code=str(form.get("code", "")), percent=_decimal(form.get("percent", "0"), "درصد"),
        usage_limit=_int_or_none(form.get("usage_limit")), categories=categories or None, is_public=bool(form.get("is_public")),
        expires_at=expires_at, per_user_limit=_int_or_none(form.get("per_user_limit")),
    ))
    return failed or RedirectResponse("/discounts?notice=saved", status_code=303)


@router.post("/discounts/{code_id}/active")
async def discounts_active(request: Request, code_id: int, active: str = Form("1"), operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    failed = await _do(request, db, _discounts_page, discount_writes.set_active(db, operator, code_id, active == "1"))
    return failed or RedirectResponse("/discounts?notice=saved", status_code=303)


@router.post("/discounts/{code_id}/delete")
async def discounts_delete(request: Request, code_id: int, operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    failed = await _do(request, db, _discounts_page, discount_writes.delete(db, operator, code_id))
    return failed or RedirectResponse("/discounts?notice=saved", status_code=303)


# ── Tutorials, links, profiles ─────────────────────────────────────────────


async def _tutorials_page(request: Request, db: AsyncSession, status_code: int = 200, **ctx):
    if not link.available:
        return _unavailable(request, "tutorials")
    async with link.session() as read:
        data = await queries.tutorials(read)
        locations = (await queries.catalog(read))["locations"]
    return render(request, "tutorials.html", page_id="tutorials", status_code=status_code, locations=locations, categories=catalog_writes.CATEGORIES, category_labels=CATEGORY_LABELS, labels={"category": CATEGORY_LABELS}, label=label, alobot_writes=writes.available, **data, **ctx)


@router.get("/tutorials")
async def tutorials_page(request: Request, operator=Depends(page("tutorials")), db: AsyncSession = Depends(get_db)):
    return await _tutorials_page(request, db, notice=request.query_params.get("notice"))


@router.post("/tutorials/platforms")
async def tutorials_platform(request: Request, label_: str = Form("", alias="label"), sort_order: str = Form("0"), operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    failed = await _do(request, db, _tutorials_page, content_writes.create_platform(db, operator, label=label_, sort_order=_int_or_none(sort_order) or 0))
    return failed or RedirectResponse("/tutorials?notice=saved", status_code=303)


@router.post("/tutorials/platforms/{platform_id}/delete")
async def tutorials_platform_delete(request: Request, platform_id: int, operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    failed = await _do(request, db, _tutorials_page, content_writes.delete_platform(db, operator, platform_id))
    return failed or RedirectResponse("/tutorials?notice=saved", status_code=303)


@router.post("/tutorials/protocols")
async def tutorials_protocol(request: Request, label_: str = Form("", alias="label"), sort_order: str = Form("0"), operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    failed = await _do(request, db, _tutorials_page, content_writes.create_protocol(db, operator, label=label_, sort_order=_int_or_none(sort_order) or 0))
    return failed or RedirectResponse("/tutorials?notice=saved", status_code=303)


@router.post("/tutorials/protocols/{protocol_id}/delete")
async def tutorials_protocol_delete(request: Request, protocol_id: int, operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    failed = await _do(request, db, _tutorials_page, content_writes.delete_protocol(db, operator, protocol_id))
    return failed or RedirectResponse("/tutorials?notice=saved", status_code=303)


@router.post("/tutorials/guides")
async def tutorials_guide(request: Request, operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    form = await request.form()
    failed = await _do(request, db, _tutorials_page, content_writes.save_guide(
        db, operator, platform_id=int(form["platform_id"]), protocol_id=int(form["protocol_id"]),
        category=str(form["category"]), location_id=_int_or_none(form.get("location_id")), body_html=str(form.get("body_html", "")),
    ))
    return failed or RedirectResponse("/tutorials?notice=saved", status_code=303)


@router.post("/tutorials/guides/{guide_id}/delete")
async def tutorials_guide_delete(request: Request, guide_id: int, operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    failed = await _do(request, db, _tutorials_page, content_writes.delete_guide(db, operator, guide_id))
    return failed or RedirectResponse("/tutorials?notice=saved", status_code=303)


@router.post("/tutorials/links")
async def tutorials_link(request: Request, platform_id: int = Form(...), protocol_id: int = Form(...), url: str = Form(""), operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    failed = await _do(request, db, _tutorials_page, content_writes.set_download_link(db, operator, platform_id=platform_id, protocol_id=protocol_id, url=url))
    return failed or RedirectResponse("/tutorials?notice=saved", status_code=303)


@router.post("/tutorials/profiles")
async def tutorials_profile(request: Request, operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    form = await request.form()
    failed = await _do(request, db, _tutorials_page, content_writes.create_profile(
        db, operator, name=str(form.get("name", "")), category=(str(form.get("category")) or None) if form.get("category") else None,
        location_id=_int_or_none(form.get("location_id")), platform_id=_int_or_none(form.get("platform_id")), text_body=str(form.get("text_body", "")),
    ))
    return failed or RedirectResponse("/tutorials?notice=saved", status_code=303)


@router.post("/tutorials/profiles/{profile_id}/delete")
async def tutorials_profile_delete(request: Request, profile_id: int, operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    failed = await _do(request, db, _tutorials_page, content_writes.delete_profile(db, operator, profile_id))
    return failed or RedirectResponse("/tutorials?notice=saved", status_code=303)


# ── AloBot's settings ──────────────────────────────────────────────────────


async def _botsettings_page(request: Request, db: AsyncSession, status_code: int = 200, **ctx):
    if not link.available:
        return _unavailable(request, "botsettings")
    return render(
        request, "botsettings.html", page_id="botsettings", status_code=status_code, specs=botsettings_writes.REGISTRY,
        values=await botsettings_writes.current(db), read_only=await botsettings_writes.read_only_keys(db),
        labels={}, label=label, alobot_writes=writes.available, **ctx,
    )


@router.get("/botsettings")
async def botsettings_page(request: Request, operator=Depends(page("botsettings")), db: AsyncSession = Depends(get_db)):
    return await _botsettings_page(request, db, notice=request.query_params.get("notice"))


@router.post("/botsettings")
async def botsettings_save(request: Request, operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    form = await request.form()
    values: dict[str, Any] = {}
    for spec in botsettings_writes.REGISTRY:
        if spec.kind == "bool":
            values[spec.key] = spec.key in form
        elif spec.key in form:
            values[spec.key] = form[spec.key]
    failed = await _do(request, db, _botsettings_page, botsettings_writes.save(db, operator, values))
    return failed or RedirectResponse("/botsettings?notice=saved", status_code=303)


# ── Cron ───────────────────────────────────────────────────────────────────


async def _cron_page(request: Request, db: AsyncSession, status_code: int = 200, **ctx):
    alobot_rows = await cron_writes.status(db) if link.available else []
    return render(request, "cron.html", page_id="cron", status_code=status_code, alobot_jobs=alobot_rows, our_sweeps=await cron_writes.our_sweeps(db), alobot_available=link.available, problems=link.problems, labels={}, label=label, alobot_writes=writes.available, **ctx)


@router.get("/cron")
async def cron_page(request: Request, operator=Depends(page("cron")), db: AsyncSession = Depends(get_db)):
    return await _cron_page(request, db, notice=request.query_params.get("notice"))


@router.post("/cron/{key}")
async def cron_set(request: Request, key: str, hour: str = Form("0"), minute: str = Form("0"), operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    try:
        await cron_writes.set_time(db, operator, key, hour=int(hour or 0), minute=int(minute or 0))
    except (ValueError, *EDIT_ERRORS) as exc:
        return await _cron_page(request, db, 400, error=str(exc) if isinstance(exc, EDIT_ERRORS) else "ساعت و دقیقه باید عدد باشند.")
    return RedirectResponse("/cron?notice=saved", status_code=303)


# ── Bot texts and keyboards (ours until Phase 7) ───────────────────────────


async def _texts_page(request: Request, db: AsyncSession, status_code: int = 200, **ctx):
    return render(request, "texts.html", page_id="texts", status_code=status_code, specs=bot_content.TEXTS, values=await bot_content.texts(db), labels={}, label=label, alobot_writes=writes.available, **ctx)


@router.get("/texts")
async def texts_page(request: Request, operator=Depends(page("texts")), db: AsyncSession = Depends(get_db)):
    return await _texts_page(request, db, notice=request.query_params.get("notice"))


@router.post("/texts/{key}")
async def texts_save(request: Request, key: str, value: str = Form(""), reset: str = Form(""), operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    coro = bot_content.reset_text(db, operator, key) if reset == "1" else bot_content.save_text(db, operator, key, value)
    failed = await _do(request, db, _texts_page, coro)
    return failed or RedirectResponse("/texts?notice=saved", status_code=303)


async def _keyboard_page(request: Request, db: AsyncSession, status_code: int = 200, **ctx):
    menus = {}
    for menu_id, menu in bot_content.MENUS.items():
        menus[menu_id] = {"menu": menu, "buttons": await bot_content.layout(db, menu_id), "customised": await bot_content.is_customised(db, menu_id)}
    return render(request, "keyboard.html", page_id="keyboard", status_code=status_code, menus=menus, styles=bot_content.STYLES, labels={}, label=label, alobot_writes=writes.available, **ctx)


@router.get("/keyboard")
async def keyboard_page(request: Request, operator=Depends(page("keyboard")), db: AsyncSession = Depends(get_db)):
    return await _keyboard_page(request, db, notice=request.query_params.get("notice"))


@router.post("/keyboard/{menu_id}")
async def keyboard_save(request: Request, menu_id: str, operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    form = await request.form()
    if form.get("reset") == "1":
        failed = await _do(request, db, _keyboard_page, bot_content.reset_layout(db, operator, menu_id))
        return failed or RedirectResponse("/keyboard?notice=saved", status_code=303)
    buttons = []
    for button in bot_content.MENUS.get(menu_id, bot_content.MENUS["main"]).buttons:
        if not form.get(f"show_{button.action}"):
            continue
        buttons.append({"action": button.action, "label": form.get(f"label_{button.action}", button.label), "style": form.get(f"style_{button.action}") or None, "row": form.get(f"row_{button.action}", 1)})
    failed = await _do(request, db, _keyboard_page, bot_content.save_layout(db, operator, menu_id, buttons))
    return failed or RedirectResponse("/keyboard?notice=saved", status_code=303)


# ── Broadcast ──────────────────────────────────────────────────────────────


async def _bulk_page(request: Request, db: AsyncSession, status_code: int = 200, **ctx):
    reaches = {}
    if link.available:
        for audience in broadcast.AUDIENCES:
            reaches[audience["id"]] = await broadcast.reach(db, audience["id"])
    detail = None
    detail_id = _int_or_none(request.query_params.get("id"))
    if detail_id:
        detail = {"progress": await broadcast.progress(db, detail_id), "failures": await broadcast.failures(db, detail_id)}
    return render(
        request, "bulk.html", page_id="bulk", status_code=status_code, audiences=broadcast.AUDIENCES, reaches=reaches,
        recent=await broadcast.recent(db), batch_id=uuid.uuid4().hex, detail=detail, alobot_available=link.available,
        labels={}, label=label, alobot_writes=writes.available, **ctx,
    )


@router.get("/bulk")
async def bulk_page(request: Request, operator=Depends(page("bulk")), db: AsyncSession = Depends(get_db)):
    return await _bulk_page(request, db, notice=request.query_params.get("notice"))


@router.post("/bulk")
async def bulk_send(request: Request, batch_id: str = Form(""), audience: str = Form("all"), body: str = Form(""), button_text: str = Form(""), button_url: str = Form(""), confirm: str = Form(""), operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    if confirm != "1":
        return await _bulk_page(request, db, 400, error="ارسال گروهی باید تایید شود: این پیام بی‌درنگ به همهٔ مخاطبان انتخاب‌شده می‌رود و برگشت‌پذیر نیست.")
    try:
        broadcast_id = await broadcast.create(db, operator, batch_id=batch_id or uuid.uuid4().hex, audience=audience, body=body, button_text=button_text or None, button_url=button_url or None, now=_now())
    except EDIT_ERRORS as exc:
        return await _bulk_page(request, db, 400, error=str(exc))
    return RedirectResponse(f"/bulk?id={broadcast_id}&notice=queued", status_code=303)


# ── Blocking a customer out of the bot ─────────────────────────────────────


async def _customer_action(request: Request, db: AsyncSession, telegram_id: int, action, operator):
    """Both controls land back on the customer's own page, with the refusal
    as a sentence when AloBot's rules say no."""
    try:
        await action(db, operator, telegram_id=telegram_id)
    except EDIT_ERRORS as exc:
        return RedirectResponse(f"/customers/{telegram_id}?error={quote(str(exc))}", status_code=303)
    return RedirectResponse(f"/customers/{telegram_id}", status_code=303)


@router.post("/customers/{telegram_id}/block")
async def customer_block(request: Request, telegram_id: int, operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    return await _customer_action(request, db, telegram_id, botusers_writes.block, operator)


@router.post("/customers/{telegram_id}/unblock")
async def customer_unblock(request: Request, telegram_id: int, operator=Depends(ADMIN), db: AsyncSession = Depends(get_db)):
    return await _customer_action(request, db, telegram_id, botusers_writes.unblock, operator)


# ── Reseller balance ───────────────────────────────────────────────────────


@router.post("/resellers/{telegram_id}/topup")
async def reseller_topup(
    request: Request, telegram_id: int, amount: str = Form(""), note: str = Form(""),
    operator=Depends(ADMIN), db: AsyncSession = Depends(get_db),
):
    try:
        toman = _decimal(amount, "مبلغ شارژ")
        await reseller_writes.top_up(db, operator, telegram_id=telegram_id, amount_toman=toman, note=note.strip()[:200] or None)
    except EDIT_ERRORS as exc:
        return RedirectResponse(f"/resellers?error={quote(str(exc))}", status_code=303)
    return RedirectResponse("/resellers", status_code=303)
