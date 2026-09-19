"""Relay phones and their tokens. Every token-changing action is audited and
the plain token is returned exactly once to the caller that created it."""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Device, DeviceCredential
from app.services.audit import audit_row
from app.services.ingest import issue_credential, revoke_credentials


class DeviceError(ValueError):
    pass


async def list_devices(session: AsyncSession) -> list[tuple[Device, DeviceCredential | None]]:
    devices = (await session.execute(select(Device).order_by(Device.id))).scalars().all()
    creds = (await session.execute(select(DeviceCredential).where(DeviceCredential.status == "ACTIVE"))).scalars().all()
    by_device = {c.device_id: c for c in creds}
    return [(d, by_device.get(d.id)) for d in devices]


async def create_device(session: AsyncSession, actor, *, code: str, display_name: str) -> tuple[Device, str]:
    code = code.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,31}", code):
        raise DeviceError("کد دستگاه: حروف کوچک لاتین، رقم، خط تیره؛ ۲ تا ۳۲ نویسه.")
    if not display_name.strip():
        raise DeviceError("نام دستگاه لازم است.")
    device = Device(code=code, display_name=display_name.strip())
    session.add(device)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise DeviceError("دستگاهی با این کد از قبل هست.") from None
    token, _ = await issue_credential(session, device)
    session.add(audit_row(action="device.create", entity_type="device", entity_id=code, actor_role=actor.role, actor_operator_id=actor.id, after={"display_name": device.display_name}))
    await session.commit()
    return device, token


async def rotate(session: AsyncSession, actor, device_id: int) -> tuple[Device, str]:
    device = await session.get(Device, device_id)
    if device is None:
        raise DeviceError("چنین دستگاهی نیست.")
    token, _ = await issue_credential(session, device)
    session.add(audit_row(action="device.rotate", entity_type="device", entity_id=device.code, actor_role=actor.role, actor_operator_id=actor.id))
    await session.commit()
    return device, token


async def revoke(session: AsyncSession, actor, device_id: int) -> Device:
    device = await session.get(Device, device_id)
    if device is None:
        raise DeviceError("چنین دستگاهی نیست.")
    n = await revoke_credentials(session, device.id, flush_only=True)
    session.add(audit_row(action="device.revoke", entity_type="device", entity_id=device.code, actor_role=actor.role, actor_operator_id=actor.id, after={"revoked": n}))
    await session.commit()
    return device


async def set_active(session: AsyncSession, actor, device_id: int, active: bool) -> Device:
    device = await session.get(Device, device_id)
    if device is None:
        raise DeviceError("چنین دستگاهی نیست.")
    before = device.is_active
    device.is_active = active
    session.add(audit_row(action="device.active", entity_type="device", entity_id=device.code, actor_role=actor.role, actor_operator_id=actor.id, before={"is_active": before}, after={"is_active": active}))
    await session.commit()
    return device


async def rename(session: AsyncSession, actor, device_id: int, display_name: str) -> Device:
    device = await session.get(Device, device_id)
    if device is None or not display_name.strip():
        raise DeviceError("نام دستگاه لازم است.")
    before = device.display_name
    device.display_name = display_name.strip()
    session.add(audit_row(action="device.rename", entity_type="device", entity_id=device.code, actor_role=actor.role, actor_operator_id=actor.id, before={"display_name": before}, after={"display_name": device.display_name}))
    await session.commit()
    return device
