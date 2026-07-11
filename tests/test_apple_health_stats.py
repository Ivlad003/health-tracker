from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

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


class SummaryPool:
    def __init__(self, rows):
        self.rows = rows
        self.fetch_args = None

    async def fetch(self, query, *args):
        self.fetch_args = args
        return self.rows


def apple_row(metric_type, value, unit, recorded_at, duration_seconds=None):
    return {
        "metric_type": metric_type,
        "value": value,
        "unit": unit,
        "recorded_at": recorded_at,
        "duration_seconds": duration_seconds,
    }


def _kyiv_today_start_utc() -> datetime:
    """Same "today" window start that get_today_stats computes at runtime."""
    now_kyiv = datetime.now(ZoneInfo("Europe/Kyiv"))
    today_start = now_kyiv.replace(hour=0, minute=0, second=0, microsecond=0)
    return today_start.astimezone(timezone.utc)


@pytest.mark.asyncio
async def test_get_apple_health_summary_merges_overlapping_sleep_and_attributes_by_end(mock_settings):
    from app.services.apple_health import get_apple_health_summary

    start_at = datetime(2026, 7, 10, 21, 0, tzinfo=timezone.utc)  # 00:00 Kyiv
    end_at = start_at + timedelta(days=1)
    rows = [
        # In Bed envelope: starts yesterday 23:00 Kyiv, ends 07:10 today.
        apple_row(
            "sleep_analysis", 0, "s",
            start_at - timedelta(hours=1), duration_seconds=29400,
        ),
        # Core stage inside the envelope must not double the night.
        apple_row(
            "sleep_analysis", 0, "s",
            start_at - timedelta(minutes=50), duration_seconds=25000,
        ),
        # Nap that ended before today's window belongs to yesterday.
        apple_row(
            "sleep_analysis", 0, "s",
            start_at - timedelta(hours=7), duration_seconds=3600,
        ),
        # Yesterday's steps come back from the widened fetch window but must
        # not leak into today's totals.
        apple_row("step_count", 900, "count", start_at - timedelta(hours=2)),
        apple_row("step_count", 4100, "count", start_at + timedelta(hours=9)),
        apple_row("heart_rate_variability_sdnn", 52, "ms", start_at + timedelta(hours=7)),
        apple_row("heart_rate_variability_sdnn", 44, "ms", start_at + timedelta(hours=8)),
    ]
    pool = SummaryPool(rows)

    summary = await get_apple_health_summary(pool, 7, start_at=start_at, end_at=end_at)

    # Sleep needs yesterday's rows, so the query fetches one extra day back.
    assert pool.fetch_args == (7, start_at - timedelta(days=1), end_at)
    assert summary["sleep_hours"] == 8.2
    assert summary["steps"] == 4100
    assert summary["avg_hrv_ms"] == 48
    assert summary["metric_counts"] == {
        "sleep_analysis": 2,
        "step_count": 1,
        "heart_rate_variability": 2,
    }
    assert "Apple Health sleep: 8.2h" in summary["summary"]
    assert "Apple Health HRV (stress proxy): 48 ms" in summary["summary"]


@pytest.mark.asyncio
async def test_get_apple_health_summary_reads_sleep_hours_from_value_units(mock_settings):
    """Health Auto Export sends sleep as hour values without duration_seconds."""
    from app.services.apple_health import get_apple_health_summary

    start_at = datetime(2026, 7, 10, 21, 0, tzinfo=timezone.utc)
    end_at = start_at + timedelta(days=1)
    pool = SummaryPool([
        apple_row("sleep_analysis", 7.5, "hr", start_at - timedelta(hours=1)),
    ])

    summary = await get_apple_health_summary(pool, 7, start_at=start_at, end_at=end_at)

    assert summary["sleep_hours"] == 7.5


@pytest.mark.asyncio
async def test_get_today_stats_uses_apple_health_only_data(mock_settings):
    from app.services.ai_assistant import get_today_stats

    today = _kyiv_today_start_utc()
    pool = StatsPool(
        apple_rows=[
            apple_row("step_count", 4200, "count", today + timedelta(hours=8)),
            apple_row("active_energy", 315, "kcal", today + timedelta(hours=9)),
            apple_row("heart_rate", 62, "bpm", today + timedelta(hours=10)),
            apple_row("heart_rate_variability", 48, "ms", today + timedelta(hours=7)),
            # Last night: starts yesterday 23:00, ends 06:30 today → today's sleep.
            apple_row(
                "sleep_analysis", 0, "s",
                today - timedelta(hours=1), duration_seconds=27000,
            ),
        ],
    )

    with patch("app.services.ai_assistant.get_pool", AsyncMock(return_value=pool)):
        stats = await get_today_stats(7)

    assert stats["today_calories_out"] == 315
    assert stats["calories_burned_source"] == "apple_health"
    assert stats["apple_health_steps"] == 4200
    assert stats["apple_health_avg_heart_rate"] == 62
    assert stats["apple_health_avg_hrv_ms"] == 48
    assert stats["apple_health_sleep_hours"] == 7.5
    assert "Apple Health steps: 4200" in stats["apple_health_summary"]
    assert "Apple Health HRV (stress proxy): 48 ms" in stats["apple_health_summary"]


@pytest.mark.asyncio
async def test_get_today_stats_prefers_whoop_burn_when_mixed_with_apple_health(mock_settings):
    from app.services.ai_assistant import get_today_stats

    today = _kyiv_today_start_utc()
    pool = StatsPool(
        whoop_user={
            "id": 7,
            "whoop_access_token": "access",
            "whoop_refresh_token": "refresh",
            "whoop_token_expires_at": datetime(2026, 7, 10, tzinfo=timezone.utc),
        },
        apple_rows=[
            apple_row("Step Count", 8000, "count", today + timedelta(hours=8)),
            apple_row("Active Energy Burned", 250, "kcal", today + timedelta(hours=9)),
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
