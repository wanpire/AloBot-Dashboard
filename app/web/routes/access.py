from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.alobot.link import link
from app.alobot.writes import WritesDisabled, writes
from app.alobot.writes import admins as admin_writes
from app.models.operator import OPERATOR_ROLES
from app.services import operators as ops
from app.web.deps import get_db, page, require_role
from app.web.templating import render

router = APIRouter()


async def _page(request: Request, db: AsyncSession, status_code: int = 200, **ctx):
    bot_admins = await admin_writes.listing(db) if link.available else []
    return render(
        request,
        "access.html",
        page_id="access",
        status_code=status_code,
        operators=await ops.list_operators(db),
        roles=OPERATOR_ROLES,
        bot_admins=bot_admins,
        bot_levels=admin_writes.LEVELS,
        bot_level_labels=admin_writes.LEVEL_LABELS,
        reviewers=await admin_writes.reviewers(db) if link.available else [],
        alobot_available=link.available,
        alobot_writes=writes.available,
        **ctx,
    )


@router.get("/access")
async def access_page(request: Request, operator=Depends(page("access")), db: AsyncSession = Depends(get_db)):
    return await _page(request, db, notice=request.query_params.get("notice"))


@router.post("/access")
async def access_create(
    request: Request,
    email: str = Form(""),
    display_name: str = Form(""),
    password: str = Form(""),
    role: str = Form(""),
    operator=Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    try:
        await ops.create(db, operator, email=email, display_name=display_name, password=password, role=role)
    except ops.OperatorError as exc:
        return await _page(request, db, status_code=400, error=str(exc))
    return RedirectResponse("/access?notice=created", status_code=303)


@router.post("/access/{operator_id}/role")
async def access_role(
    request: Request,
    operator_id: int,
    role: str = Form(""),
    operator=Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    try:
        await ops.set_role(db, operator, operator_id, role)
    except ops.OperatorError as exc:
        return await _page(request, db, status_code=400, error=str(exc))
    return RedirectResponse("/access?notice=saved", status_code=303)


@router.post("/access/{operator_id}/active")
async def access_active(
    request: Request,
    operator_id: int,
    active: str = Form("1"),
    operator=Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    try:
        await ops.set_active(db, operator, operator_id, active == "1")
    except ops.OperatorError as exc:
        return await _page(request, db, status_code=400, error=str(exc))
    return RedirectResponse("/access?notice=saved", status_code=303)


# ── AloBot's own bot admins, and who may decide on money in the bot ────────


async def _bot_admin_action(request: Request, db: AsyncSession, operator, coro):
    try:
        await coro
    except (admin_writes.AdminError, WritesDisabled) as exc:
        return await _page(request, db, 400, error=str(exc))
    return RedirectResponse("/access?notice=saved", status_code=303)


@router.post("/access/bot-admins")
async def bot_admin_add(
    request: Request,
    telegram_id: str = Form(""),
    level: str = Form(""),
    operator=Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    if not telegram_id.strip().isdigit():
        return await _page(request, db, 400, error="شناسهٔ تلگرام باید عدد باشد.")
    return await _bot_admin_action(request, db, operator, admin_writes.add(db, operator, telegram_id=int(telegram_id), level=level))


@router.post("/access/bot-admins/{telegram_id}/level")
async def bot_admin_level(
    request: Request,
    telegram_id: int,
    level: str = Form(""),
    operator=Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    return await _bot_admin_action(request, db, operator, admin_writes.set_level(db, operator, telegram_id=telegram_id, level=level))


@router.post("/access/bot-admins/{telegram_id}/remove")
async def bot_admin_remove(
    request: Request,
    telegram_id: int,
    operator=Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    return await _bot_admin_action(request, db, operator, admin_writes.remove(db, operator, telegram_id=telegram_id))


@router.post("/access/reviewers/reset")
async def bot_admin_reviewers_reset(
    request: Request,
    operator=Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    return await _bot_admin_action(request, db, operator, admin_writes.reset_reviewers(db, operator))


@router.post("/access/reviewers/{telegram_id}")
async def bot_admin_reviewer(
    request: Request,
    telegram_id: int,
    operator=Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    return await _bot_admin_action(request, db, operator, admin_writes.toggle_reviewer(db, operator, telegram_id))
