import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport

from app.main import app


@pytest.mark.asyncio
async def test_whoop_callback_missing_code(mock_settings):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/whoop/callback")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_whoop_callback_success(mock_settings):
    token_resp = MagicMock()
    token_resp.status_code = 200
    token_resp.json.return_value = {
        "access_token": "whoop_access",
        "refresh_token": "whoop_refresh",
        "expires_in": 3600,
    }
    token_resp.raise_for_status = MagicMock()

    recovery_resp = MagicMock()
    recovery_resp.status_code = 200
    recovery_resp.json.return_value = {"records": [{"user_id": 12345}]}
    recovery_resp.raise_for_status = MagicMock()

    mock_pool = AsyncMock()
    mock_pool.execute = AsyncMock(return_value="UPDATE 1")

    with (
        patch("app.routers.whoop.httpx.AsyncClient") as mock_cls,
        patch("app.routers.whoop.get_pool", return_value=mock_pool),
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=token_resp)
        mock_client.get = AsyncMock(return_value=recovery_resp)
        mock_cls.return_value = mock_client

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test", follow_redirects=False
        ) as client:
            resp = await client.get(
                "/whoop/callback", params={"code": "auth_code", "state": "999"}
            )

    assert resp.status_code == 200
    assert "WHOOP Connected" in resp.text
