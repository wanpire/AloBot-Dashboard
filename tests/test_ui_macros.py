"""The component macros in _ui.html.

Two things are worth holding here. That every macro forwards arbitrary
attributes, because without that a screen cannot attach an hx-* or a
data-testid and would have to break out of the macro to do it. And that the
state colour map keeps up with the models: a status added to a constant and
not to the map would render grey and unlabelled, which reads like a bug in
the data rather than a gap in the styling.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from app.models.bot_notification import NOTIFICATION_STATUSES
from app.models.broadcast import BROADCAST_STATUSES, RECIPIENT_STATUSES
from app.models.claims import CLAIM_STATUSES, MATCH_STATUSES
from app.models.ingest import (
    ACCOUNT_STATUSES,
    CARD_STATUSES,
    CLASSIFICATIONS,
    CREDENTIAL_STATUSES,
    DIRECTIONS,
    DISPOSITIONS,
)
from app.models.operator import OPERATOR_ROLES
from app.web.templating import templates

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "web" / "templates"

FAMILIES = {
    "claim": CLAIM_STATUSES,
    "match": MATCH_STATUSES,
    "notification": NOTIFICATION_STATUSES,
    "broadcast": BROADCAST_STATUSES,
    "recipient": RECIPIENT_STATUSES,
    "account": ACCOUNT_STATUSES,
    "card": CARD_STATUSES,
    "credential": CREDENTIAL_STATUSES,
    "direction": DIRECTIONS,
    "disposition": DISPOSITIONS,
    "classification": CLASSIFICATIONS,
    "role": OPERATOR_ROLES,
}


def ui():
    return templates.get_template("_ui.html").make_module()


def render(source: str, **context) -> str:
    """Render a snippet that imports the macros, the way a screen will."""
    return templates.env.from_string('{% import "_ui.html" as ui with context %}' + source).render(**context)


@pytest.mark.parametrize("family,values", sorted(FAMILIES.items()))
def test_every_state_the_models_can_hold_has_a_colour_and_a_word(family, values):
    tones = ui().STATE_TONES
    missing = [v for v in values if f"{family}:{v}" not in tones]
    assert not missing, f"{family} states with no entry in STATE_TONES: {missing}"


def test_the_outbox_dead_state_is_not_treated_as_an_ordinary_failure():
    """DEAD means nobody will try again; FAILED means the sweep will. They
    must not look the same."""
    tones = ui().STATE_TONES
    assert tones["notification:DEAD"][0] != tones["notification:FAILED"][0]


def test_the_same_word_in_two_families_can_mean_two_different_things():
    tones = ui().STATE_TONES
    assert tones["claim:PENDING"][1] != tones["notification:PENDING"][1]


@pytest.mark.parametrize("snippet", [
    '{{ ui.state_badge("AUTO_VERIFIED", family="claim", data_testid="probe") }}',
    '{{ ui.btn("ذخیره", tone="primary", data_testid="probe") }}',
    '{{ ui.stat_tile("عنوان", "۱۲", data_testid="probe") }}',
    '{% call ui.card("عنوان", data_testid="probe") %}بدنه{% endcall %}',
    '{% call ui.table(["الف"], data_testid="probe") %}{% endcall %}',
    '{{ ui.field("code", "کد", data_testid="probe") }}',
    '{{ ui.form_errors(["خطا"], data_testid="probe") }}',
    '{{ ui.empty_state("چیزی نیست", data_testid="probe") }}',
    '{{ ui.toast("انجام شد", data_testid="probe") }}',
    '{{ ui.pagination(2, 5, "/x?page={page}", data_testid="probe") }}',
    '{{ ui.confirm_button("حذف", "می‌دانم", "/x", data_testid="probe") }}',
    '{% call ui.page_header("عنوان", data_testid="probe") %}{% endcall %}',
])
def test_every_macro_forwards_a_data_testid(snippet):
    assert 'data-testid="probe"' in render(snippet)


@pytest.mark.parametrize("snippet", [
    '{{ ui.btn("ذخیره", attrs={"hx-post": "/save", "hx-target": "#out"}) }}',
    '{% call ui.row(attrs={"hx-get": "/row/1", "id": "row-1"}) %}{% endcall %}',
    '{{ ui.state_badge("SENT", family="notification", attrs={"hx-swap-oob": "true"}) }}',
])
def test_htmx_attributes_survive_the_macro(snippet):
    out = render(snippet)
    assert "hx-" in out, out


def test_an_id_passes_through_so_a_swap_can_target_it():
    assert 'id="row-1"' in render('{% call ui.row(attrs={"id": "row-1"}) %}{% endcall %}')


def test_a_caller_can_add_a_class_without_losing_the_macros_own():
    out = render('{{ ui.btn("x", tone="primary", class_="w-100") }}')
    assert "btn btn-primary" in out and "w-100" in out


def test_a_cell_carries_its_column_name_so_it_can_stack_on_a_phone():
    out = render('{% call ui.table(["مشتری"]) %}{% call ui.row() %}{% call ui.cell("مشتری") %}علی{% endcall %}{% endcall %}{% endcall %}')
    assert 'data-label="مشتری"' in out


def test_the_table_never_scrolls_sideways():
    """Tabler's own helper is table-responsive, which scrolls. This one stacks."""
    out = render('{% call ui.table(["الف"]) %}{% endcall %}')
    assert "table-responsive" not in out
    assert "ui-table" in out


def test_icons_are_inline_svg_and_no_icon_font_is_loaded():
    out = render('{{ ui.icon("home") }}')
    assert "<svg" in out and "currentColor" in out
    shell = (TEMPLATES / "base_v2.html").read_text()
    assert "tabler-icons" not in shell and "icons-font" not in shell


def test_every_icon_the_sidebar_names_exists():
    from app.web.nav import NAV

    for group in NAV:
        for item in group.items:
            assert (TEMPLATES / "icons" / f"{item.icon}.svg").exists(), f"icon {item.icon} is missing"


def test_nothing_in_the_macros_hardcodes_a_side():
    """The panel is RTL; a hardcoded left or right is a bug waiting for the
    first English label."""
    source = (TEMPLATES / "_ui.html").read_text()
    body = "\n".join(line for line in source.splitlines() if not line.strip().startswith(("{#", "#}")))
    for bad in ("ms-left", "me-right", "text-left", "text-right", "float-left", "float-right", "border-left", "border-right"):
        assert bad not in body, f"{bad} is a hardcoded side"


def test_a_screen_built_on_the_macros_writes_no_framework_class_of_its_own():
    """The point of the library: when Tabler renames a class it moves in one
    file. A screen that reaches for btn- or col- directly has escaped that."""
    migrated = TEMPLATES / "payments.html"
    body = "\n".join(
        line for line in migrated.read_text().splitlines()
        if not line.strip().startswith("{#") and "ui." not in line
    )
    for escaped in re.findall(r'class="([^"]*)"', body):
        assert "btn-" not in escaped, f"raw button class in the screen: {escaped}"
        assert "badge bg-" not in escaped, f"raw badge class in the screen: {escaped}"
