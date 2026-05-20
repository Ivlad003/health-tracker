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
