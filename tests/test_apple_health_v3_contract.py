from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


def _snapshot(**snapshot_overrides):
    snapshot = {
        "collector": "shortcut",
        "timezone": "Europe/Kyiv",
        "generatedAt": "2026-07-13T14:59:00+03:00",
        "coveredDates": ["2026-07-13"],
        "coveredMetricFamilies": ["steps", "active_energy", "sleep", "hrv"],
    }
    snapshot.update(snapshot_overrides)
    return {"schemaVersion": 3, "snapshot": snapshot}


def test_schema_v3_expands_dates_across_declared_metric_families(mock_settings):
    from app.services.apple_health import _extract_snapshot_meta

    meta = _extract_snapshot_meta(_snapshot(), current_time=NOW)

    assert meta.collector == "shortcut"
    assert meta.generated_at == datetime(2026, 7, 13, 11, 59, tzinfo=timezone.utc)
    assert meta.coverage == {
        "steps": {date(2026, 7, 13)},
        "active_energy": {date(2026, 7, 13)},
        "sleep": {date(2026, 7, 13)},
        "hrv": {date(2026, 7, 13)},
    }


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("collector", None, "snapshot.collector"),
        ("generatedAt", None, "snapshot.generatedAt"),
        ("generatedAt", "2026-07-13T11:59:00", "timezone offset"),
        ("generatedAt", "2026-07-14T12:00:00+00:00", "future"),
        ("coveredMetricFamilies", [], "coveredMetricFamilies"),
    ],
)
def test_schema_v3_rejects_incomplete_or_ambiguous_metadata(
    mock_settings, field, value, message
):
    from app.services.apple_health import AppleHealthIngestionError, _extract_snapshot_meta

    with pytest.raises(AppleHealthIngestionError, match=message):
        _extract_snapshot_meta(_snapshot(**{field: value}), current_time=NOW)


@pytest.mark.parametrize(
    "collector", ["legacy_daily", "legacy_backfill", "synthetic_fixture", "other"]
)
def test_schema_v3_rejects_unapproved_or_reserved_collectors(
    mock_settings, collector
):
    from app.services.apple_health import AppleHealthIngestionError, _extract_snapshot_meta

    with pytest.raises(AppleHealthIngestionError, match="supported collector"):
        _extract_snapshot_meta(_snapshot(collector=collector), current_time=NOW)


def test_schema_v3_rejects_more_than_31_covered_dates_per_family(mock_settings):
    from app.services.apple_health import AppleHealthIngestionError, _extract_snapshot_meta

    dates = [
        (date(2026, 6, 13) + timedelta(days=offset)).isoformat()
        for offset in range(32)
    ]
    with pytest.raises(AppleHealthIngestionError, match="at most 31 dates"):
        _extract_snapshot_meta(
            _snapshot(coveredDates=dates, coveredMetricFamilies=["steps"]),
            current_time=NOW,
        )


def test_schema_v3_rejects_mixed_coverage_encodings(mock_settings):
    from app.services.apple_health import AppleHealthIngestionError, _extract_snapshot_meta

    with pytest.raises(AppleHealthIngestionError, match="exactly one coverage encoding"):
        _extract_snapshot_meta(
            _snapshot(coveredDatesByFamily={"steps": ["2026-07-13"]}),
            current_time=NOW,
        )


@pytest.mark.parametrize("covered_date", ["2026-06-12", "2026-07-15"])
def test_schema_v3_rejects_covered_dates_outside_ingestion_window(
    mock_settings, covered_date
):
    from app.services.apple_health import AppleHealthIngestionError, _extract_snapshot_meta

    with pytest.raises(AppleHealthIngestionError, match="within 30 days"):
        _extract_snapshot_meta(
            _snapshot(coveredDates=[covered_date], coveredMetricFamilies=["steps"]),
            current_time=NOW,
        )


@pytest.mark.parametrize(
    "metric_type",
    [
        "step_count",
        "active_energy",
        "heart_rate",
        "heart_rate_variability",
        "sleep_analysis",
    ],
)
def test_supported_health_metrics_reject_negative_values(mock_settings, metric_type):
    from app.services.apple_health import AppleHealthIngestionError, _normalize_metric

    with pytest.raises(AppleHealthIngestionError, match="must be non-negative"):
        _normalize_metric(
            {
                "type": metric_type,
                "value": -1,
                "unit": "count",
                "timestamp": "2026-07-13T11:00:00+00:00",
            },
            data_type=None,
            current_time=NOW,
        )


