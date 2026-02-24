import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport

from app.main import app


@pytest.mark.asyncio
async def test_fatsecret_connect_redirects(mock_settings):
    mock_pool = AsyncMock()
    mock_pool.execute = AsyncMock()

    with (
        patch("app.routers.fatsecret.get_pool", return_value=mock_pool),
        patch("app.routers.fatsecret.get_request_token", return_value={
            "oauth_token": "req_token_123",
            "oauth_token_secret": "req_secret_456",
        }),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test", follow_redirects=False
        ) as client:
            resp = await client.get(
                "/fatsecret/connect", params={"state": "999"}
            )

    assert resp.status_code == 307
    assert "oauth_token=req_token_123" in resp.headers["location"]


@pytest.mark.asyncio
async def test_fatsecret_callback_success(mock_settings):
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value={
        "id": 1,
        "request_secret": "stored_secret",
    })
    mock_pool.execute = AsyncMock()

    with (
        patch("app.routers.fatsecret.get_pool", return_value=mock_pool),
        patch("app.routers.fatsecret.exchange_access_token", return_value={
            "access_token": "final_token",
            "access_secret": "final_secret",
        }),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/fatsecret/callback", params={
                "oauth_token": "req_token",
                "oauth_verifier": "verifier_123",
                "state": "999",
            })

    assert resp.status_code == 200
    assert "FatSecret Connected" in resp.text
