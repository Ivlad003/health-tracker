from datetime import date, datetime, timedelta, timezone
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
        if "FROM health_daily_aggregates" in query:
            return self.apple_rows
        raise AssertionError(f"Unexpected fetch query: {query}")


class SummaryPool:
    def __init__(self, rows):
        self.rows = rows
        self.fetch_args = None

    async def fetch(self, query, *args):
        assert "FROM health_daily_aggregates" in query
        self.fetch_args = args
        return self.rows


def daily_row(
    metric_date,
    *,
    tz="+03:00",
    steps=0,
    active_energy_kcal=0,
    avg_heart_rate=None,
    heart_rate_samples=0,
    avg_hrv_ms=None,
    hrv_samples=0,
    sleep_seconds=0,
    records_by_type=None,
    snapshot_generated_at=None,
    updated_at=None,
):
    """One processed ``health_daily_aggregates`` row, matching the reader SELECT."""
    return {
        "metric_date": metric_date,
        "timezone": tz,
        "steps": steps,
        "active_energy_kcal": active_energy_kcal,
        "avg_heart_rate": avg_heart_rate,
        "heart_rate_samples": heart_rate_samples,
        "avg_hrv_ms": avg_hrv_ms,
        "hrv_samples": hrv_samples,
        "sleep_seconds": sleep_seconds,
        "metrics": {"records_by_type": records_by_type or {}},
        "snapshot_generated_at": snapshot_generated_at,
        "updated_at": updated_at,
    }


def _kyiv_today() -> date:
    return datetime.now(ZoneInfo("Europe/Kyiv")).date()


@pytest.mark.asyncio
async def test_get_apple_health_summary_reads_daily_aggregate_for_local_day(mock_settings):
    from app.services.apple_health import get_apple_health_summary

    start_at = datetime(2026, 7, 10, 21, 0, tzinfo=timezone.utc)  # 00:00 Kyiv (07-11)
    end_at = start_at + timedelta(days=1)
    rows = [
        # Today's processed aggregate: its local midnight equals start_at, so it
        # is included. Sleep already merged/attributed by the ingest layer.
        daily_row(
            date(2026, 7, 11),
            sleep_seconds=29520,  # 8.2h
            steps=4100,
            avg_hrv_ms=48,
            hrv_samples=2,
            records_by_type={
                "sleep_analysis": 2,
                "step_count": 1,
                "heart_rate_variability": 2,
            },
        ),
        # Yesterday's row comes back from the widened fetch window but its local
        # midnight is before start_at, so it must not leak into today's totals.
        daily_row(
            date(2026, 7, 10),
            steps=900,
            sleep_seconds=3600,
            records_by_type={"step_count": 1, "sleep_analysis": 1},
        ),
    ]
    pool = SummaryPool(rows)

    summary = await get_apple_health_summary(pool, 7, start_at=start_at, end_at=end_at)

    # The reader widens the SQL window by two days on each side and filters by
    # each row's local midnight in Python.
    assert pool.fetch_args == (
        7,
        (start_at - timedelta(days=2)).date(),
        (end_at + timedelta(days=2)).date(),
    )
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
async def test_get_apple_health_summary_reads_sleep_hours_from_daily_aggregate(mock_settings):
    from app.services.apple_health import get_apple_health_summary

    start_at = datetime(2026, 7, 10, 21, 0, tzinfo=timezone.utc)
    end_at = start_at + timedelta(days=1)
    pool = SummaryPool([
        daily_row(date(2026, 7, 11), sleep_seconds=27000),  # 7.5h
    ])

    summary = await get_apple_health_summary(pool, 7, start_at=start_at, end_at=end_at)

    assert summary["sleep_hours"] == 7.5


@pytest.mark.asyncio
async def test_get_today_stats_uses_apple_health_only_data(mock_settings):
    from app.services.ai_assistant import get_today_stats

    pool = StatsPool(
        apple_rows=[
            daily_row(
                _kyiv_today(),
                steps=4200,
                active_energy_kcal=315,
                avg_heart_rate=62,
                heart_rate_samples=1,
                avg_hrv_ms=48,
                hrv_samples=1,
                sleep_seconds=27000,  # 7.5h
                records_by_type={
                    "step_count": 1,
                    "active_energy": 1,
                    "heart_rate": 1,
                    "heart_rate_variability": 1,
                    "sleep_analysis": 1,
                },
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

    pool = StatsPool(
        whoop_user={
            "id": 7,
            "whoop_access_token": "access",
            "whoop_refresh_token": "refresh",
            "whoop_token_expires_at": datetime(2026, 7, 10, tzinfo=timezone.utc),
        },
        apple_rows=[
            daily_row(
                _kyiv_today(),
                steps=8000,
                active_energy_kcal=250,
                records_by_type={"step_count": 1, "active_energy": 1},
            ),
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
