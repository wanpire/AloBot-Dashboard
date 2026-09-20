"""Phase 6 task 1: the panel driven by a real browser.

The HTTP walks answer "what does the server send". They cannot answer the
questions an operator actually runs into: does the page load its own assets,
does the stylesheet arrive, does anything throw in the console, does the
browser really keep the session cookie across a form post, and does the
Persian/RTL shell render as text a person can read rather than as markup that
happens to contain the right words.

Uvicorn runs inside this test's own event loop, so the browser and the test
talk to one database and a claim seeded here is the claim on the screen.
"""

from __future__ import annotations

import pytest
from playwright.async_api import async_playwright

from app.alobot.link import link
from app.alobot.seed import seed_alobot_copy
from app.db.session import async_session_maker
from app.services import review, settle
from app.web.nav import NAV, visible
from tests.conftest import ALOBOT_ADMIN_URL
from tests.test_claims_sweeps import _account_with_card, _claim, _credit
from tests.web import PASSWORD, make_operator

SECTIONS = [item.id for group in NAV for item in group.items]

# Every selector this suite uses, in one place, and every one of them a
# data-testid. The point is that restyling the panel - different classes,
# different nesting, a different element - cannot break these tests, and that
# a test failure therefore means the behaviour changed rather than the markup.
TID = {
    name: f'[data-testid="{name}"]'
    for name in (
        "sidebar", "page-content", "bell", "bell-count", "flash-notice", "flash-error",
        "login-email", "login-password", "login-submit", "login-error",
        "payment-approve-btn",
        "device-name-input", "device-code-input", "device-create-btn",
        "device-actions-summary", "device-rotate-form", "device-rotate-confirm", "device-rotate-btn",
        "token-reveal",
    )
}




@pytest.fixture(scope="session")
async def browser():
    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        yield b
        await b.close()


