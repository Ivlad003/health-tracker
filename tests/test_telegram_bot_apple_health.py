from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_connect_apple_health_reply_includes_telegram_user_id(mock_settings):
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

    reply = message.reply_text.call_args.args[0]
    assert "userId=123456789" in reply


@pytest.mark.asyncio
async def test_connect_apple_health_reply_does_not_prompt_userid_in_body(mock_settings):
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

    reply = message.reply_text.call_args.args[0]
    # The "userId: <id>" standalone line confused users into adding userId
    # as a Request Body field. URL already carries it as a query param.
    assert "userId: 123456789" not in reply
    assert "Request Body" in reply or "тіло" in reply


@pytest.mark.asyncio
async def test_connect_apple_health_reply_includes_prefilled_sync_url(mock_settings):
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

    reply = message.reply_text.call_args.args[0]
    assert (
        "/api/v1/health/apple-health/sync"
        "?userId=123456789&token=token-123"
    ) in reply


@pytest.mark.asyncio
async def test_connect_apple_health_reply_separates_supported_setup_paths(mock_settings):
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

    reply = message.reply_text.call_args.args[0]
    assert "Без встановлення додатків" in reply
    assert "iOS Shortcuts" in reply
    assert "Health Auto Export" in reply
    assert "з встановленням додатку" in reply
    assert "Output: JSON (REST API)" in reply


@pytest.mark.asyncio
async def test_connect_apple_health_reply_explains_reconnect_rotates_old_url(mock_settings):
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

    reply = message.reply_text.call_args.args[0]
    assert "/connect_apple_health" in reply
    assert "старий URL перестане працювати" in reply


@pytest.mark.asyncio
async def test_apple_health_help_reply_explains_shortcuts_setup_in_ukrainian(mock_settings):
    from app.services.telegram_bot import handle_apple_health_help

    message = AsyncMock()
    update = SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=123456789, username="tester"),
    )

    await handle_apple_health_help(update, None)

    reply = message.reply_text.call_args.args[0]
    assert "Як підключити Apple Health" in reply
    assert "Apple Shortcuts" in reply
    assert "нічого додатково встановлювати не треба" in reply
    assert "/connect_apple_health" in reply
    assert "Get Health Samples" in reply
    assert "Get Contents of URL" in reply
    assert "Method: POST" in reply
    assert "Content-Type = application/json" in reply
    assert "/sync" in reply


@pytest.mark.asyncio
async def test_apple_health_help_keeps_token_and_user_id_out_of_body(mock_settings):
    from app.services.telegram_bot import handle_apple_health_help

    message = AsyncMock()
    update = SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=123456789, username="tester"),
    )

    await handle_apple_health_help(update, None)

    reply = message.reply_text.call_args.args[0]
    assert "userId і token не додавай у Body" in reply
    assert "userId =" not in reply
    assert "token =" not in reply
