"""Phase 1 task 10: the loop that does things while nobody is looking,
and the health check that can tell when it stopped."""

import asyncio
import os
import time

import httpx

from app.core.config import get_settings
from app.main import app
from app.services.sweeps import SweepRegistry, heartbeat_age, run_cycle, touch_heartbeat


async def test_a_failing_sweep_does_not_stop_the_others(tmp_path):
    ran = []
    registry = SweepRegistry()

    async def a(session):
        ran.append("a")
        return 1

    async def boom(session):
        raise RuntimeError("boom")

    async def c(session):
        ran.append("c")
        return 3

    registry.register("a", a)
    registry.register("boom", boom)
    registry.register("c", c)
    results = await run_cycle(registry, heartbeat_path=str(tmp_path / "hb"))
    assert ran == ["a", "c"]
    assert results["a"] == 1 and results["c"] == 3
    assert isinstance(results["boom"], RuntimeError)


async def test_heartbeat_is_touched_only_after_a_full_cycle(tmp_path):
    hb = tmp_path / "hb"
    registry = SweepRegistry()
    assert heartbeat_age(str(hb)) is None
    await run_cycle(registry, heartbeat_path=str(hb))
    assert heartbeat_age(str(hb)) is not None and heartbeat_age(str(hb)) < 5


async def test_health_goes_503_when_sweeps_are_expected_but_the_heartbeat_is_stale(tmp_path, monkeypatch):
    settings = get_settings()
    hb = tmp_path / "hb"
    monkeypatch.setattr(settings, "run_sweeps", True)
    monkeypatch.setattr(settings, "heartbeat_path", str(hb))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        missing = await c.get("/health")
        touch_heartbeat(str(hb))
        fresh = await c.get("/health")
        os.utime(hb, (time.time() - 600, time.time() - 600))
        stale = await c.get("/health")
    assert missing.status_code == 503 and missing.json()["sweeps"]["heartbeat_age_seconds"] is None
    assert fresh.status_code == 200 and fresh.json()["sweeps"]["heartbeat_age_seconds"] < 5
    assert stale.status_code == 503


async def test_health_ignores_the_heartbeat_when_sweeps_are_off(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "run_sweeps", False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/health")
    assert r.status_code == 200 and r.json()["sweeps"]["enabled"] is False


async def test_phase1_sweeps_are_registered():
    from app.services.sweeps import registry

    assert {"events.flush", "events.prune", "sessions.prune"} <= set(registry.names())
