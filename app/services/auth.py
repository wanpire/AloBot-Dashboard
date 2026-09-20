"""Operators, login, lockout, sessions, TOTP.

Everything that decides "is this person allowed in" is here and only here.
Two properties are load-bearing and tested by concurrency:

- The failed-attempt counter is incremented inside one UPDATE that also
  decides the lock, so five simultaneous wrong guesses count as five.
- A TOTP step is burned with a conditional UPDATE (`totp_last_step < step`),
  so the same code presented twice at once is accepted once.

Wrong password, wrong current password on a change, and wrong TOTP all
charge the same counter: each is an oracle for one of the operator's
secrets and none of them may be guessed for free.
"""

from __future__ import annotations

import base64
import datetime as dt
from dataclasses import dataclass

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    DUMMY_HASH,
    hash_password,
    hash_token,
    new_session_token,
    new_totp_secret,
    totp_verify,
    verify_password,
)
from app.models import Operator, OperatorSession

MAX_FAILED_ATTEMPTS = 5
LOCK_MINUTES = 15
SESSION_IDLE_HOURS = 12
SESSION_ABSOLUTE_DAYS = 30
SESSION_SLIDE_MINUTES = 5


@dataclass
class AuthResult:
    ok: bool
    reason: str | None = None  # bad_credentials | locked | totp_required | bad_totp
    operator: Operator | None = None
    locked_until: dt.datetime | None = None


def _now(now: dt.datetime | None) -> dt.datetime:
    return now or dt.datetime.now(dt.timezone.utc)


async def create_operator(
    session: AsyncSession, *, email: str, display_name: str, password: str, role: str
) -> Operator:
    operator = Operator(
        email=email.strip().lower(),
        display_name=display_name.strip(),
        password_hash=hash_password(password),
        role=role,
    )
    session.add(operator)
    await session.commit()
    await session.refresh(operator)
    return operator


async def get_operator_by_email(session: AsyncSession, email: str) -> Operator | None:
    result = await session.execute(select(Operator).where(Operator.email == email.strip().lower()))
    return result.scalar_one_or_none()


async def set_active(session: AsyncSession, operator: Operator, active: bool) -> None:
    operator.is_active = active
    await session.commit()


async def _charge_failure(session: AsyncSession, operator_id: int, now: dt.datetime) -> dt.datetime | None:
    """One UPDATE: increment, and lock if this increment crosses the line."""
    result = await session.execute(
        text(
            """
            UPDATE operators
               SET failed_attempts = failed_attempts + 1,
                   locked_until = CASE WHEN failed_attempts + 1 >= :max_failed THEN :lock_until
                                       ELSE locked_until END
             WHERE id = :id
         RETURNING locked_until
            """
        ),
        {"max_failed": MAX_FAILED_ATTEMPTS, "lock_until": now + dt.timedelta(minutes=LOCK_MINUTES), "id": operator_id},
    )
    locked_until = result.scalar_one()
    await session.commit()
    return locked_until


async def _reset_failures(session: AsyncSession, operator_id: int) -> None:
    await session.execute(
        update(Operator).where(Operator.id == operator_id).values(failed_attempts=0, locked_until=None)
    )
    await session.commit()


def _totp_secret_bytes(operator: Operator) -> bytes:
    return base64.b32decode(operator.totp_secret or "")


def _totp_enrolled(operator: Operator) -> bool:
    return bool(operator.totp_secret) and operator.totp_confirmed_at is not None


async def _burn_totp_step(session: AsyncSession, operator_id: int, step: int) -> bool:
    result = await session.execute(
        update(Operator)
        .where(Operator.id == operator_id)
        .where((Operator.totp_last_step.is_(None)) | (Operator.totp_last_step < step))
        .values(totp_last_step=step)
    )
    await session.commit()
    return result.rowcount == 1


async def authenticate(
    session: AsyncSession,
    email: str,
    password: str,
    totp: str | None = None,
    now: dt.datetime | None = None,
) -> AuthResult:
    now = _now(now)
    operator = await get_operator_by_email(session, email)
    if operator is None or not operator.is_active:
        verify_password(password, DUMMY_HASH)  # same cost as a real check: no enumeration by timing
        return AuthResult(False, "bad_credentials")
    await session.refresh(operator)
    if operator.locked_until is not None and operator.locked_until > now:
        return AuthResult(False, "locked", locked_until=operator.locked_until)
    if not verify_password(password, operator.password_hash):
        locked_until = await _charge_failure(session, operator.id, now)
        return AuthResult(False, "bad_credentials", locked_until=locked_until)
    if _totp_enrolled(operator):
        if not totp:
            return AuthResult(False, "totp_required")
        step = totp_verify(_totp_secret_bytes(operator), totp, at=int(now.timestamp()), last_step=operator.totp_last_step)
        if not step or not await _burn_totp_step(session, operator.id, int(step)):
            locked_until = await _charge_failure(session, operator.id, now)
            return AuthResult(False, "bad_totp", locked_until=locked_until)
    await _reset_failures(session, operator.id)
    await session.refresh(operator)
    return AuthResult(True, operator=operator)


