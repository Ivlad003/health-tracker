import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport

from app.main import app


@pytest.mark.asyncio
async def test_ip_check(mock_settings):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"ip": "84.54.23.99"}
    mock_response.raise_for_status = MagicMock()

    with patch("app.routers.utils.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/ip-check")

    assert resp.status_code == 200
    assert resp.json()["ip"] == "84.54.23.99"
