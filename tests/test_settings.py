"""Phase 1 task 7: the dashboard's own settings, from a typed registry."""

import pytest
from sqlalchemy import select

from app.core.settings_registry import REGISTRY, spec_for
from app.db.session import async_session_maker
from app.models import AuditLog, Setting
from app.services import settings as settings_service
from tests.web import logged_in


def test_registry_keys_are_unique_and_every_spec_has_label_and_hint():
    seen = set()
    for spec in REGISTRY:
        assert (spec.scope, spec.key) not in seen
        seen.add((spec.scope, spec.key))
        assert spec.label and spec.hint


async def test_unset_setting_reads_as_its_default(session):
    value = await settings_service.get(session, "events", "retention_days")
    assert value == spec_for("events", "retention_days").default


async def test_set_many_writes_rows_and_one_audit_row_per_change(session):
    await settings_service.set_many(
        session,
        {("general", "shop_name"): "آلو", ("events", "retention_days"): 45},
        actor_email="admin@x.io",
        actor_role="ADMIN",
        actor_operator_id=7,
    )
    rows = (await session.execute(select(Setting))).scalars().all()
    assert {(r.scope, r.key): r.value for r in rows} == {("general", "shop_name"): "آلو", ("events", "retention_days"): 45}
    assert all(r.updated_by == "admin@x.io" for r in rows)
    audits = (await session.execute(select(AuditLog).order_by(AuditLog.id))).scalars().all()
    assert [(a.action, a.entity_id, a.before, a.after) for a in audits] == [
        ("settings.set", "general/shop_name", None, "آلو"),
        ("settings.set", "events/retention_days", None, 45),
    ]


async def test_unchanged_value_writes_no_audit_row(session):
    await settings_service.set_many(session, {("events", "retention_days"): 45}, actor_email="a", actor_role="ADMIN")
    await settings_service.set_many(session, {("events", "retention_days"): 45}, actor_email="a", actor_role="ADMIN")
    audits = (await session.execute(select(AuditLog))).scalars().all()
    assert len(audits) == 1


async def test_unknown_key_is_refused_and_nothing_is_written(session):
    with pytest.raises(settings_service.SettingError):
        await settings_service.set_many(
            session, {("general", "shop_name"): "x", ("nope", "key"): 1}, actor_email="a", actor_role="ADMIN"
        )
    assert (await session.execute(select(Setting))).scalars().all() == []


@pytest.mark.parametrize("bad", [0, 400, "abc"])
async def test_int_setting_is_bounded_and_typed(session, bad):
    with pytest.raises(settings_service.SettingError):
        await settings_service.set_many(session, {("events", "retention_days"): bad}, actor_email="a", actor_role="ADMIN")


async def test_settings_page_shows_every_registry_key_with_label_hint_and_value():
    c = await logged_in("ADMIN")
    async with c:
        r = await c.get("/settings")
    assert r.status_code == 200
    for spec in REGISTRY:
        assert spec.label in r.text and spec.hint in r.text
        assert f'name="{spec.scope}.{spec.key}"' in r.text


async def test_settings_form_saves_and_reads_back_from_the_database():
    c = await logged_in("ADMIN")
    async with c:
        r = await c.post(
            "/settings",
            data={"general.shop_name": "فروشگاه آلو", "events.retention_days": "60"},
            headers={"Origin": "http://test"},
        )
        assert r.status_code == 303, r.text
    async with async_session_maker() as s:
        assert await settings_service.get(s, "general", "shop_name") == "فروشگاه آلو"
        assert await settings_service.get(s, "events", "retention_days") == 60


async def test_settings_form_refuses_a_bad_value_with_a_readable_sentence():
    c = await logged_in("ADMIN")
    async with c:
        r = await c.post("/settings", data={"events.retention_days": "999"}, headers={"Origin": "http://test"})
    assert r.status_code == 400 and "رویدادها" in r.text and "۳۶۵" in r.text


async def test_reviewer_cannot_open_the_settings_page():
    c = await logged_in("REVIEWER")
    async with c:
        r = await c.get("/settings")
    assert r.status_code == 403
