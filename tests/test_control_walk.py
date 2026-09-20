"""Phase 6 task 1: walk every section as every role, and press every control.

Two different questions, and the second is the one route tests cannot answer:

1. Does the server refuse? `tests/test_write_guards.py` already walks the
   router and asserts that.
2. **Is a control even drawn for a role that may not use it?** A page that
   offers a button and then answers 403 reads as broken rather than as a
   boundary, and it is how an operator learns to distrust the panel. This
   walks the rendered HTML of every section a role can open, collects every
   form that posts, and holds it against what that role is allowed to do.
"""

from __future__ import annotations

from html.parser import HTMLParser

import pytest

from app.alobot.link import link
from app.alobot.seed import seed_alobot_copy
from app.db.session import async_session_maker
from app.services import settle
from app.web.nav import NAV, visible
from tests.conftest import ALOBOT_ADMIN_URL
from tests.test_claims_sweeps import _account_with_card, _claim, _credit
from tests.web import logged_in

ROLES = ("ADMIN", "REVIEWER", "READ_ONLY")

# What each role may POST to, as path prefixes. Everything else must be
# neither drawn nor accepted.
ALLOWED_WRITES = {
    "ADMIN": ("/",),  # everything
    "REVIEWER": ("/logout", "/password", "/payments/"),
    "READ_ONLY": ("/logout", "/password"),
}
# A reviewer decides on money but does not switch the shop's modes.
REVIEWER_FORBIDDEN = ("/payments/continuity",)


class FormFinder(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.actions: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "form":
            return
        data = dict(attrs)
        if (data.get("method") or "get").lower() == "post":
            self.actions.append(data.get("action") or "")


def forms_in(html: str) -> list[str]:
    finder = FormFinder()
    finder.feed(html)
    return finder.actions


def may_post(role: str, action: str) -> bool:
    if role == "REVIEWER" and any(action.startswith(p) for p in REVIEWER_FORBIDDEN):
        return False
    return any(action.startswith(prefix) for prefix in ALLOWED_WRITES[role])


@pytest.fixture
async def seeded():
    await seed_alobot_copy(ALOBOT_ADMIN_URL, customers=8, seed=5)
    await link.connect()
    acct = await _account_with_card()
    await _claim(account_id=acct, alobot_id=9001)
    await _credit()
    async with async_session_maker() as db:
        await settle.settle(db)


SECTIONS = [item.id for group in NAV for item in group.items]


@pytest.mark.parametrize("role", ROLES)
async def test_every_section_either_opens_or_refuses_by_the_same_rule_the_sidebar_uses(seeded, role):
    c = await logged_in(role)
    async with c:
        for page_id in SECTIONS:
            path = "/" if page_id == "overview" else f"/{page_id}"
            r = await c.get(path)
            expected = 200 if visible(role, page_id) else 403
            assert r.status_code == expected, f"{role} GET {path} -> {r.status_code}, expected {expected}"


@pytest.mark.parametrize("role", ROLES)
async def test_no_section_draws_a_write_control_the_role_may_not_use(seeded, role):
    c = await logged_in(role)
    offending: list[str] = []
    async with c:
        for page_id in SECTIONS:
            if not visible(role, page_id):
                continue
            path = "/" if page_id == "overview" else f"/{page_id}"
            r = await c.get(path)
            assert r.status_code == 200, path
            for action in forms_in(r.text):
                if not may_post(role, action):
                    offending.append(f"{page_id}: {action}")
    assert not offending, f"{role} is offered controls it cannot use:\n" + "\n".join(offending)


@pytest.mark.parametrize("role", ("REVIEWER", "READ_ONLY"))
async def test_pressing_a_control_the_role_should_not_have_is_refused(seeded, role):
    """Whatever the page draws, the server is the guard. Take every control
    ADMIN is offered and press it as this role."""
    admin = await logged_in("ADMIN")
    actions: set[str] = set()
    async with admin:
        for page_id in SECTIONS:
            r = await admin.get("/" if page_id == "overview" else f"/{page_id}")
            if r.status_code == 200:
                actions.update(a for a in forms_in(r.text) if a)
    assert len(actions) > 15, "the walk found almost no controls - it is not proving anything"

    c = await logged_in(role)
    wrong: list[str] = []
    async with c:
        for action in sorted(actions):
            if may_post(role, action):
                continue
            r = await c.request("POST", action, headers={"Origin": "http://test"})
            if r.status_code != 403:
                wrong.append(f"{action} -> {r.status_code}")
    assert not wrong, f"{role} was not refused:\n" + "\n".join(wrong)


async def test_a_reviewer_can_still_do_the_job_the_role_exists_for(seeded):
    """The mirror image: a rule that refused everything would pass the tests
    above and leave the panel useless."""
    c = await logged_in("REVIEWER")
    async with c:
        queue = await c.get("/payments?tab=auto")
        assert queue.status_code == 200
        assert "/payments/" in queue.text, "a reviewer must be offered the review controls"
        assert (await c.get("/transactions")).status_code == 200
        blocked = await c.post("/payments/continuity", data={"minutes": "60", "reason": "x"}, headers={"Origin": "http://test"})
    assert blocked.status_code == 403
