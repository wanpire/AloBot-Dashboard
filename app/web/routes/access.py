from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.operator import OPERATOR_ROLES
from app.services import operators as ops
from app.web.deps import get_db, page, require_role
from app.web.templating import render

router = APIRouter()


async def _page(request: Request, db: AsyncSession, status_code: int = 200, **ctx):
    return render(
        request,
        "access.html",
        page_id="access",
        status_code=status_code,
        operators=await ops.list_operators(db),
        roles=OPERATOR_ROLES,
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
