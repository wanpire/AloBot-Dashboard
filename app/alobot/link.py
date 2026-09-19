"""The read-only window onto AloBot's database, as one object the app boots.

`connect()` reflects the tables this project reads and runs the compatibility
check. `available` is the one question every AloBot-backed page asks; when
it is False, `problems` says why in sentences an operator can act on.
"""

from __future__ import annotations

from sqlalchemy import MetaData, Table
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.alobot import compat
from app.core.logging import get_logger
from app.db.session import alobot_engine

log = get_logger(__name__)


class AloBotLink:
    def __init__(self, engine: AsyncEngine | None) -> None:
        self.engine = engine
        self.metadata = MetaData()
        self.tables: dict[str, Table] = {}
        self.problems: list[str] = []
        self.connected = False
        self._sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False) if engine else None

    @property
    def available(self) -> bool:
        return self.connected and not self.problems

    async def connect(self) -> None:
        self.problems = []
        self.connected = False
        if self.engine is None:
            self.problems = ["ALOBOT_DATABASE_URL is blank - AloBot-backed pages are disabled"]
            return
        try:
            self.metadata = MetaData()
            async with self.engine.connect() as conn:
                await conn.run_sync(self.metadata.reflect, only=lambda name, _: name in compat.REQUIRED_COLUMNS)
        except Exception as exc:  # noqa: BLE001 - a broken link is a reported state, not a crash
            self.problems = [f"could not reach AloBot's database: {type(exc).__name__}"]
            log.warning("alobot.link_failed", error=type(exc).__name__)
            return
        self.tables = dict(self.metadata.tables)
        self.problems = compat.check(self.metadata)
        self.connected = True
        if self.problems:
            log.warning("alobot.incompatible", problems=self.problems)
        else:
            log.info("alobot.linked", tables=sorted(self.tables))

    def session(self) -> AsyncSession:
        if self._sessions is None:
            raise RuntimeError("AloBot link is not configured")
        return self._sessions()

    def t(self, name: str) -> Table:
        return self.tables[name]


link = AloBotLink(alobot_engine)