# ── Sessions ──────────────────────────────────────────────────────────────


async def open_session(
    session: AsyncSession,
    operator: Operator,
    now: dt.datetime | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> str:
    now = _now(now)
    token = new_session_token()
    session.add(
        OperatorSession(
            operator_id=operator.id,
            token_hash=hash_token(token),
            created_at=now,
            last_seen_at=now,
            expires_at=now + dt.timedelta(days=SESSION_ABSOLUTE_DAYS),
            ip=ip,
            user_agent=(user_agent or "")[:256] or None,
        )
    )
    await session.commit()
    return token


async def resolve_session(session: AsyncSession, token: str, now: dt.datetime | None = None) -> Operator | None:
    now = _now(now)
    result = await session.execute(
        select(OperatorSession, Operator)
        .join(Operator, Operator.id == OperatorSession.operator_id)
        .where(OperatorSession.token_hash == hash_token(token))
    )
    row = result.first()
    if row is None:
        return None
    sess, operator = row
    if sess.revoked_at is not None or sess.expires_at <= now:
        return None
    if sess.last_seen_at + dt.timedelta(hours=SESSION_IDLE_HOURS) <= now:
        return None
    if not operator.is_active:
        return None
    # Slide the idle window only when it is stale: every request writing the
    # row would serialise an operator's own requests behind a row lock.
    if now - sess.last_seen_at >= dt.timedelta(minutes=SESSION_SLIDE_MINUTES):
        sess.last_seen_at = now
        await session.commit()
    return operator


async def revoke_session(session: AsyncSession, token: str, now: dt.datetime | None = None) -> None:
    await session.execute(
        update(OperatorSession)
        .where(OperatorSession.token_hash == hash_token(token), OperatorSession.revoked_at.is_(None))
        .values(revoked_at=_now(now))
    )
    await session.commit()


async def revoke_other_sessions(
    session: AsyncSession, operator_id: int, keep_token: str | None, now: dt.datetime | None = None
) -> int:
    stmt = (
        update(OperatorSession)
        .where(OperatorSession.operator_id == operator_id, OperatorSession.revoked_at.is_(None))
        .values(revoked_at=_now(now))
    )
    if keep_token:
        stmt = stmt.where(OperatorSession.token_hash != hash_token(keep_token))
    result = await session.execute(stmt)
    await session.commit()
    return result.rowcount


async def change_password(
    session: AsyncSession,
    operator: Operator,
    *,
    current: str,
    new: str,
    keep_token: str | None,
    now: dt.datetime | None = None,
) -> bool:
    now = _now(now)
    # The caller's operator may be detached from this session (request
    # scope); decide on a fresh row, not on whatever the object remembers.
    operator = await session.get(Operator, operator.id)
    if operator is None:
        return False
    if operator.locked_until is not None and operator.locked_until > now:
        return False
    if not verify_password(current, operator.password_hash):
        await _charge_failure(session, operator.id, now)
        return False
    operator.password_hash = hash_password(new)
    await session.commit()
    await revoke_other_sessions(session, operator.id, keep_token, now=now)
    await _reset_failures(session, operator.id)
    return True


# ── TOTP ──────────────────────────────────────────────────────────────────


async def enrol_totp(session: AsyncSession, operator: Operator) -> bytes:
    """Start enrolment: a new secret, not yet required at login until
    `confirm_totp` proves the operator's app produces the right codes."""
    secret = new_totp_secret()
    operator.totp_secret = base64.b32encode(secret).decode()
    operator.totp_confirmed_at = None
    operator.totp_last_step = None
    await session.commit()
    return secret


async def confirm_totp(session: AsyncSession, operator: Operator, code: str, now: dt.datetime | None = None) -> bool:
    now = _now(now)
    await session.refresh(operator)
    if not operator.totp_secret:
        return False
    step = totp_verify(_totp_secret_bytes(operator), code, at=int(now.timestamp()), last_step=operator.totp_last_step)
    if not step:
        return False
    operator.totp_confirmed_at = now
    operator.totp_last_step = int(step)
    await session.commit()
    return True


async def disable_totp(session: AsyncSession, operator: Operator) -> None:
    operator.totp_secret = None
    operator.totp_confirmed_at = None
    operator.totp_last_step = None
    await session.commit()
