from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings_registry import REGISTRY
from app.services import settings as settings_service
from app.web.deps import get_db, page, require_role
from app.web.templating import render

router = APIRouter()


async def _page(request: Request, db: AsyncSession, status_code: int = 200, **ctx):
    values = await settings_service.get_all(db)
    return render(request, "settings.html", page_id="settings", status_code=status_code, specs=REGISTRY, values=values, **ctx)


@router.get("/settings")
async def settings_page(request: Request, operator=Depends(page("settings")), db: AsyncSession = Depends(get_db)):
    return await _page(request, db, notice=request.query_params.get("notice"))


@router.post("/settings")
async def settings_save(request: Request, operator=Depends(require_role("ADMIN")), db: AsyncSession = Depends(get_db)):
    form = await request.form()
    updates = {}
    for spec in REGISTRY:
        if spec.kind == "bool":
            updates[(spec.scope, spec.key)] = spec.form_name in form
        elif spec.form_name in form:
            updates[(spec.scope, spec.key)] = form[spec.form_name]
    try:
        await settings_service.set_many(
            db, updates, actor_email=operator.email, actor_role=operator.role, actor_operator_id=operator.id
        )
    except settings_service.SettingError as exc:
        return await _page(request, db, status_code=400, error=str(exc))
    return RedirectResponse("/settings?notice=saved", status_code=303)
