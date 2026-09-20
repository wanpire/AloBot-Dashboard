"""The notification bell: a count that keeps up, and a sound when it grows.

An operator does not sit refreshing the payments page. The bell is how they
learn that money is waiting on them, so a number that is only correct at page
load is not much of a bell.
"""

from __future__ import annotations

import datetime as dt

from app.db.session import async_session_maker
from app.services import settle
from tests.test_claims_sweeps import _account_with_card, _claim, _credit
from tests.web import client, logged_in

NOW = dt.datetime(2026, 9, 20, 12, 0, tzinfo=dt.timezone.utc)


async def _one_for_review() -> None:
    acct = await _account_with_card()
    await _claim(account_id=acct, alobot_id=1, at=NOW - dt.timedelta(minutes=20))
    await _claim(account_id=acct, alobot_id=2, at=NOW - dt.timedelta(minutes=19))
    await _credit(at=NOW - dt.timedelta(minutes=18))
    async with async_session_maker() as s:
        await settle.settle(s, now=NOW)


async def test_the_bell_endpoint_answers_the_current_count():
    await _one_for_review()
    c = await logged_in("REVIEWER")
    async with c:
        r = await c.get("/bell")
    assert r.status_code == 200 and r.json() == {"review": 2}


async def test_the_bell_endpoint_is_not_public():
    async with client() as c:
        r = await c.get("/bell")
    assert r.status_code in (302, 303, 401), r.status_code


async def test_every_role_that_sees_the_bell_may_read_it():
    """The bell is drawn in the shell for everyone, so refusing the count for
    some role would leave a badge that never updates."""
    await _one_for_review()
    for role in ("ADMIN", "REVIEWER", "READ_ONLY"):
        c = await logged_in(role, email=f"bell-{role.lower()}@x.io")
        async with c:
            r = await c.get("/bell")
        assert r.status_code == 200, f"{role} cannot read the bell count"