def test_supported_health_metric_rejects_excessive_magnitude(mock_settings):
    from app.services.apple_health import AppleHealthIngestionError, _normalize_metric

    with pytest.raises(AppleHealthIngestionError, match="supported range"):
        _normalize_metric(
            {
                "type": "step_count",
                "value": "9" * 400,
                "unit": "count",
                "timestamp": "2026-07-13T11:00:00+00:00",
            },
            data_type=None,
            current_time=NOW,
        )


@pytest.mark.parametrize(
    ("metric_type", "unit"),
    [
        ("step_count", "kg"),
        ("active_energy", "count"),
        ("heart_rate", "kcal"),
        ("heart_rate_variability", "s"),
        ("sleep_analysis", "kcal"),
    ],
)
def test_supported_health_metrics_reject_incompatible_units(
    mock_settings, metric_type, unit
):
    from app.services.apple_health import AppleHealthIngestionError, _normalize_metric

    with pytest.raises(AppleHealthIngestionError, match="unit is not supported"):
        _normalize_metric(
            {
                "type": metric_type,
                "value": 1,
                "unit": unit,
                "timestamp": "2026-07-13T11:00:00+00:00",
            },
            data_type=None,
            current_time=NOW,
        )


def test_active_energy_kilojoules_are_normalized_to_kilocalories(mock_settings):
    from app.services.apple_health import _normalize_metric

    normalized = _normalize_metric(
        {
            "type": "active_energy",
            "value": "418.4",
            "unit": "kJ",
            "timestamp": "2026-07-13T11:00:00+00:00",
        },
        data_type="auto_export",
        current_time=NOW,
    )

    assert normalized["unit"] == "kcal"
    assert normalized["value"] == 100


@pytest.mark.parametrize(
    ("metric_type", "unit", "canonical_unit"),
    [
        ("step_count", "steps", "count"),
        ("heart_rate", "bpm", "count/min"),
        ("heart_rate_variability", "milliseconds", "ms"),
        ("sleep_analysis", "hours", "hr"),
    ],
)
def test_supported_health_metric_unit_aliases_are_canonicalized(
    mock_settings, metric_type, unit, canonical_unit
):
    from app.services.apple_health import _normalize_metric

    normalized = _normalize_metric(
        {
            "type": metric_type,
            "value": 1,
            "unit": unit,
            "timestamp": "2026-07-13T11:00:00+00:00",
        },
        data_type="auto_export",
        current_time=NOW,
    )

    assert normalized["unit"] == canonical_unit


@pytest.mark.parametrize(
    "duration", ["not-a-duration", "NaN", "1.5", "1e10000000"]
)
def test_metric_duration_rejects_malformed_nonfinite_or_fractional_values(
    mock_settings, duration
):
    from app.services.apple_health import AppleHealthIngestionError, _normalize_metric

    with pytest.raises(AppleHealthIngestionError, match="metric duration"):
        _normalize_metric(
            {
                "type": "sleep_analysis",
                "value": 1,
                "unit": "hr",
                "timestamp": "2026-07-13T11:00:00+00:00",
                "duration": duration,
            },
            data_type=None,
            current_time=NOW,
        )


@pytest.mark.parametrize("offset", ["+24:00", "+03:60", "+12:99"])
def test_invalid_fixed_offsets_raise_controlled_ingestion_error(mock_settings, offset):
    from app.services.apple_health import AppleHealthIngestionError, _parse_timezone

    with pytest.raises(AppleHealthIngestionError, match="UTC offset"):
        _parse_timezone(offset)


@pytest.mark.parametrize("offset", ["+14:00", "-12:00", "+05:45", "Europe/Kyiv"])
def test_valid_fixed_offsets_and_iana_zones_are_accepted(mock_settings, offset):
    from app.services.apple_health import _parse_timezone

    assert _parse_timezone(offset) is not None


def _sleep_metric(start: datetime, end: datetime, stage: str):
    return {
        "metric_type": "sleep_analysis",
        "metric_subtype": "activity",
        "value": 0,
        "unit": "s",
        "recorded_at": start,
        "duration_seconds": int((end - start).total_seconds()),
        "additional_data": {"stage": stage},
        "local_recorded_date": start.date(),
        "local_end_date": end.date(),
    }


