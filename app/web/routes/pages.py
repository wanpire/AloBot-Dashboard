"""Section pages. Each section id in the sidebar has a route; sections built
in later phases replace the placeholder with their own router, and the
`page()` guard stays the one rule for who may open what."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import review
from app.web.deps import current_operator, get_db, page
from app.web.nav import NAV
from app.web.templating import render

router = APIRouter()


@router.get("/overview")
async def overview_alias(operator=Depends(current_operator)) -> RedirectResponse:
    """The overview lives at the root, because that is where login lands and
    what a bookmark of the panel should be. The sidebar builds every link from
    the section id, so this id needs somewhere to go: it sends people to the
    canonical address rather than answering a second copy of the page at a
    second URL."""
    return RedirectResponse("/", status_code=308)


@router.get("/bell")
async def bell(operator=Depends(current_operator), db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """What the bell shows, for the shell to poll. Every role sees the bell,
    so every role may read it; it carries one number and no payment details."""
    return JSONResponse({"review": await review.review_count(db)})


def _placeholder(page_id: str):
    async def view(request: Request, operator=Depends(page(page_id))):
        return render(request, "placeholder.html", page_id=page_id)

    view.__name__ = f"page_{page_id}"
    return view


for _group in NAV:
    for _item in _group.items:
        if _item.id != "overview":
            router.add_api_route(f"/{_item.id}", _placeholder(_item.id), methods=["GET"], name=f"page_{_item.id}")
