"""Reading and writing the dashboard's own settings against the registry."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings_registry import REGISTRY, SettingSpec, spec_for
from app.models import Setting
from app.services.audit import audit_row
from app.web.format import fa_number


class SettingError(ValueError):
    """A human-readable reason a write was refused. Nothing was written."""


def coerce(spec: SettingSpec, raw: Any) -> Any:
    if spec.kind == "text":
        return str(raw or "").strip()
    if spec.kind == "bool":
        return raw in (True, 1, "1", "true", "on", "yes")
    if spec.kind in ("int", "chat_id"):
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            if spec.kind == "chat_id":
                return None
            raise SettingError(f"«{spec.label}» نمی‌تواند خالی باشد.")
        try:
            value = int(str(raw).strip())
        except ValueError:
            raise SettingError(f"«{spec.label}» باید یک عدد باشد.") from None
        if spec.kind == "int" and (
            (spec.min is not None and value < spec.min) or (spec.max is not None and value > spec.max)
        ):
            raise SettingError(f"«{spec.label}» باید عددی بین {fa_number(spec.min)} و {fa_number(spec.max)} باشد.")
        return value
    raise SettingError(f"نوع تنظیم «{spec.label}» ناشناخته است.")


async def get_all(session: AsyncSession) -> dict[tuple[str, str], Any]:
    rows = (await session.execute(select(Setting))).scalars().all()
    stored = {(r.scope, r.key): r.value for r in rows}
    return {(s.scope, s.key): stored.get((s.scope, s.key), s.default) for s in REGISTRY}


async def get(session: AsyncSession, scope: str, key: str) -> Any:
    spec = spec_for(scope, key)
    if spec is None:
        raise SettingError(f"تنظیم ناشناخته: {scope}/{key}")
    row = await session.get(Setting, (scope, key))
    return spec.default if row is None else row.value


async def set_many(
    session: AsyncSession,
    updates: dict[tuple[str, str], Any],
    *,
    actor_email: str,
    actor_role: str,
    actor_operator_id: int | None = None,
) -> int:
    """Validate everything first, then write every change and its audit row
    in one transaction. Returns the number of keys that actually changed."""
    coerced: dict[tuple[str, str], Any] = {}
    for (scope, key), raw in updates.items():
        spec = spec_for(scope, key)
        if spec is None:
            raise SettingError(f"تنظیم «{scope}/{key}» وجود ندارد و نوشته نمی‌شود.")
        coerced[(scope, key)] = coerce(spec, raw)

    changed = 0
    for (scope, key), value in coerced.items():
        row = await session.get(Setting, (scope, key))
        before = None if row is None else row.value
        if row is not None and row.value == value:
            continue
        if row is None:
            session.add(Setting(scope=scope, key=key, value=value, updated_by=actor_email))
        else:
            row.value = value
            row.updated_by = actor_email
        session.add(
            audit_row(
                action="settings.set",
                entity_type="setting",
                entity_id=f"{scope}/{key}",
                actor_role=actor_role,
                actor_operator_id=actor_operator_id,
                before=before,
                after=value,
            )
        )
        changed += 1
    await session.commit()
    return changed
