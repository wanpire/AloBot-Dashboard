from __future__ import annotations

from typing import Any

from app.core.logging import current_request_id
from app.models import AuditLog


def audit_row(
    *,
    action: str,
    entity_type: str,
    entity_id: str,
    actor_role: str,
    actor_operator_id: int | None = None,
    before: Any = None,
    after: Any = None,
    note: str | None = None,
) -> AuditLog:
    """An audit row to `session.add()` inside the SAME transaction as the
    write it describes - never committed separately, so there is no
    "written but not recorded" state."""
    return AuditLog(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_role=actor_role,
        actor_operator_id=actor_operator_id,
        before=before,
        after=after,
        request_id=current_request_id(),
        note=note,
    )
