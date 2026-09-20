"""Phase 6 task 4: the runbook has to stay true.

A runbook is read in an incident, by someone who will type what it says
without checking. The failure mode is not that it is badly written, it is
that it names a screen that was renamed, a script that moved, or a setting
that no longer exists. These tests hold every such name against the code.
"""

from __future__ import annotations

import pathlib
import re

import pytest

import pytest

from app.core.config import Settings
from app.models.claims import CLAIM_STATUSES, MATCH_STATUSES
from app.models.operator import OPERATOR_ROLES
from app.services.review import TAB_LABELS
from app.web.nav import NAV

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUNBOOK = (ROOT / "RUNBOOK.md").read_text()
DEMO = (ROOT / "docs" / "DEMO.md").read_text()
# Both are read by someone following them literally, so both are held to the
# same standard.
OPERATOR_DOCS = {"RUNBOOK.md": RUNBOOK, "docs/DEMO.md": DEMO}

PERSIAN = "[\u0600-\u06FF]"
NAV_LABELS = {item.label for group in NAV for item in group.items}
# Persian names the runbook may use that are not sidebar sections: queue tabs,
# in-page controls and the settings rows they live in.
OTHER_UI_NAMES = set(TAB_LABELS.values()) | {
    "عملیات",          # the disclosure on a device row
    "خوانده‌نشده‌ها",    # the unparsed view of the transactions page
    "چت هشدارهای سیستم",  # a settings row
}


def _bold(text: str) -> set[str]:
    return {m.strip() for m in re.findall(r"\*\*([^*]+)\*\*", text)}


@pytest.mark.parametrize("doc", sorted(OPERATOR_DOCS))
def test_every_persian_name_in_bold_is_a_real_screen_or_control(doc):
    persian = {name for name in _bold(OPERATOR_DOCS[doc]) if re.search(PERSIAN, name)}
    unknown = {name for name in persian if name not in NAV_LABELS and name not in OTHER_UI_NAMES}
    assert not unknown, f"{doc} names screens that do not exist: {unknown}"


@pytest.mark.parametrize("doc", sorted(OPERATOR_DOCS))
def test_every_script_it_tells_someone_to_run_exists(doc):
    named = set(re.findall(r"scripts/[\w./-]+", OPERATOR_DOCS[doc]))
    missing = {path for path in named if not (ROOT / path).exists()}
    assert not missing, f"{doc} points at missing scripts: {missing}"


@pytest.mark.parametrize("doc", sorted(OPERATOR_DOCS))
def test_every_make_target_it_uses_exists(doc):
    makefile = (ROOT / "Makefile").read_text()
    targets = set(re.findall(r"^([a-z][\w-]*):", makefile, re.M))
    used = set(re.findall(r"\bmake ([a-z][\w-]*)", OPERATOR_DOCS[doc]))
    assert used <= targets, f"{doc} uses targets the Makefile does not define: {used - targets}"


def test_every_setting_the_runbook_names_is_a_real_setting():
    fields = {name.upper() for name in Settings.model_fields}
    shouted = set(re.findall(r"`([A-Z][A-Z0-9_]{3,})`", RUNBOOK))
    # Shouted names that are deliberately not settings: the AloBot pin file,
    # the roles, the statuses a claim can hold, and SQL.
    not_settings = {"ALOBOT_COMMIT", "SELECT"} | set(OPERATOR_ROLES) | set(CLAIM_STATUSES) | set(MATCH_STATUSES)
    unknown = shouted - fields - not_settings
    assert not unknown, f"the runbook names settings that do not exist: {unknown}"


def test_the_runbook_still_says_who_must_approve_an_alobot_change():
    """The one paragraph that must never quietly disappear."""
    assert "go-ahead" in RUNBOOK
    assert "ALOBOT_DB_WRITES_ENABLED" in RUNBOOK
    assert re.search(r"never touches|does not touch", RUNBOOK)
