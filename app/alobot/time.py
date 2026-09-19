"""Tehran days. Every period on a report is a whole local day, never a UTC
day - the legacy shop under-reported every evening for years because of
that mistake, and this project pins the boundary against a known point."""

from __future__ import annotations

import datetime as dt

from app.web.format import TEHRAN


def tehran_day_bounds(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    start = dt.datetime(day.year, day.month, day.day, tzinfo=TEHRAN).astimezone(dt.timezone.utc)
    return start, start + dt.timedelta(days=1)


def tehran_today(now: dt.datetime | None = None) -> dt.date:
    now = now or dt.datetime.now(dt.timezone.utc)
    return now.astimezone(TEHRAN).date()
