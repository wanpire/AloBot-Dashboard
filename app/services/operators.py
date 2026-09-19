"""Managing who may operate the dashboard.

Two refusals are properties of the data, not of the screen, so they live in
the transaction: an operator never changes their own role or deactivates
themselves, and the last active ADMIN is never demoted or deactivated. The
second is a race - two admins demoting each other at once - so the rows of
every active admin are locked (`FOR UPDATE`) before the conditional UPDATE
decides; the second transaction then sees the first one's result.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models import Operator, OperatorSession
from app.models.operator import OPERATOR_ROLES
from app.services.audit import audit_row


class OperatorError(ValueError):
    """A human-readable refusal. Nothing was written."""


ERR_SELF_ROLE = "نقش خودتان را نمی‌توانید تغییر دهید."
ERR_SELF_ACTIVE = "خودتان را نمی‌توانید غیرفعال کنید."
ERR_LAST_ADMIN = "آخرین مدیر فعال را نمی‌توان تغییر داد؛ اول یک مدیر دیگر بسازید."
ERR_ROLE = "نقش نامعتبر است."
ERR_NOT_FOUND = "چنین اپراتوری وجود ندارد."


async def list_operators(session: AsyncSession) -> list[Operator]:
    return list((await session.execute(select(Operator).order_by(Operator.id))).scalars().all())


async def create(
    session: AsyncSession, actor: Operator, *, email: str, display_name: str, password: str, role: str
) -> Operator:
    if role not in OPERATOR_ROLES:
        raise OperatorError(ERR_ROLE)
    email = email.strip().lower()
    if len(password) < 12:
        raise OperatorError("رمز عبور باید دست‌کم ۱۲ نویسه باشد.")
    if await session.scalar(select(Operator.id).where(Operator.email == email)):
        raise OperatorError("اپراتوری با این ایمیل از قبل وجود دارد.")
    operator = Operator(email=email, display_name=display_name.strip() or email, password_hash=hash_password(password), role=role)
    session.add(operator)
    session.add(
        audit_row(
            action="operator.create",
            entity_type="operator",
            entity_id=email,
            actor_role=actor.role,
            actor_operator_id=actor.id,
            after={"role": role, "display_name": operator.display_name},
        )
    )
    await session.commit()
    await session.refresh(operator)
    return operator


async def _lock_active_admins(session: AsyncSession) -> None:
    await session.execute(text("SELECT id FROM operators WHERE role = 'ADMIN' AND is_active FOR UPDATE"))


_OTHER_ACTIVE_ADMIN_EXISTS = (
    "EXISTS (SELECT 1 FROM operators o2 WHERE o2.id <> :id AND o2.role = 'ADMIN' AND o2.is_active)"
)


async def set_role(session: AsyncSession, actor: Operator, operator_id: int, role: str) -> Operator:
    if role not in OPERATOR_ROLES:
        raise OperatorError(ERR_ROLE)
    if operator_id == actor.id:
        raise OperatorError(ERR_SELF_ROLE)
    await _lock_active_admins(session)
    target = await session.get(Operator, operator_id, with_for_update=True)
    if target is None:
        await session.rollback()
        raise OperatorError(ERR_NOT_FOUND)
    before = target.role
    result = await session.execute(
        text(
            "UPDATE operators SET role = :role WHERE id = :id AND ("
            ":role = 'ADMIN' OR role <> 'ADMIN' OR NOT is_active OR " + _OTHER_ACTIVE_ADMIN_EXISTS + ")"
        ),
        {"role": role, "id": operator_id},
    )
    if result.rowcount != 1:
        await session.rollback()
        raise OperatorError(ERR_LAST_ADMIN)
    session.add(
        audit_row(
            action="operator.role",
            entity_type="operator",
            entity_id=target.email,
            actor_role=actor.role,
            actor_operator_id=actor.id,
            before={"role": before},
            after={"role": role},
        )
    )
    await session.commit()
    await session.refresh(target)
    return target


async def set_active(session: AsyncSession, actor: Operator, operator_id: int, active: bool) -> Operator:
    if operator_id == actor.id:
        raise OperatorError(ERR_SELF_ACTIVE)
    await _lock_active_admins(session)
    target = await session.get(Operator, operator_id, with_for_update=True)
    if target is None:
        await session.rollback()
        raise OperatorError(ERR_NOT_FOUND)
    before = target.is_active
    result = await session.execute(
        text(
            "UPDATE operators SET is_active = :active WHERE id = :id AND ("
            ":active OR role <> 'ADMIN' OR NOT is_active OR " + _OTHER_ACTIVE_ADMIN_EXISTS + ")"
        ),
        {"active": active, "id": operator_id},
    )
    if result.rowcount != 1:
        await session.rollback()
        raise OperatorError(ERR_LAST_ADMIN)
    if not active:
        await session.execute(
            update(OperatorSession)
            .where(OperatorSession.operator_id == operator_id, OperatorSession.revoked_at.is_(None))
            .values(revoked_at=dt.datetime.now(dt.timezone.utc))
        )
    session.add(
        audit_row(
            action="operator.active",
            entity_type="operator",
            entity_id=target.email,
            actor_role=actor.role,
            actor_operator_id=actor.id,
            before={"is_active": before},
            after={"is_active": active},
        )
    )
    await session.commit()
    await session.refresh(target)
    return target
