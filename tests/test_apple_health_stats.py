from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest


class StatsPool:
    def __init__(self, *, apple_rows=None, whoop_user=None):
        self.apple_rows = apple_rows or []
        self.whoop_user = whoop_user

    async def fetchrow(self, query, *args):
        if "fatsecret_access_token" in query:
            return {"fatsecret_access_token": None, "fatsecret_access_secret": None}
        if "whoop_access_token" in query:
            return self.whoop_user
        raise AssertionError(f"Unexpected fetchrow query: {query}")

    async def fetch(self, query, *args):
        if "FROM health_data" in query:
            return self.apple_rows
        raise AssertionError(f"Unexpected fetch query: {query}")


def apple_row(metric_type, value, unit, recorded_at, duration_seconds=None):
    return {
        "metric_type": metric_type,
        "value": value,
        "unit": unit,
        "recorded_at": recorded_at,
        "duration_seconds": duration_seconds,
    }


@pytest.mark.asyncio
async def test_get_today_stats_uses_apple_health_only_data(mock_settings):
    from app.services.ai_assistant import get_today_stats

    pool = StatsPool(
        apple_rows=[
            apple_row("step_count", 4200, "count", datetime(2026, 7, 10, 8, tzinfo=timezone.utc)),
            apple_row("active_energy", 315, "kcal", datetime(2026, 7, 10, 9, tzinfo=timezone.utc)),
            apple_row("heart_rate", 62, "bpm", datetime(2026, 7, 10, 10, tzinfo=timezone.utc)),
            apple_row("sleep_analysis", 1, "count", datetime(2026, 7, 10, 6, tzinfo=timezone.utc), 25200),
        ],
    )

    with patch("app.services.ai_assistant.get_pool", AsyncMock(return_value=pool)):
        stats = await get_today_stats(7)

    assert stats["today_calories_out"] == 315
    assert stats["calories_burned_source"] == "apple_health"
    assert stats["apple_health_steps"] == 4200
    assert stats["apple_health_avg_heart_rate"] == 62
    assert stats["apple_health_sleep_hours"] == 7.0
    assert "Apple Health steps: 4200" in stats["apple_health_summary"]


@pytest.mark.asyncio
async def test_get_today_stats_prefers_whoop_burn_when_mixed_with_apple_health(mock_settings):
    from app.services.ai_assistant import get_today_stats

    pool = StatsPool(
        whoop_user={
            "id": 7,
            "whoop_access_token": "access",
            "whoop_refresh_token": "refresh",
            "whoop_token_expires_at": datetime(2026, 7, 10, tzinfo=timezone.utc),
        },
        apple_rows=[
            apple_row("Step Count", 8000, "count", datetime(2026, 7, 10, 8, tzinfo=timezone.utc)),
            apple_row("Active Energy Burned", 250, "kcal", datetime(2026, 7, 10, 9, tzinfo=timezone.utc)),
        ],
    )
    whoop_context = {
        "calories_out": 700,
        "strain": 9.2,
        "workout_count": 1,
        "cycle_score_state": "SCORED",
        "sleep_info": "WHOOP sleep: 7.5h",
        "recovery_info": "WHOOP recovery: 75%",
        "activities_info": "",
        "body_info": "",
    }

    with (
        patch("app.services.ai_assistant.get_pool", AsyncMock(return_value=pool)),
        patch("app.services.whoop_sync.refresh_token_if_needed", AsyncMock(return_value="token")),
        patch("app.services.whoop_sync.fetch_whoop_context", AsyncMock(return_value=whoop_context)),
    ):
        stats = await get_today_stats(7)

    assert stats["today_calories_out"] == 700
    assert stats["calories_burned_source"] == "whoop"
    assert stats["today_strain"] == 9.2
    assert stats["apple_health_active_energy_kcal"] == 250
    assert stats["apple_health_steps"] == 8000


@pytest.mark.asyncio
async def test_morning_briefing_includes_apple_health_summary(mock_settings):
    from app.services import ai_assistant
    from app.services import briefings

    captured = {}

    async def fake_generate(prompt, data_summary):
        captured["data_summary"] = data_summary
        return "briefing"

    with (
        patch.object(
            briefings,
            "_get_users_with_telegram",
            AsyncMock(return_value=[{
                "id": 7,
                "telegram_user_id": 999,
                "daily_calorie_goal": 2000,
                "language": "en",
            }]),
        ),
        patch.object(
            ai_assistant,
            "get_today_stats",
            AsyncMock(return_value={
                "today_calories_in": 0,
                "today_calories_out": 315,
                "whoop_sleep": "",
                "whoop_recovery": "",
                "apple_health_summary": "Apple Health steps: 4200. Apple Health active energy: 315 kcal",
            }),
        ),
        patch.object(briefings, "_generate_briefing", fake_generate),
        patch.object(briefings, "_send_telegram_message", AsyncMock()),
        patch.object(briefings.settings, "telegram_bot_token", "telegram-token"),
        patch.object(briefings.settings, "openai_api_key", "openai-token"),
    ):
        await briefings.morning_briefing()

    assert "Apple Health steps: 4200" in captured["data_summary"]
    assert "Apple Health active energy: 315 kcal" in captured["data_summary"]
