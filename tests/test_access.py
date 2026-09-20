"""Phase 1 task 8: who may operate the dashboard - with the guards in the UPDATE."""

import asyncio

import pytest
import pytest_asyncio

from app.alobot.link import link
from app.alobot.seed import seed_alobot_copy
from tests.conftest import ALOBOT_ADMIN_URL


@pytest_asyncio.fixture
async def seeded_alobot():
    await seed_alobot_copy(ALOBOT_ADMIN_URL, customers=5, seed=3)
    await link.connect()
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


# ── AloBot's bot admins, from the same page ────────────────────────────────


async def test_the_reviewer_reset_route_is_not_shadowed_by_the_toggle_route():
    """`/access/reviewers/reset` must be declared before
    `/access/reviewers/{telegram_id}`, or "reset" is parsed as a telegram id
    and the button silently 422s."""
    c = await logged_in("ADMIN")
    async with c:
        r = await c.post("/access/reviewers/reset", headers={"Origin": "http://test"})
    assert r.status_code != 422, "the reset route is shadowed by the parameterised one"
    assert r.status_code == 303


async def test_bot_admins_can_be_managed_from_the_access_page(seeded_alobot):
    from sqlalchemy import text

    from tests.alobot_seed import write_engine

    c = await logged_in("ADMIN")
    async with c:
        added = await c.post("/access/bot-admins", data={"telegram_id": "555123456", "level": "support"}, headers={"Origin": "http://test"})
        assert added.status_code == 303, added.text
        toggled = await c.post("/access/reviewers/555123456", headers={"Origin": "http://test"})
        assert toggled.status_code == 303
        page = await c.get("/access")
    async with write_engine.connect() as conn:
        level = (await conn.execute(text("SELECT level FROM admin_users WHERE telegram_id=555123456"))).scalar_one()
        reviewers = (await conn.execute(text("SELECT value FROM app_config WHERE key='payment_review_admin_ids'"))).scalar_one()
    assert level == "support" and reviewers == "555123456"
    assert "ادمین‌های ربات آلوبات" in page.text


async def test_a_non_admin_telegram_id_cannot_be_made_a_reviewer(seeded_alobot):
    c = await logged_in("ADMIN")
    async with c:
        r = await c.post("/access/reviewers/424242", headers={"Origin": "http://test"})
    assert r.status_code == 400 and "ادمین" in r.text
