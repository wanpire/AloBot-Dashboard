"""Phase 6 task 2: the connection arithmetic, held against AloBot's own files.

This project shares a Postgres server with a bot that is processing real
purchases. Exhausting that server's connections would not slow the dashboard
down, it would stop AloBot from selling. The numbers the budget is built on
are AloBot's, so they are read out of `vendor/alobot` rather than trusted to
a comment that was true once.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from app.db import session as db
from app.db.session import (
    ALOBOT_CONNECTION_BUDGET,
    ALOBOT_MAX_CONNECTIONS,
    ALOBOT_OWN_MAX_CONNECTIONS,
    alobot_connections_at_worst,
    check_connection_budget,
)

VENDOR = pathlib.Path(__file__).resolve().parent.parent / "vendor" / "alobot"


def test_alobots_postgres_still_allows_the_ceiling_this_budget_assumes():
    compose = (VENDOR / "docker-compose.yml").read_text()
    found = re.search(r"max_connections=(\d+)", compose)
    assert found, "AloBot's compose no longer pins max_connections"
    assert int(found.group(1)) == ALOBOT_MAX_CONNECTIONS


def test_alobots_own_pool_still_takes_what_this_budget_assumes():
    source = (VENDOR / "app" / "db" / "session.py").read_text()
    size = re.search(r"pool_size=(\d+)", source)
    overflow = re.search(r"max_overflow=(\d+)", source)
    assert size and overflow, "AloBot's engine no longer states its pool"
    assert int(size.group(1)) + int(overflow.group(1)) == ALOBOT_OWN_MAX_CONNECTIONS


def test_this_project_stays_inside_its_slice_of_the_headroom():
    worst = alobot_connections_at_worst()
    assert worst <= ALOBOT_CONNECTION_BUDGET
    # And the budget itself fits in what AloBot leaves unclaimed, with room
    # left for migrations, psql and monitoring.
    headroom = ALOBOT_MAX_CONNECTIONS - ALOBOT_OWN_MAX_CONNECTIONS
    assert ALOBOT_CONNECTION_BUDGET <= headroom // 2


def test_raising_a_pool_past_the_budget_is_refused_at_import_time(monkeypatch):
    """The guard is only worth having if it actually fires."""

    class Fat:
        class pool:
            @staticmethod
            def size() -> int:
                return 100

            _max_overflow = 100

    monkeypatch.setattr(db, "alobot_engine", Fat)
    monkeypatch.setattr(db, "alobot_write_engine", None)
    with pytest.raises(RuntimeError, match="budget"):
        check_connection_budget()


def test_the_own_database_pool_is_the_one_that_may_be_generous():
    """Our own Postgres is ours alone: ingest bursts may use it freely. The
    point of the budget is the shared server, not this one."""
    own = db.engine.pool.size() + db.engine.pool._max_overflow
    assert own >= alobot_connections_at_worst()