class Page:
    """A browser page plus everything that went wrong while it was open."""

    def __init__(self, page) -> None:
        self.page = page
        self.console: list[str] = []
        self.failures: list[str] = []
        page.on("console", lambda m: self.console.append(f"{m.type}: {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: self.console.append(f"pageerror: {e}"))
        page.on("requestfailed", lambda r: self.failures.append(f"{r.url} failed: {r.failure}"))
        page.on("response", lambda r: self.failures.append(f"{r.url} -> {r.status}") if r.status >= 500 else None)

    def problems(self) -> list[str]:
        return self.console + self.failures


@pytest.fixture
async def op(browser, base_url):
    """A fresh browser context, a fresh operator, logged in through the form."""

    async def _open(role: str = "ADMIN") -> Page:
        email = f"{role.lower()}@x.io"
        await make_operator(email=email, role=role)
        context = await browser.new_context(locale="fa-IR")
        page = Page(await context.new_page())
        await page.page.goto(f"{base_url}/login")
        await page.page.fill(TID["login-email"], email)
        await page.page.fill(TID["login-password"], PASSWORD)
        await page.page.click(TID["login-submit"])
        await page.page.wait_for_load_state("networkidle")
        return page

    return _open


@pytest.fixture
async def seeded():
    await seed_alobot_copy(ALOBOT_ADMIN_URL, customers=8, seed=5)
    await link.connect()
    acct = await _account_with_card()
    # Two claims of the same amount against one credit: the matcher refuses to
    # guess, so both land in the review queue with the credit suggested. That
    # is the screen a reviewer is hired for.
    first = await _claim(account_id=acct, alobot_id=9001)
    await _claim(account_id=acct, alobot_id=9002)
    await _credit()
    async with async_session_maker() as db:
        await settle.settle(db)
    return first


async def test_logging_in_through_the_form_opens_the_panel(op, base_url):
    page = await op("ADMIN")
    assert page.page.url.rstrip("/") == base_url, f"login did not land on the overview: {page.page.url}"
    assert await page.page.locator(TID["sidebar"]).is_visible()
    assert not page.problems(), page.problems()


async def test_a_wrong_password_says_one_generic_thing_and_opens_nothing(browser, base_url):
    await make_operator(email="admin@x.io", role="ADMIN")
    context = await browser.new_context()
    page = await context.new_page()
    await page.goto(f"{base_url}/login")
    await page.fill(TID["login-email"], "admin@x.io")
    await page.fill(TID["login-password"], "wrong")
    await page.click(TID["login-submit"])
    await page.wait_for_load_state("networkidle")
    assert await page.locator(TID["login-error"]).is_visible()
    assert not [c for c in await context.cookies() if c["name"] == "alod_session"]


async def test_every_section_renders_in_a_browser_without_a_console_error_or_a_broken_asset(op, seeded):
    page = await op("ADMIN")
    for page_id in SECTIONS:
        if not visible("ADMIN", page_id):
            continue
        path = "/" if page_id == "overview" else f"/{page_id}"
        response = await page.page.goto(page.page.url.split("/", 3)[0] + "//" + page.page.url.split("/")[2] + path)
        assert response is not None and response.status == 200, f"{path} -> {response and response.status}"
        await page.page.wait_for_load_state("networkidle")
        assert await page.page.locator(TID["page-content"]).is_visible(), f"{path} rendered nothing"
    assert not page.problems(), "the panel is not clean in a browser:\n" + "\n".join(page.problems())


async def test_the_shell_is_right_to_left_and_the_numbers_are_persian(op, seeded, base_url):
    page = await op("ADMIN")
    await page.page.goto(f"{base_url}/payments?tab=review")
    direction = await page.page.evaluate("getComputedStyle(document.documentElement).direction")
    assert direction == "rtl"
    # The bell carries the review count, and an operator reads it in Persian.
    bell = await page.page.locator(TID["bell-count"]).inner_text()
    assert bell.strip() == "۲", f"the bell shows {bell!r}, not Persian digits"


async def test_a_reviewer_approves_a_claim_with_the_credit_and_the_queue_lets_it_go(op, seeded, base_url):
    page = await op("REVIEWER")
    await page.page.goto(f"{base_url}/payments?tab=review")
    approve = page.page.locator(TID["payment-approve-btn"])
    assert await approve.count() == 2, "both ambiguous claims should offer their suggested credit"
    await approve.first.click()
    await page.page.wait_for_load_state("networkidle")

    async with async_session_maker() as db:
        counts = await review.tab_counts(db)
    assert counts["review"] == 1 and counts["manual"] + counts["auto"] == 1
    # The credit is spent, so the second claim is no longer offered it.
    assert await page.page.locator(TID["payment-approve-btn"]).count() == 0
    assert (await page.page.locator(TID["bell-count"]).inner_text()).strip() == "۱"
    assert not page.problems(), page.problems()


async def test_a_destructive_control_does_nothing_until_its_confirmation_is_ticked(op, base_url):
    page = await op("ADMIN")
    await page.page.goto(f"{base_url}/devices")
    await page.page.fill(TID["device-name-input"], "گوشی تست")
    await page.page.fill(TID["device-code-input"], "phone-browser")
    await page.page.click(TID["device-create-btn"])
    await page.page.wait_for_load_state("networkidle")

    # The row's actions are a disclosure of their own (the page has another).
    await page.page.locator(TID["device-actions-summary"]).click()
    await page.page.locator(TID["device-rotate-btn"]).click()  # without ticking
    await page.page.wait_for_load_state("networkidle")
    assert await page.page.locator(TID["flash-error"]).is_visible()
    assert await page.page.locator(TID["token-reveal"]).count() == 0, "a token was issued without the confirmation"

    await page.page.locator(TID["device-actions-summary"]).click()
    await page.page.locator(TID["device-rotate-confirm"]).check()
    await page.page.locator(TID["device-rotate-btn"]).click()
    await page.page.wait_for_load_state("networkidle")
    assert "phone-browser" in await page.page.content()


async def test_the_bell_count_keeps_itself_up_to_date_without_a_page_load(op, base_url, monkeypatch):
    """A payment that arrives while the operator is looking at another screen
    has to reach them. The count is polled, so the number on the page changes
    without anyone reloading anything."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "bell_poll_seconds", 1)
    page = await op("ADMIN")
    await page.page.goto(f"{base_url}/accounts")
    assert await page.page.locator(TID["bell-count"]).count() == 0, "nothing is waiting yet"

    # Two payments and one credit: the matcher refuses to guess and both land
    # in the review queue, while the browser sits on another page.
    acct = await _account_with_card()
    await _claim(account_id=acct, alobot_id=8801)
    await _claim(account_id=acct, alobot_id=8802)
    await _credit()
    async with async_session_maker() as db:
        await settle.settle(db)

    badge = page.page.locator(TID["bell-count"])
    await badge.wait_for(timeout=10_000)
    assert (await badge.inner_text()).strip() == "۲"
    assert (await page.page.title()).startswith("(۲)"), "the tab does not say how many are waiting"
    assert not page.problems(), page.problems()


async def test_no_section_scrolls_sideways_on_a_phone(op, seeded, base_url):
    """Operators check the queue on a phone. A page wider than the screen is
    not a cosmetic problem: the actions live at the end of each row, which is
    exactly the part that falls off."""
    page = await op("ADMIN")
    await page.page.set_viewport_size({"width": 390, "height": 844})
    too_wide: list[str] = []
    for page_id in SECTIONS:
        if not visible("ADMIN", page_id):
            continue
        path = "/" if page_id == "overview" else f"/{page_id}"
        await page.page.goto(base_url + path)
        overflow = await page.page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        if overflow > 0:
            too_wide.append(f"{page_id}: {overflow}px past the screen")
    assert not too_wide, "sections overflow a phone screen:\n" + "\n".join(too_wide)
