"""Phase 1 task 8: who may operate the dashboard - with the guards in the UPDATE."""

import asyncio

import pytest
from sqlalchemy import select

from app.db.session import async_session_maker
from app.models import AuditLog, Operator
from app.services import auth
from app.services import operators as ops
from tests.web import PASSWORD, client, logged_in, login, make_operator


async def test_create_operator_from_the_page_and_that_person_can_log_in():
    c = await logged_in("ADMIN")
    async with c:
        r = await c.post(
            "/access",
            data={"email": "New@x.io", "display_name": "نو", "password": PASSWORD, "role": "REVIEWER"},
            headers={"Origin": "http://test"},
        )
        assert r.status_code == 303, r.text
    async with client() as c2:
        assert (await login(c2, email="new@x.io")).status_code == 303
    async with async_session_maker() as s:
        audit = (await s.execute(select(AuditLog).where(AuditLog.action == "operator.create"))).scalar_one()
        assert audit.entity_id == "new@x.io" and audit.actor_role == "ADMIN"


async def test_access_page_lists_operators_with_role_and_state():
    c = await logged_in("ADMIN")
    await make_operator(email="ro@x.io", role="READ_ONLY")
    async with c:
        r = await c.get("/access")
    assert r.status_code == 200 and "ro@x.io" in r.text and "READ_ONLY" in r.text


async def test_you_cannot_change_your_own_role_or_deactivate_yourself(session):
    me = await make_operator(email="me@x.io", role="ADMIN")
    with pytest.raises(ops.OperatorError, match="خودتان"):
        await ops.set_role(session, actor=me, operator_id=me.id, role="READ_ONLY")
    with pytest.raises(ops.OperatorError, match="خودتان"):
        await ops.set_active(session, actor=me, operator_id=me.id, active=False)


async def test_the_last_active_admin_cannot_be_demoted_or_deactivated(session):
    me = await make_operator(email="me@x.io", role="ADMIN")
    other = await make_operator(email="other@x.io", role="ADMIN")
    await ops.set_role(session, actor=me, operator_id=other.id, role="REVIEWER")  # fine: I remain
    # Now `other` is not an admin; a second admin tries to demote me.
    other_admin = await make_operator(email="third@x.io", role="ADMIN")
    await ops.set_active(session, actor=me, operator_id=other_admin.id, active=False)
    with pytest.raises(ops.OperatorError, match="آخرین"):
        await ops.set_role(session, actor=other, operator_id=me.id, role="REVIEWER")


async def test_two_admins_demoting_each_other_at_once_leave_at_least_one_admin(session):
    a = await make_operator(email="a@x.io", role="ADMIN")
    b = await make_operator(email="b@x.io", role="ADMIN")

    async def demote(actor, target):
        async with async_session_maker() as s:
            try:
                await ops.set_role(s, actor=actor, operator_id=target.id, role="REVIEWER")
                return "ok"
            except ops.OperatorError:
                return "refused"

    results = await asyncio.gather(demote(a, b), demote(b, a))
    admins = (await session.execute(select(Operator).where(Operator.role == "ADMIN", Operator.is_active.is_(True)))).scalars().all()
    assert len(admins) >= 1
    assert "refused" in results


async def test_deactivating_an_operator_revokes_their_sessions(session):
    me = await make_operator(email="me@x.io", role="ADMIN")
    victim = await make_operator(email="v@x.io", role="REVIEWER")
    token = await auth.open_session(session, victim)
    await ops.set_active(session, actor=me, operator_id=victim.id, active=False)
    assert await auth.resolve_session(session, token) is None


async def test_role_change_from_the_page_is_audited_with_before_and_after():
    c = await logged_in("ADMIN")
    target = await make_operator(email="t@x.io", role="READ_ONLY")
    async with c:
        r = await c.post(f"/access/{target.id}/role", data={"role": "REVIEWER"}, headers={"Origin": "http://test"})
        assert r.status_code == 303, r.text
    async with async_session_maker() as s:
        audit = (await s.execute(select(AuditLog).where(AuditLog.action == "operator.role"))).scalar_one()
        assert (audit.before, audit.after) == ({"role": "READ_ONLY"}, {"role": "REVIEWER"})


async def test_refusal_from_the_page_is_the_servers_sentence():
    c = await logged_in("ADMIN", email="me@x.io")
    async with async_session_maker() as s:
        me = await auth.get_operator_by_email(s, "me@x.io")
    async with c:
        r = await c.post(f"/access/{me.id}/active", data={"active": "0"}, headers={"Origin": "http://test"})
    assert r.status_code == 400 and "خودتان" in r.text
