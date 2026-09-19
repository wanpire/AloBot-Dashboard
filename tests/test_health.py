"""Needs a reachable throwaway Postgres at TEST_DATABASE_URL (CLAUDE.md > Testing)."""

import httpx

from app.main import app


async def test_health_reports_own_db_and_unconfigured_alobot():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    body = response.json()
    assert response.status_code == 200, body
    assert body["ok"] is True
    assert body["env"] == "test"
    assert body["db"] == "ok"
    assert body["alobot_db"] == "unconfigured"
    assert body["alobot_db_writes_enabled"] is False
