"""Phase 1 task 3, the database half: operators, lockout, sessions, TOTP."""

import asyncio
import datetime as dt

import pytest

from app.core.security import totp_code
from app.db.session import async_session_maker
from app.services import auth

NOW = dt.datetime(2026, 9, 20, 10, 0, tzinfo=dt.timezone.utc)


async def make_admin(session, email="admin@x.io", password="correct horse battery", role="ADMIN"):
    return await auth.create_operator(session, email=email, display_name="Admin", password=password, role=role)


async def test_create_operator_lowercases_email_and_never_stores_the_password(session):
    op = await make_admin(session, email="Admin@X.io")
    assert op.email == "admin@x.io"
    assert "correct horse" not in op.password_hash


async def test_login_succeeds_with_the_right_password(session):
    await make_admin(session)
    result = await auth.authenticate(session, "admin@x.io", "correct horse battery", now=NOW)
    assert result.ok and result.operator.email == "admin@x.io"


async def test_login_fails_the_same_way_for_unknown_email_and_wrong_password(session):
    await make_admin(session)
    a = await auth.authenticate(session, "nobody@x.io", "x", now=NOW)
    b = await auth.authenticate(session, "admin@x.io", "wrong", now=NOW)
    assert (a.ok, a.reason) == (False, "bad_credentials")
    assert (b.ok, b.reason) == (False, "bad_credentials")


async def test_five_wrong_passwords_lock_the_account_for_fifteen_minutes(session):
    await make_admin(session)
    for _ in range(5):
        await auth.authenticate(session, "admin@x.io", "wrong", now=NOW)
    locked = await auth.authenticate(session, "admin@x.io", "correct horse battery", now=NOW)
    assert (locked.ok, locked.reason) == (False, "locked")
    assert locked.locked_until == NOW + dt.timedelta(minutes=15)
    later = await auth.authenticate(session, "admin@x.io", "correct horse battery", now=NOW + dt.timedelta(minutes=16))
    assert later.ok


async def test_five_concurrent_wrong_guesses_all_count(session):
    """The counter lives in the UPDATE, not in a SELECT-then-UPDATE."""
    await make_admin(session)

    async def guess():
        async with async_session_maker() as s:
            return await auth.authenticate(s, "admin@x.io", "wrong", now=NOW)

    await asyncio.gather(*(guess() for _ in range(5)))
    op = await auth.get_operator_by_email(session, "admin@x.io")
    await session.refresh(op)
    assert op.failed_attempts == 5
    assert op.locked_until is not None


async def test_successful_login_resets_the_counter(session):
    await make_admin(session)
    await auth.authenticate(session, "admin@x.io", "wrong", now=NOW)
    await auth.authenticate(session, "admin@x.io", "correct horse battery", now=NOW)
    op = await auth.get_operator_by_email(session, "admin@x.io")
    await session.refresh(op)
    assert op.failed_attempts == 0


async def test_inactive_operator_cannot_log_in(session):
    op = await make_admin(session)
    await auth.set_active(session, op, False)
    result = await auth.authenticate(session, "admin@x.io", "correct horse battery", now=NOW)
    assert (result.ok, result.reason) == (False, "bad_credentials")


async def test_session_token_resolves_until_idle_or_absolute_expiry(session):
    op = await make_admin(session)
    token = await auth.open_session(session, op, now=NOW, ip="1.2.3.4", user_agent="ua")
    assert (await auth.resolve_session(session, token, now=NOW)).id == op.id
    assert (await auth.resolve_session(session, token, now=NOW + dt.timedelta(hours=11))).id == op.id
    assert await auth.resolve_session(session, token, now=NOW + dt.timedelta(hours=11, minutes=1) + dt.timedelta(hours=12)) is None


async def test_session_idle_window_slides_only_when_stale(session):
    op = await make_admin(session)
    token = await auth.open_session(session, op, now=NOW)
    await auth.resolve_session(session, token, now=NOW + dt.timedelta(hours=11))  # slides
    assert (await auth.resolve_session(session, token, now=NOW + dt.timedelta(hours=22))).id == op.id
    assert await auth.resolve_session(session, token, now=NOW + dt.timedelta(hours=35)) is None


async def test_session_absolute_expiry_is_thirty_days(session):
    op = await make_admin(session)
    token = await auth.open_session(session, op, now=NOW)
    t = NOW
    while t < NOW + dt.timedelta(days=29, hours=20):
        t += dt.timedelta(hours=6)
        await auth.resolve_session(session, token, now=t)
    assert await auth.resolve_session(session, token, now=NOW + dt.timedelta(days=30, minutes=1)) is None


async def test_revoked_session_and_garbage_token_resolve_to_nothing(session):
    op = await make_admin(session)
    token = await auth.open_session(session, op, now=NOW)
    await auth.revoke_session(session, token)
    assert await auth.resolve_session(session, token, now=NOW) is None
    assert await auth.resolve_session(session, "not-a-token", now=NOW) is None


async def test_change_password_requires_the_current_one_and_revokes_other_sessions(session):
    op = await make_admin(session)
    mine = await auth.open_session(session, op, now=NOW)
    other = await auth.open_session(session, op, now=NOW)
    assert not await auth.change_password(session, op, current="wrong", new="new password here", keep_token=mine, now=NOW)
    assert await auth.change_password(session, op, current="correct horse battery", new="new password here", keep_token=mine, now=NOW)
    assert (await auth.resolve_session(session, mine, now=NOW)).id == op.id
    assert await auth.resolve_session(session, other, now=NOW) is None
    assert (await auth.authenticate(session, "admin@x.io", "new password here", now=NOW)).ok


async def test_wrong_current_password_charges_the_lockout_counter(session):
    op = await make_admin(session)
    for _ in range(5):
        await auth.change_password(session, op, current="wrong", new="new password here", keep_token=None, now=NOW)
    result = await auth.authenticate(session, "admin@x.io", "correct horse battery", now=NOW)
    assert result.reason == "locked"


async def test_totp_is_required_once_enrolled_and_each_code_is_single_use(session):
    op = await make_admin(session)
    secret = await auth.enrol_totp(session, op)
    at = int(NOW.timestamp())
    assert await auth.confirm_totp(session, op, totp_code(secret, at), now=NOW)
    without = await auth.authenticate(session, "admin@x.io", "correct horse battery", now=NOW + dt.timedelta(minutes=1))
    assert (without.ok, without.reason) == (False, "totp_required")
    later = NOW + dt.timedelta(minutes=2)
    code = totp_code(secret, int(later.timestamp()))
    first = await auth.authenticate(session, "admin@x.io", "correct horse battery", totp=code, now=later)
    assert first.ok
    replay = await auth.authenticate(session, "admin@x.io", "correct horse battery", totp=code, now=later)
    assert (replay.ok, replay.reason) == (False, "bad_totp")


async def test_wrong_totp_charges_the_lockout_counter(session):
    op = await make_admin(session)
    secret = await auth.enrol_totp(session, op)
    await auth.confirm_totp(session, op, totp_code(secret, int(NOW.timestamp())), now=NOW)
    for _ in range(5):
        await auth.authenticate(session, "admin@x.io", "correct horse battery", totp="000000", now=NOW)
    result = await auth.authenticate(session, "admin@x.io", "correct horse battery", totp=totp_code(secret, int(NOW.timestamp()) + 60), now=NOW)
    assert result.reason == "locked"
