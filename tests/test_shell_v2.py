"""The Tabler shell (base_v2.html), which no real screen uses yet.

It is tested now rather than when a screen adopts it, because the promises
worth holding are structural: that it renders at all, that it takes the
context the app already provides without a route change, and above all that
it makes no external request. A panel that quietly fetches a font from a CDN
works on a laptop and fails on a locked-down network.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import re

import pytest

from app.web.nav import NAV, visible_nav
from app.web.templating import templates

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "web" / "static"


class FakeOperator:
    display_name = "پیمان"
    role = "ADMIN"


def render(page_id: str | None = "payments") -> str:
    """The shell, rendered with exactly what app/web/templating.py provides."""
    now = dt.datetime(2026, 9, 20, 12, 0, tzinfo=dt.timezone.utc)
    return templates.get_template("_shell_preview.html").render(
        operator=FakeOperator(),
        nav=visible_nav("ADMIN"),
        page_id=page_id,
        page_label=lambda pid: {"payments": "پرداخت‌ها"}.get(pid, pid),
        badges={"payments": 12},
        env_name="staging",
        app_version="abc1234567890",
        rows=[
            {"name": "مشتری یک", "amount": 2_800_000, "at": now, "state": "در انتظار", "tone": "yellow"},
            {"name": "مشتری دو", "amount": 1_500_000, "at": now, "state": "تایید شد", "tone": "green"},
        ],
    )


def test_the_shell_renders():
    html = render()
    assert html.lstrip().startswith("<!doctype html>")
    assert 'dir="rtl"' in html and 'lang="fa"' in html


def test_it_makes_no_external_request():
    """Every asset is served from this repository. The only absolute URLs
    allowed anywhere in the output are none at all."""
    html = render()
    external = [url for url in re.findall(r'(?:src|href)="(https?://[^"]+)"', html)]
    assert not external, f"the shell reaches out to: {external}"
    for asset in re.findall(r'(?:src|href)="(/static/[^"]+)"', html):
        assert (STATIC / asset[len("/static/"):]).exists(), f"{asset} is referenced but not vendored"


def test_every_vendored_file_is_present_with_its_licence():
    for path in (
        "vendor/tabler/css/tabler.rtl.min.css",
        "vendor/tabler/js/tabler.min.js",
        "vendor/tabler/LICENSE",
        "vendor/vazirmatn/vazirmatn-variable.woff2",
        "vendor/vazirmatn/LICENSE",
        "VENDOR.md",
    ):
        assert (STATIC / path).exists(), f"{path} is missing"
    assert "MIT" in (STATIC / "vendor/tabler/LICENSE").read_text()
    assert "SIL Open Font License" in (STATIC / "vendor/vazirmatn/LICENSE").read_text()


def test_the_vendored_css_itself_fetches_nothing():
    """A stylesheet can reach the network on its own, through @import or a
    url() pointing off-host, and no template review would catch it."""
    css = (STATIC / "vendor/tabler/css/tabler.rtl.min.css").read_text()
    assert "@import" not in css
    assert not re.search(r"url\(\s*['\"]?https?://", css)


def test_no_build_step_crept_in():
    for forbidden in ("package.json", "package-lock.json", "node_modules", "gulpfile.js", "yarn.lock"):
        assert not (ROOT / forbidden).exists(), f"{forbidden} exists; this project has no build step"


def test_the_sidebar_is_generated_from_nav_and_marks_where_you_are():
    html = render(page_id="payments")
    for group in NAV:
        assert group.label in html, f"group {group.label} is missing from the sidebar"
        for item in group.items:
            assert f'href="/{item.id}"' in html, f"{item.id} is missing from the sidebar"
    # The active item is marked, and only it.
    active = re.findall(r'href="/([a-z]+)"[^>]*aria-current="page"', html)
    assert active == ["payments"], active
    # Its group is open so the item is visible when the page loads.
    assert 'class="collapse show"' in html


def test_the_counts_in_the_sidebar_are_persian():
    assert ">۱۲<" in render(), "the badge is not rendered in Persian digits"


def test_the_blocks_a_page_will_need_all_exist():
    source = (ROOT / "app" / "web" / "templates" / "base_v2.html").read_text()
    for block in ("page_title", "page_actions", "content", "extra_head", "extra_scripts"):
        assert "{% block " + block + " %}" in source, f"block {block} is missing"


def test_the_theme_choice_is_applied_before_paint_and_remembered():
    source = (ROOT / "app" / "web" / "templates" / "base_v2.html").read_text()
    head = source.split("</head>")[0]
    assert "localStorage.getItem" in head, "the stored theme is read after paint, so dark mode flashes white"
    assert 'setAttribute("data-bs-theme"' in source
    assert "localStorage.setItem" in source


def test_tooltips_are_reinstated_after_an_htmx_swap():
    """Dropdowns, collapses and modals are delegated and survive a swap;
    tooltips are per-element and do not."""
    source = (ROOT / "app" / "web" / "templates" / "base_v2.html").read_text()
    assert "htmx.onLoad" in source
    assert 'bootstrap.Tooltip.getOrCreateInstance' in source


# The screens migrated to the Tabler shell so far. Each phase adds to this
# list deliberately, so a screen can never drift onto the new shell unnoticed
# and half-converted.
MIGRATED = {
    "payments.html", "login.html", "password.html", "error.html",
    "overview.html", "stats.html", "customers.html", "customer.html",
    "orders.html", "subscriptions.html", "resellers.html", "alobot_unavailable.html",
}


def test_only_the_screens_this_track_has_migrated_are_on_the_new_shell():
    templates_dir = ROOT / "app" / "web" / "templates"
    adopters = {
        p.name for p in templates_dir.glob("*.html")
        if 'extends "base_v2.html"' in p.read_text() and p.name != "_shell_preview.html"
    }
    assert adopters == MIGRATED, f"expected {MIGRATED}, found {adopters}"


def test_every_screen_not_yet_migrated_still_extends_the_old_base():
    templates_dir = ROOT / "app" / "web" / "templates"
    for path in templates_dir.glob("*.html"):
        if path.name.startswith("_") or path.name in MIGRATED | {"base.html", "base_v2.html"}:
            continue
        assert 'extends "base.html"' in path.read_text(), f"{path.name} extends neither base"


def test_the_new_shell_answers_to_the_same_handles_as_the_old_one():
    """A migrated screen must not lose the handles the browser suite selects
    on, or the suite silently stops covering it. Rendered, not grepped: some
    handles are passed through a macro and only exist in the output."""
    html = templates.get_template("_shell_preview.html").render(
        operator=FakeOperator(),
        nav=visible_nav("ADMIN"),
        page_id="payments",
        page_label=lambda pid: pid,
        badges={"payments": 3},
        env_name="test",
        app_version="v",
        rows=[],
        notice_text="ثبت شد.",
        error="خطا",
    )
    for handle in ("sidebar", "page-content", "bell", "bell-count", "flash-notice", "flash-error"):
        assert f'data-testid="{handle}"' in html, f"the shell dropped the {handle} handle"
