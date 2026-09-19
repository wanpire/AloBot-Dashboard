"""Phase 1 task 1: the own-database tables and the guarantees they carry."""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.conftest import alembic


def test_migrations_round_trip_cleanly():
    assert alembic("downgrade", "base").returncode == 0
    up = alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr
    assert alembic("check").returncode == 0, "models and migrations have drifted apart"


async def test_every_phase1_table_exists(session):
    rows = await session.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))
    names = {r[0] for r in rows}
    for table in ("operators", "operator_sessions", "settings", "audit_logs", "app_events", "bot_notifications"):
        assert table in names, table


async def test_audit_logs_cannot_be_updated_or_deleted(session):
    from app.models import AuditLog

    session.add(AuditLog(actor_role="SYSTEM", action="test.write", entity_type="test", entity_id="1"))
    await session.commit()
    with pytest.raises(DBAPIError, match="append-only"):
        await session.execute(text("UPDATE audit_logs SET action='changed'"))
    await session.rollback()
    with pytest.raises(DBAPIError, match="append-only"):
        await session.execute(text("DELETE FROM audit_logs"))
    await session.rollback()


async def test_outbox_dedupe_key_is_unique_even_after_send(session):
    from app.models import BotNotification

    session.add(BotNotification(dedupe_key="settle:1", chat_id=1, payload={"text": "a"}, status="SENT"))
    await session.commit()
    session.add(BotNotification(dedupe_key="settle:1", chat_id=1, payload={"text": "b"}))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_outbox_status_is_a_closed_set(session):
    from app.models import BotNotification

    session.add(BotNotification(dedupe_key="x", chat_id=1, payload={}, status="MAYBE"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_operator_email_is_unique_and_role_is_closed(session):
    from app.models import Operator

    session.add(Operator(email="a@x.io", display_name="A", password_hash="h", role="ADMIN"))
    await session.commit()
    session.add(Operator(email="a@x.io", display_name="B", password_hash="h", role="ADMIN"))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()
    session.add(Operator(email="b@x.io", display_name="B", password_hash="h", role="GOD"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_settings_are_keyed_by_scope_and_key(session):
    from app.models import Setting

    session.add(Setting(scope="general", key="shop_name", value="Alo"))
    session.add(Setting(scope="other", key="shop_name", value="Other"))
    await session.commit()
    rows = (await session.execute(select(Setting).order_by(Setting.scope))).scalars().all()
    assert [(r.scope, r.key, r.value) for r in rows] == [("general", "shop_name", "Alo"), ("other", "shop_name", "Other")]
    with pytest.raises(IntegrityError):
        await session.execute(text("INSERT INTO settings (scope, key, value) VALUES ('general','shop_name','\"dup\"'::jsonb)"))
