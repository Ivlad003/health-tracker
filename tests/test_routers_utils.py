import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport

from app.main import app


@pytest.mark.asyncio
async def test_ip_check(mock_settings, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_token", "admin-secret")
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
            resp = await client.get(
                "/ip-check", headers={"Authorization": "Bearer admin-secret"},
            )

    assert resp.status_code == 200
    assert resp.json()["ip"] == "84.54.23.99"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/debug/stats?telegram_user_id=1",
        "/debug/whoop-token?telegram_user_id=1",
        "/debug/whoop-raw?telegram_user_id=1",
        "/fatsecret/diary?user_id=1",
        "/food/search?q=egg",
        "/ip-check",
    ],
)
async def test_operator_endpoints_hidden_without_admin_token(mock_settings, monkeypatch, path):
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_token", "")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(path)
    assert resp.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("header", [{}, {"Authorization": "Bearer wrong"}, {"X-Admin-Token": "nope"}])
async def test_operator_endpoints_require_valid_admin_token(mock_settings, monkeypatch, header):
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_token", "admin-secret")
    with patch("app.database.get_pool") as get_pool:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/debug/stats?telegram_user_id=1", headers=header)
    assert resp.status_code == 401
    get_pool.assert_not_called()


def test_redactor_does_not_break_code_percent_s_format():
    import logging

    from app.main import SecretRedactingFilter

    record = logging.LogRecord(
        name="test", level=logging.ERROR, pathname=__file__, lineno=1,
        msg="FatSecret %s error: code=%s message=%s",
        args=("food.get", 106, "Invalid ID: please check your food_id"),
        exc_info=None,
    )
    assert SecretRedactingFilter().filter(record) is True
    assert record.getMessage() == (
        "FatSecret food.get error: code=106 message=Invalid ID: please check your food_id"
    )


def test_redact_secrets_masks_apple_health_token_and_oauth_codes():
    from app.main import redact_secrets

    line = (
        '"POST /api/v1/health/apple-health/sync?userId=1&token=abcDEF123 HTTP/1.1" '
        "GET /whoop/callback?code=xyz&state=1.2.3"
    )
    redacted = redact_secrets(line)
    assert "abcDEF123" not in redacted
    assert "code=***" in redacted and "state=***" in redacted
    assert "userId=1" in redacted
