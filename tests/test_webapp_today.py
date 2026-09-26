"""Web App today summary is session-gated and returns the shared stats payload."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


class _Session:
    user_id = 7
    transport = "bearer"


@pytest.mark.asyncio
async def test_today_requires_a_session(monkeypatch):
    async def fake_pool():
        return object()

    async def fake_resolve(_pool, _token):
        return None

    monkeypatch.setattr("app.routers.webapp.get_pool", fake_pool)
    monkeypatch.setattr("app.services.webapp_auth.resolve_session", fake_resolve)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/webapp/today")
    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "session_invalid"


@pytest.mark.asyncio
async def test_today_returns_shared_stats(monkeypatch):
    async def fake_pool():
        return object()

    async def fake_resolve(_pool, _token):
        return _Session()

    async def fake_stats(user_id):
        assert user_id == 7
        return {"today_calories_in": 1200, "today_calories_out": 400, "timezone": "Europe/Kyiv"}

    monkeypatch.setattr("app.routers.webapp.get_pool", fake_pool)
    monkeypatch.setattr("app.services.webapp_auth.resolve_session", fake_resolve)
    monkeypatch.setattr("app.services.ai_assistant.get_today_stats", fake_stats)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/webapp/today", headers={"Authorization": "Bearer session-token"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["today_calories_in"] == 1200
    assert body["timezone"] == "Europe/Kyiv"
