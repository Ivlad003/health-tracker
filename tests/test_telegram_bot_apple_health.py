from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


async def _connect_apple_health_reply():
    from app.services.telegram_bot import handle_connect_apple_health

    message = AsyncMock()
    update = SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=123456789, username="tester"),
    )

    with (
        patch(
            "app.services.telegram_bot._ensure_user",
            AsyncMock(return_value={"id": 7, "daily_calorie_goal": 2000}),
        ),
        patch("app.services.telegram_bot.get_pool", AsyncMock(return_value=object())),
        patch(
            "app.services.telegram_bot.ensure_apple_health_sync",
            AsyncMock(return_value={"secret_key": "token-123"}),
        ),
    ):
        await handle_connect_apple_health(update, None)

    return message.reply_text.call_args.args[0]


async def _apple_health_help_reply():
    from app.services.telegram_bot import handle_apple_health_help

    message = AsyncMock()
    update = SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=123456789, username="tester"),
    )

    await handle_apple_health_help(update, None)

    return message.reply_text.call_args.args[0]


@pytest.mark.asyncio
async def test_connect_apple_health_reply_includes_telegram_user_id(mock_settings):
    reply = await _connect_apple_health_reply()
    assert "userId=123456789" in reply


@pytest.mark.asyncio
async def test_connect_apple_health_reply_does_not_prompt_userid_in_body(mock_settings):
    reply = await _connect_apple_health_reply()
    # The "userId: <id>" standalone line confused users into adding userId
    # as a Request Body field. URL already carries it as a query param.
    assert "userId: 123456789" not in reply
    assert "Request Body" in reply or "тіло" in reply


@pytest.mark.asyncio
async def test_connect_apple_health_reply_includes_prefilled_sync_url(mock_settings):
    reply = await _connect_apple_health_reply()
    assert (
        "/api/v1/health/apple-health/sync"
        "?userId=123456789&token=token-123"
    ) in reply


@pytest.mark.asyncio
async def test_connect_apple_health_reply_links_served_shortcut_artifact(mock_settings):
    reply = await _connect_apple_health_reply()
    assert "http://localhost:8000/api/v1/health/apple-health/shortcut" in reply
    assert "APPLE_HEALTH_SHORTCUT_IMPORT_URL" not in reply


@pytest.mark.asyncio
async def test_connect_apple_health_reply_prioritizes_ready_shortcut_import(mock_settings):
    reply = await _connect_apple_health_reply()
    assert "Готовий Shortcut" in reply
    assert "Import Question" in reply
    assert "Health Tracker Apple Health Sync" in reply
    assert "Get Health Samples → Get Contents of URL" not in reply


@pytest.mark.asyncio
async def test_connect_apple_health_reply_warns_that_health_samples_do_not_run_on_mac(
    mock_settings,
):
    reply = await _connect_apple_health_reply()
    assert "саме на iPhone або iPad" in reply
    assert "На Mac" in reply
    assert "Find Health Samples" in reply


@pytest.mark.asyncio
async def test_connect_apple_health_reply_separates_supported_setup_paths(mock_settings):
    reply = await _connect_apple_health_reply()
    assert "Готовий Shortcut" in reply
    assert "iOS Shortcuts" in reply
    assert "Health Auto Export" in reply
    assert "з встановленням додатку" in reply
    assert "Output: JSON (REST API)" in reply


@pytest.mark.asyncio
async def test_connect_apple_health_reply_explains_reconnect_rotates_old_url(mock_settings):
    reply = await _connect_apple_health_reply()
    assert "/connect_apple_health" in reply
    assert "старий URL перестане працювати" in reply


@pytest.mark.asyncio
async def test_apple_health_help_reply_explains_shortcuts_setup_in_ukrainian(mock_settings):
    reply = await _apple_health_help_reply()
    assert "Як підключити Apple Health" in reply
    assert "Apple Shortcuts" in reply
    assert "нічого додатково встановлювати не треба" in reply
    assert "/connect_apple_health" in reply
    assert "готовий Shortcut" in reply
    assert "ручний варіант" in reply
    assert "Get Health Samples" not in reply
    assert "/sync" in reply


@pytest.mark.asyncio
async def test_apple_health_help_warns_that_shortcut_must_run_on_iphone_or_ipad(
    mock_settings,
):
    reply = await _apple_health_help_reply()
    assert "саме на iPhone або iPad" in reply
    assert "На Mac" in reply
    assert "Find Health Samples" in reply


@pytest.mark.asyncio
async def test_apple_health_help_keeps_token_and_user_id_out_of_body(mock_settings):
    reply = await _apple_health_help_reply()
    assert "userId і token не додавай у Body" in reply
    assert "userId =" not in reply
    assert "token =" not in reply
