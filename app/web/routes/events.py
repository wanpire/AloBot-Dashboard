from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import events
from app.web.deps import get_db, page
from app.web.templating import render

router = APIRouter()
LEVELS = ("ERROR", "WARNING", "INFO")


@router.get("/events")
async def events_page(request: Request, operator=Depends(page("events")), db: AsyncSession = Depends(get_db)):
    level = request.query_params.get("level") or None
    q = request.query_params.get("q") or None
    hours_raw = request.query_params.get("hours") or "24"
    try:
        hours = max(1, min(int(hours_raw), 24 * 30))
    except ValueError:
        hours = 24
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    rows = await events.list_events(db, level=level if level in LEVELS else None, q=q, since=since)
    items = [
        (
            row,
            json.dumps(
                {
                    "at": row.at.isoformat(),
                    "level": row.level,
                    "event": row.event,
                    "service": row.service,
                    "request_id": row.request_id,
                    "fields": row.fields,
                    "err": row.err,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        for row in rows
    ]
    return render(
        request, "events.html", page_id="events", items=items, levels=LEVELS, level=level, q=q or "", hours=hours
    )
