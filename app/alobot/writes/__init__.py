"""Writing to AloBot's tables - the only door, and it is bolted twice.

A write needs BOTH a write-capable connection (`ALOBOT_WRITE_DATABASE_URL`,
a role granted INSERT/UPDATE/DELETE on exactly the tables the dashboard may
edit) AND `ALOBOT_DB_WRITES_ENABLED`. Production has neither until Phase 7,
so every editor screen renders read-only and says so rather than failing on
save.

**Atomicity across the boundary, honestly.** The edit lands in AloBot's
database and the audit row in ours - two databases, so one transaction is
impossible. The order is: write, then audit. If the audit write fails the
edit has still happened, so that case is logged as `audit.unrecorded` (which
reaches the events page) rather than being silently lost. Nothing here
pretends the two are atomic.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import alobot_write_engine
from app.services.audit import audit_row

log = get_logger(__name__)

MESSAGE_DISABLED = (
    "ویرایش دادهٔ آلوبات از این داشبورد هنوز فعال نیست؛ این بخش فقط‌خواندنی است. "
    "در فاز یکپارچه‌سازی با دسترسی نوشتن روی همان جدول‌ها روشن می‌شود."
)


class WritesDisabled(RuntimeError):
    """Raised instead of attempting a write the deployment has not enabled."""


class AloBotWrites:
    def __init__(self, engine) -> None:
        self.engine = engine
        self._sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False) if engine else None

    @property
    def available(self) -> bool:
        return self._sessions is not None and get_settings().alobot_db_writes_enabled

    def session(self) -> AsyncSession:
        if not self.available:
            raise WritesDisabled(MESSAGE_DISABLED)
        return self._sessions()


writes = AloBotWrites(alobot_write_engine)


@asynccontextmanager
async def edit(db: AsyncSession, actor, *, action: str, entity_type: str, entity_id: str, before: Any = None) -> AsyncIterator[AsyncSession]:
    """An AloBot edit and its audit row in ours.

    Usage::

        async with edit(db, actor, action="catalog.bind", entity_type="service", entity_id="5") as a:
            await a.execute(...)
            a.audit_after = {"price": 1000}

    The AloBot write commits first; the audit row follows. A failure to
    record the audit is reported, never swallowed.
    """
    session = writes.session()
    try:
        session.audit_after = None  # type: ignore[attr-defined]
        yield session
        await session.commit()
    finally:
        after = getattr(session, "audit_after", None)
        await session.close()
    try:
        db.add(audit_row(action=action, entity_type=entity_type, entity_id=entity_id, actor_role=actor.role, actor_operator_id=actor.id, before=before, after=after))
        await db.commit()
    except Exception:  # noqa: BLE001 - the edit is already committed; say so loudly
        await db.rollback()
        log.exception("audit.unrecorded", action=action, entity_type=entity_type, entity_id=entity_id, actor=actor.email)