def test_sleep_uses_asleep_stages_and_excludes_awake_and_in_bed(mock_settings):
    from app.services.apple_health import (
        METRIC_FAMILY_SLEEP,
        _finalize_metric_family,
        aggregate_metric_families_by_day,
    )

    start = datetime(2026, 7, 12, 22, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=8)
    metrics = [
        _sleep_metric(start, end, "In Bed"),
        _sleep_metric(start + timedelta(hours=2), start + timedelta(hours=2, minutes=20), "Awake"),
        _sleep_metric(start + timedelta(minutes=30), start + timedelta(hours=3), "Core"),
        _sleep_metric(start + timedelta(hours=3), start + timedelta(hours=4), "Deep"),
        _sleep_metric(start + timedelta(hours=4), start + timedelta(hours=7, minutes=30), "REM"),
    ]

    groups = aggregate_metric_families_by_day(
        metrics,
        tz=timezone.utc,
        coverage={METRIC_FAMILY_SLEEP: {date(2026, 7, 13)}},
    )
    row = _finalize_metric_family(
        METRIC_FAMILY_SLEEP, groups[(date(2026, 7, 13), METRIC_FAMILY_SLEEP)]
    )

    assert int(row["total_value"]) == 7 * 3600
    assert row["details"]["stage_seconds"] == {
        "Core": 9000,
        "Deep": 3600,
        "REM": 12600,
    }


def test_sleep_falls_back_to_in_bed_minus_awake_without_asleep_stages(mock_settings):
    from app.services.apple_health import (
        METRIC_FAMILY_SLEEP,
        _finalize_metric_family,
        aggregate_metric_families_by_day,
    )

    start = datetime(2026, 7, 12, 22, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=8)
    metrics = [
        _sleep_metric(start, end, "In Bed"),
        _sleep_metric(start + timedelta(hours=2), start + timedelta(hours=2, minutes=20), "Awake"),
    ]

    groups = aggregate_metric_families_by_day(
        metrics,
        tz=timezone.utc,
        coverage={METRIC_FAMILY_SLEEP: {date(2026, 7, 13)}},
    )
    row = _finalize_metric_family(
        METRIC_FAMILY_SLEEP, groups[(date(2026, 7, 13), METRIC_FAMILY_SLEEP)]
    )

    assert int(row["total_value"]) == 7 * 3600 + 40 * 60
    assert row["details"]["sleep_basis"] == "in_bed_minus_awake"


def test_awake_only_sleep_coverage_is_authoritative_zero(mock_settings):
    from app.services.apple_health import (
        METRIC_FAMILY_SLEEP,
        _finalize_metric_family,
        aggregate_metric_families_by_day,
    )

    start = datetime(2026, 7, 13, 5, 0, tzinfo=timezone.utc)
    metrics = [_sleep_metric(start, start + timedelta(minutes=20), "Awake")]

    groups = aggregate_metric_families_by_day(
        metrics,
        tz=timezone.utc,
        coverage={METRIC_FAMILY_SLEEP: {date(2026, 7, 13)}},
    )
    row = _finalize_metric_family(
        METRIC_FAMILY_SLEEP, groups[(date(2026, 7, 13), METRIC_FAMILY_SLEEP)]
    )

    assert int(row["total_value"]) == 0


@pytest.mark.parametrize(
    ("stage", "expected_kind", "expected_label"),
    [
        ("Неспання", "awake", "Awake"),
        ("Без сну", "awake", "Awake"),
        ("У ліжку", "in_bed", "In Bed"),
        ("Основний сон", "asleep", "Core"),
        ("Основний", "asleep", "Core"),
        ("Повільний", "asleep", "Core"),
        ("Глибокий сон", "asleep", "Deep"),
        ("Глибокий", "asleep", "Deep"),
        ("Швидкий сон", "asleep", "REM"),
        ("Швидкий", "asleep", "REM"),
    ],
)
def test_sleep_stage_recognizes_ukrainian_apple_labels(
    mock_settings, stage, expected_kind, expected_label
):
    from app.services.apple_health import _sleep_stage

    assert _sleep_stage({"additional_data": {"stage": stage}}) == (
        expected_kind,
        expected_label,
    )


def test_unknown_localized_sleep_stage_fails_safe_instead_of_inflating_sleep(
    mock_settings,
):
    from app.services.apple_health import (
        METRIC_FAMILY_SLEEP,
        _finalize_metric_family,
        aggregate_metric_families_by_day,
    )

    start = datetime(2026, 7, 13, 1, 0, tzinfo=timezone.utc)
    metrics = [_sleep_metric(start, start + timedelta(hours=8), "Нова фаза")]
    groups = aggregate_metric_families_by_day(
        metrics,
        tz=timezone.utc,
        coverage={METRIC_FAMILY_SLEEP: {date(2026, 7, 13)}},
    )
    row = _finalize_metric_family(
        METRIC_FAMILY_SLEEP, groups[(date(2026, 7, 13), METRIC_FAMILY_SLEEP)]
    )

    assert int(row["total_value"]) == 0
    assert row["samples_aggregated"] == 0
    assert row["details"]["unknown_stage_samples"] == 1
