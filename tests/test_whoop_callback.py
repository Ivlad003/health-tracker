import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport

import time

from app.main import app
from app.security import sign_oauth_state


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
                "/whoop/callback",
                params={"code": "auth_code", "state": sign_oauth_state(999, "whoop")},
            )

    assert resp.status_code == 200
    assert "WHOOP Connected" in resp.text
    assert mock_pool.execute.await_args[0][-1] == 999


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state_factory",
    [
        lambda: "999",  # legacy unsigned Telegram id
        lambda: sign_oauth_state(999, "fatsecret"),  # wrong purpose
        lambda: sign_oauth_state(999, "whoop", now=int(time.time()) - 7200),  # expired
        lambda: sign_oauth_state(999, "whoop")[:-2] + "xx",  # tampered MAC
    ],
)
async def test_whoop_callback_rejects_untrusted_state(mock_settings, state_factory):
    with patch("app.routers.whoop.httpx.AsyncClient") as mock_cls:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/whoop/callback", params={"code": "c", "state": state_factory()},
            )

    assert resp.status_code == 400
    mock_cls.assert_not_called()


@pytest.mark.asyncio
async def test_whoop_callback_succeeds_for_member_without_recovery(mock_settings):
    token_resp = MagicMock(status_code=200)
    token_resp.json.return_value = {"access_token": "a", "refresh_token": "r", "expires_in": 3600}
    empty = MagicMock(status_code=200)
    empty.json.return_value = {"records": []}
    sleep = MagicMock(status_code=200)
    sleep.json.return_value = {"records": [{"user_id": 77}]}
    mock_pool = AsyncMock()

    with (
        patch("app.routers.whoop.httpx.AsyncClient") as mock_cls,
        patch("app.routers.whoop.get_pool", return_value=mock_pool),
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=token_resp)
        mock_client.get = AsyncMock(side_effect=[empty, sleep])
        mock_cls.return_value = mock_client

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/whoop/callback",
                params={"code": "c", "state": sign_oauth_state(5, "whoop")},
            )

    assert resp.status_code == 200
    assert mock_pool.execute.await_args[0][4] == "77"
