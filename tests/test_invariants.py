"""Phase 1 task 11: the guarantees are checked by the database itself, in a
transaction that rolls back, on every test run."""

from pathlib import Path

import pytest
from sqlalchemy import text

import asyncpg

from app.db.session import engine
from tests.conftest import TEST_DATABASE_URL

SCRIPT = Path(__file__).parent.parent / "scripts" / "verify_invariants.sql"


async def _run_script() -> list[str]:
    """The script manages its own BEGIN/ROLLBACK, so it gets a connection of
    its own rather than one the SQLAlchemy pool thinks it controls."""
    notices: list[str] = []
    conn = await asyncpg.connect(TEST_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        conn.add_log_listener(lambda _c, msg: notices.append(msg.message))
        await conn.execute(SCRIPT.read_text())
    finally:
        await conn.close()
    return notices


async def test_invariants_pass_on_the_migrated_schema():
    notices = await _run_script()
    passes = [n for n in notices if n.startswith("PASS")]
    assert len(passes) >= 5, notices
    assert not [n for n in notices if n.startswith("FAIL")]


async def test_invariants_fail_when_the_audit_trigger_is_missing():
    async with engine.begin() as conn:
        await conn.execute(text("DROP TRIGGER trg_audit_logs_append_only ON audit_logs"))
    try:
        with pytest.raises(Exception, match="FAIL audit_logs"):
            await _run_script()
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TRIGGER trg_audit_logs_append_only BEFORE UPDATE OR DELETE ON audit_logs "
                    "FOR EACH ROW EXECUTE FUNCTION audit_logs_append_only()"
                )
            )


async def test_invariants_leave_no_rows_behind(session):
    await _run_script()
    for table in ("audit_logs", "bot_notifications", "operators"):
        count = await session.scalar(text(f"SELECT COUNT(*) FROM {table}"))
        assert count == 0, table
