"""Who is asking, and what they may do. Raised exceptions are turned into a
redirect (pages) or a status (HTMX/API) by the handlers in app.main."""

from __future__ import annotations

from typing import AsyncIterator

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session_maker
from app.services import auth
from app.web.nav import visible

SESSION_COOKIE = "dashboard_session"


class Unauthenticated(Exception):
    """No valid session. Pages redirect to /login; HTMX and API get 401."""


async def get_db() -> AsyncIterator[AsyncSession]:
    async with async_session_maker() as session:
        yield session


async def current_operator(request: Request, db: AsyncSession = Depends(get_db)):
    token = request.cookies.get(SESSION_COOKIE)
    operator = await auth.resolve_session(db, token) if token else None
    if operator is None:
        raise Unauthenticated()
    request.state.operator = operator
    request.state.session_token = token
    return operator


def require_role(*roles: str):
    """Guard for a WRITE route: the operator's role must be one of `roles`."""

    async def dep(operator=Depends(current_operator)):
        if operator.role not in roles:
            raise HTTPException(status_code=403, detail="این کار برای نقش شما مجاز نیست")
        return operator

    return dep


def page(page_id: str):
    """Guard for a section page: the same rule that draws the sidebar."""

    async def dep(operator=Depends(current_operator)):
        if not visible(operator.role, page_id):
            raise HTTPException(status_code=403, detail="این بخش برای نقش شما در دسترس نیست")
        return operator

    return dep
