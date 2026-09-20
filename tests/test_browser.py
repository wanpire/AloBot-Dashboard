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
        await page.page.fill("input[name=email]", email)
        await page.page.fill("input[name=password]", PASSWORD)
        await page.page.click("button[type=submit]")
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
    assert await page.page.locator(".sidebar").is_visible()
    assert not page.problems(), page.problems()


async def test_a_wrong_password_says_one_generic_thing_and_opens_nothing(browser, base_url):
    await make_operator(email="admin@x.io", role="ADMIN")
    context = await browser.new_context()
    page = await context.new_page()
    await page.goto(f"{base_url}/login")
    await page.fill("input[name=email]", "admin@x.io")
    await page.fill("input[name=password]", "wrong")
    await page.click("button[type=submit]")
    await page.wait_for_load_state("networkidle")
    assert await page.locator(".flash--err").is_visible()
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
        assert await page.page.locator("h1, h2, .content").first.is_visible(), f"{path} rendered nothing"
    assert not page.problems(), "the panel is not clean in a browser:\n" + "\n".join(page.problems())


async def test_the_shell_is_right_to_left_and_the_numbers_are_persian(op, seeded, base_url):
    page = await op("ADMIN")
    await page.page.goto(f"{base_url}/payments?tab=review")
    direction = await page.page.evaluate("getComputedStyle(document.documentElement).direction")
    assert direction == "rtl"
    # The bell carries the review count, and an operator reads it in Persian.
    bell = await page.page.locator(".bell .badge--count").inner_text()
    assert bell.strip() == "۲", f"the bell shows {bell!r}, not Persian digits"


async def test_a_reviewer_approves_a_claim_with_the_credit_and_the_queue_lets_it_go(op, seeded, base_url):
    page = await op("REVIEWER")
    await page.page.goto(f"{base_url}/payments?tab=review")
    approve = page.page.locator("form[action^='/payments/'][action*='/approve'] button")
    assert await approve.count() == 2, "both ambiguous claims should offer their suggested credit"
    await approve.first.click()
    await page.page.wait_for_load_state("networkidle")

    async with async_session_maker() as db:
        counts = await review.tab_counts(db)
    assert counts["review"] == 1 and counts["manual"] + counts["auto"] == 1
    # The credit is spent, so the second claim is no longer offered it.
    assert await page.page.locator("form[action*='/approve'] button").count() == 0
    assert (await page.page.locator(".bell .badge--count").inner_text()).strip() == "۱"
    assert not page.problems(), page.problems()


async def test_a_destructive_control_does_nothing_until_its_confirmation_is_ticked(op, base_url):
    page = await op("ADMIN")
    await page.page.goto(f"{base_url}/devices")
    await page.page.fill("input[name=display_name]", "گوشی تست")
    await page.page.fill("input[name=code]", "phone-browser")
    await page.page.click("form[action='/devices'] button[type=submit]")
    await page.page.wait_for_load_state("networkidle")

    # The row's actions are a disclosure of their own (the page has another).
    await page.page.locator("details:has(form[action$='/rotate']) summary").click()
    rotate = page.page.locator("form[action$='/rotate']")
    await rotate.locator("button[type=submit]").click()  # without ticking
    await page.page.wait_for_load_state("networkidle")
    assert await page.page.locator(".flash--err, .flash").first.is_visible()
    assert await page.page.locator(".token-reveal, code").count() == 0, "a token was issued without the confirmation"

    await page.page.locator("details:has(form[action$='/rotate']) summary").click()
    rotate = page.page.locator("form[action$='/rotate']")
    await rotate.locator("input[name=confirm]").check()
    await rotate.locator("button[type=submit]").click()
    await page.page.wait_for_load_state("networkidle")
    assert "phone-browser" in await page.page.content()
