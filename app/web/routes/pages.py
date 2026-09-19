"""Section pages. Each section id in the sidebar has a route; sections built
in later phases replace the placeholder with their own router, and the
`page()` guard stays the one rule for who may open what."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.web.deps import current_operator, page
from app.web.nav import NAV
from app.web.templating import render

router = APIRouter()


@router.get("/")
async def overview(request: Request, operator=Depends(current_operator)):
    return render(request, "overview.html", page_id="overview", notice=request.query_params.get("notice"))


def _placeholder(page_id: str):
    async def view(request: Request, operator=Depends(page(page_id))):
        return render(request, "placeholder.html", page_id=page_id)

    view.__name__ = f"page_{page_id}"
    return view


for _group in NAV:
    for _item in _group.items:
        if _item.id != "overview":
            router.add_api_route(f"/{_item.id}", _placeholder(_item.id), methods=["GET"], name=f"page_{_item.id}")
