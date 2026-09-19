"""Needs a reachable throwaway Postgres at TEST_DATABASE_URL (CLAUDE.md > Testing)."""

import httpx

from app.alobot.link import link
from app.main import app


async def _health():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/health")


async def test_health_reports_own_db_and_a_compatible_alobot_link():
    await link.connect()
    response = await _health()
    body = response.json()
    assert response.status_code == 200, body
    assert body["ok"] is True
    assert body["env"] == "test"
    assert body["db"] == "ok"
    assert body["alobot_db"] == "ok"
    assert body["alobot_db_writes_enabled"] is False


async def test_health_names_an_incompatible_alobot_schema(monkeypatch):
    await link.connect()
    monkeypatch.setattr(link, "problems", ["AloBot column payments.amount is missing"])
    body = (await _health()).json()
    assert body["alobot_db"].startswith("incompatible: AloBot column payments.amount")
    assert body["ok"] is True  # the dashboard's own half is healthy; the link is reported, not fatal
