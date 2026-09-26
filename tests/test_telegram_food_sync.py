"""Telegram glue for food flows (the ledger itself is covered by
tests/test_food_ledger_db.py)."""
from unittest.mock import AsyncMock, patch

import pytest


def test_quantity_only_messages(mock_settings):
    from app.services.telegram_bot import _QUANTITY_ONLY_RE

    for text in ("135 г", "135г", "0,2 кг", "180 grams", " 50 g. "):
        assert _QUANTITY_ONLY_RE.match(text), text
    for text in ("135", "гречка 180 г", "135 ml", "як справи"):
        assert not _QUANTITY_ONLY_RE.match(text), text


def test_reply_markup_only_offers_https_web_app(mock_settings):
    from app.services.food_bot import BotReply
    from app.services.telegram_bot import _reply_markup

    markup = _reply_markup(BotReply("x", buttons=[[("↩️", "fe:u:1:1")]], webapp_url="https://a.test/app/#/x"), "uk")
    buttons = [b for row in markup.inline_keyboard for b in row]
    assert buttons[0].callback_data == "fe:u:1:1"
    assert buttons[1].web_app.url == "https://a.test/app/#/x"
    assert _reply_markup(BotReply("x"), "uk") is None


def test_webapp_link_requires_https(mock_settings):
    from app.config import settings
    from app.services.food_bot import webapp_link

    with patch.object(settings, "webapp_url", ""), patch.object(settings, "app_base_url", "http://localhost:8000"):
        assert webapp_link() is None
    with patch.object(settings, "webapp_url", "https://bot.test/app/"):
        assert webapp_link("/diary/2026-09-26") == "https://bot.test/app/#/diary/2026-09-26"


@pytest.mark.asyncio
async def test_undo_last_triggers_remote_delete(mock_settings):
    from app.services import food_bot

    entry = {"id": 5, "food_name": "Гречка", "calories": 198, "sync_status": "delete_pending"}
    with (
        patch.object(food_bot.ledger, "undo_last", AsyncMock(return_value=entry)),
        patch.object(food_bot, "_sync_delete", AsyncMock()) as sync_delete,
    ):
        text = await food_bot.undo_last(object(), object())
    assert text == "Гречка (198 kcal)"
    sync_delete.assert_awaited_once()
