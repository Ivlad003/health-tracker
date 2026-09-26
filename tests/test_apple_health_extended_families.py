"""Variant A: resting heart rate, body mass, distance, and exercise time."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
TODAY = "2026-09-20"


def _normalize(metric_type, value, unit):
    from app.services.apple_health import _normalize_metric_value_and_unit

    return _normalize_metric_value_and_unit(value, metric_type, unit)


@pytest.mark.parametrize(
    ("metric_type", "expected"),
    [
        ("resting_heart_rate", "resting_heart_rate"),
        ("RestingHeartRate", "resting_heart_rate"),
        ("body_mass", "body_mass"),
        ("weight_body_mass", "body_mass"),  # Health Auto Export name
        ("walking_running_distance", "distance"),
        ("distance_walking_running", "distance"),
        ("apple_exercise_time", "exercise_time"),
        ("exercise_minutes", "exercise_time"),
        # Existing families keep their mapping.
        ("heart_rate", "heart_rate"),
        ("walking_heart_rate_average", "heart_rate"),
        ("step_count", "steps"),
    ],
)
def test_metric_family_mapping(mock_settings, metric_type, expected):
    from app.services.apple_health import metric_family_for_type

    assert metric_family_for_type(metric_type) == expected


@pytest.mark.parametrize(
    ("metric_type", "value", "unit", "expected_value", "expected_unit"),
    [
        ("resting_heart_rate", "58 count/min", "count/min", Decimal("58"), "count/min"),
        ("resting_heart_rate", "61", "уд/хв", Decimal("61"), "count/min"),
        ("body_mass", "72,5 кг", "кг", Decimal("72.5"), "kg"),
        ("body_mass", "160", "lb", Decimal("72.5747792"), "kg"),
        ("body_mass", "72500", "g", Decimal("72.500"), "kg"),
        ("walking_running_distance", "5.2", "km", Decimal("5200.0"), "m"),
        ("walking_running_distance", "1", "mi", Decimal("1609.344"), "m"),
        ("walking_running_distance", "850", "м", Decimal("850"), "m"),
        ("apple_exercise_time", "34", "min", Decimal("34"), "min"),
        ("apple_exercise_time", "1.5", "hr", Decimal("90.0"), "min"),
        ("apple_exercise_time", "120", "s", Decimal("2"), "min"),
    ],
)
def test_unit_normalization(mock_settings, metric_type, value, unit, expected_value, expected_unit):
    normalized_value, normalized_unit = _normalize(metric_type, value, unit)
    assert normalized_unit == expected_unit
    assert normalized_value == expected_value


@pytest.mark.parametrize(
    ("metric_type", "value", "unit"),
    [
        ("body_mass", "72", "kcal"),  # incompatible unit
        ("walking_running_distance", "3", "count"),
        ("resting_heart_rate", "400", "bpm"),  # above physiological bound
        ("body_mass", "900", "kg"),
        ("apple_exercise_time", "2000", "min"),
        ("body_mass", "-1", "kg"),
    ],
)
def test_invalid_values_are_rejected(mock_settings, metric_type, value, unit):
    from app.services.apple_health import AppleHealthIngestionError

    with pytest.raises(AppleHealthIngestionError):
        _normalize(metric_type, value, unit)


def test_aggregation_sums_and_averages_new_families(mock_settings):
    from app.services.apple_health import (
        _finalize_metric_family,
        _normalize_metric,
        aggregate_metric_families_by_day,
    )

    raw = [
        {"type": "walking_running_distance", "value": "1.5", "unit": "km", "timestamp": f"{TODAY}T08:00:00+00:00"},
        {"type": "walking_running_distance", "value": "500", "unit": "m", "timestamp": f"{TODAY}T09:00:00+00:00"},
        {"type": "apple_exercise_time", "value": "20", "unit": "min", "timestamp": f"{TODAY}T08:00:00+00:00"},
        {"type": "apple_exercise_time", "value": "12", "unit": "min", "timestamp": f"{TODAY}T18:00:00+00:00"},
        {"type": "resting_heart_rate", "value": "56", "unit": "count/min", "timestamp": f"{TODAY}T06:00:00+00:00"},
        {"type": "resting_heart_rate", "value": "60", "unit": "count/min", "timestamp": f"{TODAY}T07:00:00+00:00"},
        {"type": "body_mass", "value": "72.4", "unit": "kg", "timestamp": f"{TODAY}T07:05:00+00:00"},
    ]
    normalized = [_normalize_metric(m, data_type="activity", current_time=NOW) for m in raw]
    day = date(2026, 9, 20)
    coverage = {f: {day} for f in ("distance", "exercise_time", "resting_heart_rate", "body_mass")}

    groups = aggregate_metric_families_by_day(normalized, tz=timezone.utc, coverage=coverage)
    rows = {family: _finalize_metric_family(family, acc) for (_d, family), acc in groups.items()}

    assert rows["distance"]["total_value"] == Decimal("2000.0")
    assert rows["exercise_time"]["total_value"] == Decimal("32.0")
    assert rows["resting_heart_rate"]["average_value"] == Decimal("58.0")
    assert rows["resting_heart_rate"]["sample_count"] == 2
    assert rows["body_mass"]["average_value"] == Decimal("72.4")
    assert rows["body_mass"]["details"]["records_by_type"] == {"body_mass": 1}


def test_empty_covered_family_is_an_authoritative_zero(mock_settings):
    """A denied/absent HealthKit type yields a zero row, not a stale value."""
    from app.services.apple_health import _finalize_metric_family, aggregate_metric_families_by_day

    day = date(2026, 9, 20)
    groups = aggregate_metric_families_by_day([], tz=timezone.utc, coverage={"body_mass": {day}})
    row = _finalize_metric_family("body_mass", groups[(day, "body_mass")])
    assert row["average_value"] is None
    assert row["sample_count"] == 0


def test_snapshot_meta_accepts_new_families(mock_settings):
    from app.services.apple_health import _extract_snapshot_meta

    meta = _extract_snapshot_meta(
        {
            "schemaVersion": 3,
            "snapshot": {
                "collector": "shortcut",
                "timezone": "+03:00",
                "generatedAt": "2026-09-20T14:00:00+03:00",
                "coveredDates": [TODAY],
                "coveredMetricFamilies": [
                    "steps", "resting_heart_rate", "body_mass", "distance", "exercise_time",
                ],
            },
        },
        current_time=NOW,
    )
    assert set(meta.coverage) == {"steps", "resting_heart_rate", "body_mass", "distance", "exercise_time"}


def test_health_auto_export_weight_metric_converts(mock_settings):
    from app.services.apple_health import convert_health_auto_export

    converted = convert_health_auto_export(
        {"data": {"metrics": [{
            "name": "weight_body_mass",
            "units": "kg",
            "data": [{"date": "2026-09-20 07:00:00 +0300", "qty": 72.1}],
        }]}},
        telegram_user_id=1,
        automation_period="Today",
        snapshot_timezone="+03:00",
        snapshot_generated_at="2026-09-20T14:00:00+03:00",
        now=NOW,
    )
    assert converted["snapshot"]["coveredDatesByFamily"] == {"body_mass": [TODAY]}


class _SummaryPool:
    def __init__(self, v3_rows):
        self.v3_rows = v3_rows

    async def fetch(self, query, *args):
        if "FROM health_daily_metric_aggregates" in query and "collector NOT IN" in query:
            return self.v3_rows
        return []


def _row(day, family, *, total=0, average=None, count=0):
    return {
        "metric_date": day,
        "metric_family": family,
        "timezone": "+00:00",
        "total_value": total,
        "average_value": average,
        "sample_count": count,
        "samples_received": count,
        "metrics": {"records_by_type": {}},
        "snapshot_generated_at": NOW,
        "updated_at": NOW,
    }


@pytest.mark.asyncio
async def test_summary_reports_new_families(mock_settings):
    from app.services.apple_health import get_apple_health_summary

    day = date(2026, 9, 20)
    pool = _SummaryPool([
        _row(day, "distance", total=Decimal("4250")),
        _row(day, "exercise_time", total=Decimal("31.6")),
        _row(day, "resting_heart_rate", average=Decimal("57"), count=2),
        _row(day, "body_mass", average=Decimal("72.44"), count=1),
    ])
    start = datetime(2026, 9, 20, tzinfo=timezone.utc)

    summary = await get_apple_health_summary(pool, 7, start_at=start, end_at=start + timedelta(days=1))

    assert summary["distance_km"] == 4.25
    assert summary["exercise_minutes"] == 32
    assert summary["resting_heart_rate"] == 57
    assert summary["body_mass_kg"] == 72.4
    assert "resting heart rate: 57 bpm" in summary["summary"]
    assert "walking+running distance: 4.25 km" in summary["summary"]
    assert "body mass: 72.4 kg" in summary["summary"]


@pytest.mark.asyncio
async def test_summary_ignores_zero_sample_body_mass(mock_settings):
    from app.services.apple_health import get_apple_health_summary

    day = date(2026, 9, 20)
    pool = _SummaryPool([_row(day, "body_mass", average=None, count=0)])
    start = datetime(2026, 9, 20, tzinfo=timezone.utc)

    summary = await get_apple_health_summary(pool, 7, start_at=start, end_at=start + timedelta(days=1))

    assert summary["body_mass_kg"] == 0
    assert "body mass" not in summary["summary"]


@pytest.mark.asyncio
async def test_get_latest_body_mass_queries_lookback_window(mock_settings):
    from app.services.apple_health import get_latest_body_mass

    captured = {}

    class Pool:
        async def fetchrow(self, query, *args):
            captured["query"] = query
            captured["args"] = args
            return {"metric_date": date(2026, 9, 15), "average_value": Decimal("71.96")}

    result = await get_latest_body_mass(Pool(), 7, today=date(2026, 9, 20))

    assert result == {"metric_date": date(2026, 9, 15), "kg": 72.0}
    assert "metric_family = 'body_mass'" in captured["query"]
    assert "sample_count > 0" in captured["query"]
    assert captured["args"] == (7, date(2026, 8, 21), date(2026, 9, 21))


def test_migration_011_widens_family_check_and_is_idempotent():
    from pathlib import Path

    sql = (
        Path(__file__).resolve().parents[1]
        / "database" / "migrations" / "011_apple_health_extended_families.sql"
    ).read_text()
    for family in ("resting_heart_rate", "body_mass", "distance", "exercise_time"):
        assert f"'{family}'" in sql
    assert "DROP CONSTRAINT IF EXISTS health_daily_metric_aggregates_family_check" in sql
    assert "IF NOT EXISTS" in sql


def test_preflight_requires_migration_011():
    from app.db_preflight import (
        APPLE_HEALTH_MIGRATIONS,
        REQUIRED_APPLE_HEALTH_CONSTRAINTS,
        REQUIRED_APPLE_HEALTH_INDEXES,
    )

    names = [path.name for path in APPLE_HEALTH_MIGRATIONS]
    assert names[names.index("011_apple_health_extended_families.sql"):] == [
        "011_apple_health_extended_families.sql",
        "012_user_profile.sql",
        "013_health_workouts.sql",
        "014_apple_health_secret_hash.sql",
        "015_drop_unused_tables.sql",
        "016_food_ledger_catalog.sql",
        "017_webapp_admin_preferences.sql",
        "018_webapp_hardening.sql",
        ]
    assert "health_daily_metric_aggregates_family_check_v2" in REQUIRED_APPLE_HEALTH_CONSTRAINTS
    assert "idx_health_daily_metric_aggregates_user_family_date" in REQUIRED_APPLE_HEALTH_INDEXES


@pytest.mark.parametrize(
    ("value", "expected"),
    [("72,5 кг", "кг"), ("3.1 mi", "mi"), ("5 037 m", "m"), ("58 count/min", "count/min"), ("72.5", ""), (72.5, "")],
)
def test_unit_suffix_extraction(mock_settings, value, expected):
    from app.services.apple_health import _unit_suffix

    assert _unit_suffix(value) == expected


def test_empty_unit_falls_back_to_value_suffix(mock_settings):
    from app.services.apple_health import AppleHealthIngestionError, _normalize_metric

    normalized = _normalize_metric(
        {"type": "body_mass", "value": "160 lb", "unit": "", "timestamp": f"{TODAY}T07:00:00+00:00"},
        data_type="activity",
        current_time=NOW,
    )
    assert normalized["unit"] == "kg"
    assert round(float(normalized["value"]), 2) == 72.57

    with pytest.raises(AppleHealthIngestionError, match="unit is required"):
        _normalize_metric(
            {"type": "body_mass", "value": "72", "unit": "", "timestamp": f"{TODAY}T07:00:00+00:00"},
            data_type="activity",
            current_time=NOW,
        )
