from __future__ import annotations

import hmac
import hashlib
import json
import logging
import re
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)


class AppleHealthIngestionError(ValueError):
    """Raised when Apple Health payload validation or ingestion fails."""


class AppleHealthSnapshotConflictError(AppleHealthIngestionError):
    """Raised when one snapshot timestamp identifies different processed data."""


class AppleHealthPersistenceError(RuntimeError):
    """Raised when validated Apple Health data cannot be persisted."""


_STEP_METRICS = {"step_count", "stepcount", "steps", "step_count_total"}
_ACTIVE_ENERGY_METRICS = {
    "active_energy",
    "activeenergy",
    "active_energy_burned",
    "activeenergyburned",
    "active_calories",
}
_HEART_RATE_METRICS = {
    "heart_rate",
    "heartrate",
    "heart_rate_average",
    "walking_heart_rate_average",
}
_SLEEP_METRICS = {"sleep", "sleep_analysis", "sleepanalysis", "asleep", "sleep_duration"}
_HRV_METRICS = {
    "heart_rate_variability",
    "heartratevariability",
    "heart_rate_variability_sdnn",
    "hrv",
    "hrv_sdnn",
}

# Metric types the summary/aggregation layer knows how to interpret. Anything
# outside this set is counted for diagnostics but is not persisted because raw
# HealthKit retention is zero and there is no declared aggregate family for it.
_SUMMARY_MAPPED_METRICS = (
    _STEP_METRICS
    | _ACTIVE_ENERGY_METRICS
    | _HEART_RATE_METRICS
    | _SLEEP_METRICS
    | _HRV_METRICS
)

# Human-readable labels for the parsed-data summary, keyed by the normalized
# metric key produced by _metric_key().
_METRIC_SUMMARY_LABELS = {
    "step_count": "steps",
    "active_energy": "active energy",
    "heart_rate": "HR samples",
    "heart_rate_variability": "HRV samples",
    "sleep_analysis": "sleep",
}

# A single sleep sample longer than a day is bogus input; clamp it so one bad
# interval cannot inflate the daily total.
_MAX_SLEEP_SAMPLE_SECONDS = 24 * 3600

# Apple Health raw-sample retention is 0 days: individual HealthKit samples are
# aggregated in memory and only processed per-family daily results are persisted
# (in health_daily_metric_aggregates). This is the count of raw health_data rows
# written per sync -- always zero -- surfaced in results/summaries so operators
# can confirm the retention guarantee.
RAW_ROWS_STORED = 0

# The ingestion contract version the Shortcut must send. A payload without the
# schema-v3 collector, freshness, timezone, and per-family coverage envelope is
# rejected (see _extract_snapshot_meta) rather than merged, because a partial
# snapshot could otherwise clear unrelated or newer family aggregates.
SNAPSHOT_SCHEMA_VERSION = 3

# External snapshots may identify only the two supported live senders. Keeping
# this set closed prevents a caller from rotating arbitrary collector names to
# bypass freshness/idempotency and create unbounded durable rows.
LIVE_SNAPSHOT_COLLECTORS = frozenset({"shortcut", "health_auto_export"})
MAX_COVERED_DATES_PER_FAMILY = 31
MAX_COVERAGE_AGE_DAYS = 30
MAX_COVERAGE_FUTURE_DAYS = 1
MAX_METRICS_PER_SNAPSHOT = 10_000

METRIC_FAMILY_STEPS = "steps"
METRIC_FAMILY_ACTIVE_ENERGY = "active_energy"
METRIC_FAMILY_HEART_RATE = "heart_rate"
METRIC_FAMILY_HRV = "hrv"
METRIC_FAMILY_SLEEP = "sleep"
SUPPORTED_METRIC_FAMILIES = frozenset(
    {
        METRIC_FAMILY_STEPS,
        METRIC_FAMILY_ACTIVE_ENERGY,
        METRIC_FAMILY_HEART_RATE,
        METRIC_FAMILY_HRV,
        METRIC_FAMILY_SLEEP,
    }
)
MAX_METRIC_VALUE_BY_FAMILY = {
    METRIC_FAMILY_STEPS: Decimal("1000000"),
    METRIC_FAMILY_ACTIVE_ENERGY: Decimal("1000000"),
    METRIC_FAMILY_HEART_RATE: Decimal("1000"),
    METRIC_FAMILY_HRV: Decimal("100000"),
    METRIC_FAMILY_SLEEP: Decimal("1000000"),
}
MAX_PERSISTED_ERROR_MESSAGE_CHARS = 256

_UNIT_ALIASES_BY_FAMILY = {
    METRIC_FAMILY_STEPS: {
        "count": "count",
        "counts": "count",
        "step": "count",
        "steps": "count",
    },
    METRIC_FAMILY_ACTIVE_ENERGY: {
        "kcal": "kcal",
        "kilocalorie": "kcal",
        "kilocalories": "kcal",
        "ккал": "kcal",
        "kj": "kJ",
        "kilojoule": "kJ",
        "kilojoules": "kJ",
        "кдж": "kJ",
    },
    METRIC_FAMILY_HEART_RATE: {
        "count/min": "count/min",
        "counts/min": "count/min",
        "bpm": "count/min",
        "beat/min": "count/min",
        "beats/min": "count/min",
    },
    METRIC_FAMILY_HRV: {
        "ms": "ms",
        "millisecond": "ms",
        "milliseconds": "ms",
        "мс": "ms",
    },
    METRIC_FAMILY_SLEEP: {
        "h": "hr",
        "hr": "hr",
        "hour": "hr",
        "hours": "hr",
        "m": "min",
        "min": "min",
        "minute": "min",
        "minutes": "min",
        "s": "s",
        "sec": "s",
        "second": "s",
        "seconds": "s",
    },
}


@dataclass(frozen=True)
class SnapshotMeta:
    tzinfo: Any
    timezone_str: str
    collector: str
    generated_at: datetime
    generated_at_by_pair: dict[tuple[date, str], datetime]
    coverage: dict[str, set[date]]
    timezone_by_pair: dict[tuple[date, str], str]
    advance_identical_freshness: bool

_REIMPORT_GUIDANCE = (
    "Re-import the latest Apple Health Shortcut from the bot and run it again — "
    "this server now requires an ordered per-metric calendar-day snapshot "
    "(schemaVersion 3 with collector, generatedAt, timezone, dates, and metric families)."
)


def _parse_timezone(tz_str: str) -> Any:
    """Resolve a snapshot timezone string to a tzinfo.

    Accepts an IANA name ("Europe/Kyiv") or a fixed UTC offset
    ("+03:00", "+0300", "Z", "UTC"). Raises AppleHealthIngestionError on
    anything else so an ambiguous timezone fails loudly instead of silently
    defaulting and mis-attributing days.
    """
    raw = (tz_str or "").strip()
    if not raw:
        raise AppleHealthIngestionError("snapshot timezone is required")
    if raw in {"Z", "z", "UTC", "utc"}:
        return timezone.utc
    match = re.fullmatch(r"([+-])(\d{2}):?(\d{2})", raw)
    if match:
        sign = 1 if match.group(1) == "+" else -1
        hours = int(match.group(2))
        minutes = int(match.group(3))
        if hours > 14 or minutes > 59 or (hours == 14 and minutes != 0):
            raise AppleHealthIngestionError(
                "snapshot timezone is not a valid UTC offset"
            )
        try:
            offset = timedelta(hours=hours, minutes=minutes)
            return timezone(sign * offset)
        except ValueError as exc:
            raise AppleHealthIngestionError(
                "snapshot timezone is not a valid UTC offset"
            ) from exc
    try:
        return ZoneInfo(raw)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        raise AppleHealthIngestionError(
            "snapshot timezone is not a valid IANA name or UTC offset"
        ) from exc


def _format_fixed_offset(offset: timedelta | None) -> str:
    if offset is None:
        return "UTC"
    total_minutes = int(offset.total_seconds() // 60)
    if total_minutes == 0:
        return "UTC"
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    hours, minutes = divmod(total_minutes, 60)
    return f"{sign}{hours:02d}:{minutes:02d}"


def _local_date(dt: datetime, tz: Any) -> date:
    """Calendar date of an instant in the snapshot's timezone."""
    return dt.astimezone(tz).date()


def _parse_coverage_date(value: Any) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise AppleHealthIngestionError(
            "snapshot coveredDates entry must be an ISO date"
        ) from exc


def _parse_aware_datetime(value: Any, field_name: str) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise AppleHealthIngestionError(f"{field_name} is required")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AppleHealthIngestionError(f"{field_name} must be ISO 8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AppleHealthIngestionError(f"{field_name} must include a timezone offset")
    return parsed.astimezone(timezone.utc)


def _parse_metric_families(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise AppleHealthIngestionError(f"{field_name} must be a non-empty list")
    families: list[str] = []
    for entry in value:
        family = str(entry or "").strip()
        if family not in SUPPORTED_METRIC_FAMILIES:
            raise AppleHealthIngestionError(
                f"{field_name} contains an unsupported metric family"
            )
        if family not in families:
            families.append(family)
    return families


def _extract_snapshot_meta(
    payload: dict[str, Any], *, current_time: datetime | None = None
) -> SnapshotMeta:
    """Validate and extract schema-v3 freshness and per-family coverage."""
    if not isinstance(payload, dict):
        raise AppleHealthIngestionError(f"payload must be an object. {_REIMPORT_GUIDANCE}")

    try:
        schema_version = int(payload.get("schemaVersion"))
    except (TypeError, ValueError):
        schema_version = None
    if schema_version != SNAPSHOT_SCHEMA_VERSION:
        raise AppleHealthIngestionError(
            "Apple Health payload is missing the completeness envelope "
            f"(schemaVersion {SNAPSHOT_SCHEMA_VERSION}). {_REIMPORT_GUIDANCE}"
        )

    snapshot = payload.get("snapshot")
    if not isinstance(snapshot, dict):
        raise AppleHealthIngestionError(
            f"Apple Health payload is missing the snapshot metadata. {_REIMPORT_GUIDANCE}"
        )

    tz_str = str(snapshot.get("timezone") or "").strip()
    if not tz_str:
        raise AppleHealthIngestionError(
            f"snapshot.timezone is required. {_REIMPORT_GUIDANCE}"
        )
    tzinfo = _parse_timezone(tz_str)

    collector = str(snapshot.get("collector") or "").strip()
    if collector not in LIVE_SNAPSHOT_COLLECTORS:
        raise AppleHealthIngestionError(
            "snapshot.collector must identify a supported collector "
            f"({', '.join(sorted(LIVE_SNAPSHOT_COLLECTORS))}). {_REIMPORT_GUIDANCE}"
        )

    generated_at = _parse_aware_datetime(snapshot.get("generatedAt"), "snapshot.generatedAt")
    now_utc = (current_time or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if generated_at > now_utc + timedelta(minutes=5):
        raise AppleHealthIngestionError("snapshot.generatedAt cannot be in the future")

    coverage: dict[str, set[date]] = {}
    by_family = snapshot.get("coveredDatesByFamily")
    if by_family is not None:
        if snapshot.get("coveredDates") is not None or snapshot.get(
            "coveredMetricFamilies"
        ) is not None:
            raise AppleHealthIngestionError(
                "snapshot must use exactly one coverage encoding"
            )
        if not isinstance(by_family, dict) or not by_family:
            raise AppleHealthIngestionError(
                "snapshot.coveredDatesByFamily must be a non-empty object"
            )
        for family, values in by_family.items():
            parsed_family = _parse_metric_families(
                [family], "snapshot.coveredDatesByFamily"
            )[0]
            if not isinstance(values, list) or not values:
                raise AppleHealthIngestionError(
                    f"snapshot.coveredDatesByFamily.{parsed_family} must be a non-empty list"
                )
            coverage[parsed_family] = {_parse_coverage_date(entry) for entry in values}
    else:
        covered_raw = snapshot.get("coveredDates")
        if not isinstance(covered_raw, list) or not covered_raw:
            raise AppleHealthIngestionError(
                f"snapshot.coveredDates must be a non-empty list of ISO dates. {_REIMPORT_GUIDANCE}"
            )
        covered_dates = {_parse_coverage_date(entry) for entry in covered_raw}
        families = _parse_metric_families(
            snapshot.get("coveredMetricFamilies"), "snapshot.coveredMetricFamilies"
        )
        coverage = {family: set(covered_dates) for family in families}

    timezone_by_pair = {
        (day, family): tz_str
        for family, dates in coverage.items()
        for day in dates
    }
    generated_at_by_pair = {
        (day, family): generated_at
        for family, dates in coverage.items()
        for day in dates
    }
    pair_timezones_raw = snapshot.get("coveredTimezonesByFamilyDate")
    if pair_timezones_raw is not None:
        if collector != "health_auto_export" or not isinstance(
            pair_timezones_raw, dict
        ):
            raise AppleHealthIngestionError(
                "snapshot.coveredTimezonesByFamilyDate is only valid for Health Auto Export"
            )
        if set(pair_timezones_raw) != set(coverage):
            raise AppleHealthIngestionError(
                "snapshot.coveredTimezonesByFamilyDate must match covered families"
            )
        for family, dates in coverage.items():
            date_map = pair_timezones_raw.get(family)
            if not isinstance(date_map, dict) or set(date_map) != {
                day.isoformat() for day in dates
            }:
                raise AppleHealthIngestionError(
                    "snapshot.coveredTimezonesByFamilyDate must match covered dates"
                )
            for day in dates:
                pair_timezone = str(date_map[day.isoformat()] or "").strip()
                _parse_timezone(pair_timezone)
                timezone_by_pair[(day, family)] = pair_timezone

    pair_freshness_raw = snapshot.get("generatedAtByFamilyDate")
    if pair_freshness_raw is not None:
        if collector != "health_auto_export" or not isinstance(
            pair_freshness_raw, dict
        ):
            raise AppleHealthIngestionError(
                "snapshot.generatedAtByFamilyDate is only valid for Health Auto Export"
            )
        if set(pair_freshness_raw) != set(coverage):
            raise AppleHealthIngestionError(
                "snapshot.generatedAtByFamilyDate must match covered families"
            )
        for family, dates in coverage.items():
            date_map = pair_freshness_raw.get(family)
            if not isinstance(date_map, dict) or set(date_map) != {
                day.isoformat() for day in dates
            }:
                raise AppleHealthIngestionError(
                    "snapshot.generatedAtByFamilyDate must match covered dates"
                )
            for day in dates:
                pair_generated_at = _parse_aware_datetime(
                    date_map[day.isoformat()],
                    "snapshot.generatedAtByFamilyDate entry",
                )
                if pair_generated_at > now_utc + timedelta(minutes=5):
                    raise AppleHealthIngestionError(
                        "snapshot.generatedAtByFamilyDate entry cannot be in the future"
                    )
                generated_at_by_pair[(day, family)] = pair_generated_at

    for family, dates in coverage.items():
        if len(dates) > MAX_COVERED_DATES_PER_FAMILY:
            raise AppleHealthIngestionError(
                f"snapshot coverage for {family} must contain at most "
                f"{MAX_COVERED_DATES_PER_FAMILY} dates"
            )
        for day in dates:
            pair_tz = _parse_timezone(timezone_by_pair[(day, family)])
            local_today = now_utc.astimezone(pair_tz).date()
            earliest = local_today - timedelta(days=MAX_COVERAGE_AGE_DAYS)
            latest = local_today + timedelta(days=MAX_COVERAGE_FUTURE_DAYS)
            if day < earliest or day > latest:
                raise AppleHealthIngestionError(
                    "snapshot covered dates must be within 30 days before ingestion "
                    "and no more than 1 day in the future"
                )

    return SnapshotMeta(
        tzinfo=tzinfo,
        timezone_str=tz_str,
        collector=collector,
        generated_at=generated_at,
        generated_at_by_pair=generated_at_by_pair,
        coverage=coverage,
        timezone_by_pair=timezone_by_pair,
        advance_identical_freshness=(
            collector != "health_auto_export"
            or snapshot.get("generatedAtProvenance") != "receipt"
        ),
    )


def verify_apple_health_token(provided_token: str | None, expected_token: str) -> bool:
    """Validate the per-user token sent by an iOS Shortcut."""
    if not provided_token or not expected_token:
        return False

    return hmac.compare_digest(provided_token, expected_token)


async def ensure_apple_health_sync(
    pool: Any,
    *,
    user_id: int,
    sync_frequency_hours: int = 6,
) -> dict[str, str]:
    """Create or rotate Apple Health sync credentials for a user."""
    token = secrets.token_urlsafe(32)
    row = await pool.fetchrow(
        """INSERT INTO apple_health_sync (user_id, secret_key, sync_frequency_hours, is_active)
           VALUES ($1, $2, $3, TRUE)
           ON CONFLICT (user_id) DO UPDATE
           SET secret_key = EXCLUDED.secret_key,
               is_active = TRUE,
               sync_frequency_hours = EXCLUDED.sync_frequency_hours,
               updated_at = NOW()
           RETURNING secret_key""",
        user_id,
        token,
        sync_frequency_hours,
    )
    return {"secret_key": row["secret_key"]}


def _parse_datetime(value: str, field_name: str) -> datetime:
    if not value:
        raise AppleHealthIngestionError(f"{field_name} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AppleHealthIngestionError(f"{field_name} must be ISO 8601") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_datetime_with_local_date(
    value: str, field_name: str
) -> tuple[datetime, date | None]:
    if not value:
        raise AppleHealthIngestionError(f"{field_name} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AppleHealthIngestionError(f"{field_name} must be ISO 8601") from exc
    local_date = parsed.date() if parsed.tzinfo is not None else None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc), local_date


def _normalize_numeric_text(raw: str) -> str:
    """Extract the numeric part of a Shortcuts-rendered quantity string.

    The Shortcut inserts the health sample's Value property as localized
    text, which can carry a unit suffix ("434 count"), grouping spaces
    ("5 037", regular or non-breaking), or a decimal comma ("68,5"). A comma
    followed by exactly three digits is ambiguous grouping (5,037 could be
    5037 or 5.037) and is left untouched so it fails validation instead of
    silently changing magnitude.
    """
    match = re.match(r"\s*(-?\d[\d\u00a0\u202f .,]*)", raw)
    if not match:
        return raw
    number = (
        match.group(1)
        .replace("\u00a0", "")
        .replace("\u202f", "")
        .replace(" ", "")
        .rstrip(".,")
    )
    if re.fullmatch(r"-?\d+,\d{1,2}", number):
        number = number.replace(",", ".")
    return number


def _parse_decimal(
    value: Any,
    field_name: str,
    *,
    allow_quantity_suffix: bool = True,
) -> Decimal:
    if isinstance(value, str) and allow_quantity_suffix:
        value = _normalize_numeric_text(value)
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise AppleHealthIngestionError(f"{field_name} must be numeric") from exc
    if not parsed.is_finite():
        raise AppleHealthIngestionError(f"{field_name} must be finite")
    return parsed


def _normalize_metric_value_and_unit(
    value: Any,
    metric_type: str,
    unit: Any,
) -> tuple[Decimal, str]:
    """Return a value in the canonical unit for a supported metric family.

    Health Auto Export applies user-selected unit preferences to exported
    values. Aggregation must therefore normalize known convertible units and
    reject incompatible ones instead of silently labeling, for example, kJ as
    kcal. Unknown metric types are diagnostic-only and keep their supplied
    unit because they are not persisted as aggregates.
    """
    raw_unit = str(unit or "").strip()
    if not raw_unit:
        raise AppleHealthIngestionError("metric unit is required")
    if len(raw_unit) > 50:
        raise AppleHealthIngestionError("metric unit must be at most 50 characters")

    parsed = _parse_decimal(value, "metric value")
    family = metric_family_for_type(metric_type)
    if family is None:
        return parsed, raw_unit
    if parsed < 0:
        raise AppleHealthIngestionError("metric value must be non-negative")

    unit_key = re.sub(r"\s+", "", raw_unit.casefold())
    canonical_unit = _UNIT_ALIASES_BY_FAMILY[family].get(unit_key)
    if canonical_unit is None:
        raise AppleHealthIngestionError(
            f"metric unit is not supported for {family}"
        )
    if family == METRIC_FAMILY_ACTIVE_ENERGY and canonical_unit == "kJ":
        parsed /= Decimal("4.184")
        canonical_unit = "kcal"

    if parsed > MAX_METRIC_VALUE_BY_FAMILY[family]:
        raise AppleHealthIngestionError(
            f"metric value exceeds the supported range for {family}"
        )
    return parsed, canonical_unit


async def _get_active_sync(pool: Any, telegram_user_id: int) -> dict[str, Any]:
    row = await pool.fetchrow(
        """SELECT u.id AS user_id,
                  ahs.id AS sync_id,
                  ahs.secret_key
           FROM users u
           JOIN apple_health_sync ahs ON ahs.user_id = u.id
           WHERE u.telegram_user_id = $1
                 AND ahs.is_active = TRUE""",
        telegram_user_id,
    )
    if not row:
        raise AppleHealthIngestionError("Apple Health sync is not active for this user")
    return dict(row)


async def get_apple_health_sync_for_observability(
    pool: Any,
    telegram_user_id: int,
) -> dict[str, Any] | None:
    row = await pool.fetchrow(
        """SELECT u.id AS user_id,
                  ahs.id AS sync_id,
                  ahs.secret_key
           FROM users u
           JOIN apple_health_sync ahs ON ahs.user_id = u.id
           WHERE u.telegram_user_id = $1""",
        telegram_user_id,
    )
    return dict(row) if row else None


def _metric_key(metric_type: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", metric_type.strip().lower()).strip("_")
    aliases = {
        "active_energy_burned": "active_energy",
        "active_calories": "active_energy",
        "step_count": "step_count",
        "steps": "step_count",
        "heart_rate": "heart_rate",
        "sleep_analysis": "sleep_analysis",
        "heart_rate_variability_sdnn": "heart_rate_variability",
        "hrv": "heart_rate_variability",
        "hrv_sdnn": "heart_rate_variability",
    }
    return aliases.get(key, key)


def _is_metric(metric_type: str, candidates: set[str]) -> bool:
    key = _metric_key(metric_type)
    compact = key.replace("_", "")
    return key in candidates or compact in candidates


def metric_family_for_type(metric_type: str) -> str | None:
    if _is_metric(metric_type, _STEP_METRICS):
        return METRIC_FAMILY_STEPS
    if _is_metric(metric_type, _ACTIVE_ENERGY_METRICS):
        return METRIC_FAMILY_ACTIVE_ENERGY
    if _is_metric(metric_type, _HEART_RATE_METRICS):
        return METRIC_FAMILY_HEART_RATE
    if _is_metric(metric_type, _HRV_METRICS):
        return METRIC_FAMILY_HRV
    if _is_metric(metric_type, _SLEEP_METRICS):
        return METRIC_FAMILY_SLEEP
    return None


def _to_float(value: Any) -> float:
    if isinstance(value, Decimal):
        return float(value)
    return float(value or 0)


def _format_number(value: float) -> str:
    rounded = round(value)
    if abs(value - rounded) < 0.05:
        return str(rounded)
    return f"{value:.1f}"


def _sleep_duration_seconds(row: Any, value: float, unit: str) -> float:
    if row.get("metric_subtype") == "auto_export":
        if unit in {"h", "hr", "hour", "hours"}:
            return value * 3600
        if unit in {"m", "min", "minute", "minutes"}:
            return value * 60
        if unit in {"s", "sec", "second", "seconds"}:
            return value
    duration_seconds = row["duration_seconds"]
    if duration_seconds:
        return float(duration_seconds)
    if unit in {"h", "hr", "hour", "hours"}:
        return value * 3600
    if unit in {"m", "min", "minute", "minutes"}:
        return value * 60
    if unit in {"s", "sec", "second", "seconds"}:
        return value
    return 0.0


def _merged_interval_seconds(intervals: list[tuple[datetime, datetime]]) -> float:
    """Total seconds covered by the union of possibly-overlapping intervals.

    Apple Health reports sleep as overlapping samples (an "In Bed" envelope
    plus Core/REM/Deep stage segments, sometimes from both iPhone and Watch);
    summing them would double-count the night.
    """
    total = 0.0
    merged_start: datetime | None = None
    merged_end: datetime | None = None
    for start, end in sorted(intervals):
        if merged_end is None or start > merged_end:
            if merged_start is not None and merged_end is not None:
                total += (merged_end - merged_start).total_seconds()
            merged_start, merged_end = start, end
        elif end > merged_end:
            merged_end = end
    if merged_start is not None and merged_end is not None:
        total += (merged_end - merged_start).total_seconds()
    return total


def _merged_intervals(
    intervals: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    merged: list[list[datetime]] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        elif end > merged[-1][1]:
            merged[-1][1] = end
    return [(start, end) for start, end in merged]


def _interval_difference_seconds(
    base: list[tuple[datetime, datetime]],
    subtract: list[tuple[datetime, datetime]],
) -> float:
    """Seconds in the union of ``base`` that are not covered by ``subtract``."""
    base_merged = _merged_intervals(base)
    subtract_merged = _merged_intervals(subtract)
    total = 0.0
    for base_start, base_end in base_merged:
        cursor = base_start
        for sub_start, sub_end in subtract_merged:
            if sub_end <= cursor:
                continue
            if sub_start >= base_end:
                break
            if sub_start > cursor:
                total += (min(sub_start, base_end) - cursor).total_seconds()
            cursor = max(cursor, sub_end)
            if cursor >= base_end:
                break
        if cursor < base_end:
            total += (base_end - cursor).total_seconds()
    return total


def _sleep_stage(metric: dict[str, Any]) -> tuple[str, str]:
    raw_stage = (metric.get("additional_data") or {}).get("stage")
    if raw_stage is None or str(raw_stage).strip() == "":
        return "asleep", "Asleep"
    normalized = re.sub(r"[\W_]+", "", str(raw_stage).casefold())
    if normalized in {"0", "inbed", "bed", "уліжку"}:
        return "in_bed", "In Bed"
    if normalized in {
        "2",
        "awake",
        "безсну",
        "часбезсну",
        "неспання",
        "пробудження",
    }:
        return "awake", "Awake"
    stage_labels = {
        "1": "Asleep",
        "3": "Core",
        "4": "Deep",
        "5": "REM",
        "asleep": "Asleep",
        "asleepunspecified": "Asleep",
        "unspecified": "Asleep",
        "core": "Core",
        "asleepcore": "Core",
        "deep": "Deep",
        "asleepdeep": "Deep",
        "rem": "REM",
        "asleeprem": "REM",
        "сон": "Asleep",
        "увісні": "Asleep",
        "основнийсон": "Core",
        "основний": "Core",
        "повільнийсон": "Core",
        "повільний": "Core",
        "глибокийсон": "Deep",
        "глибокий": "Deep",
        "швидкийсон": "REM",
        "швидкий": "REM",
    }
    label = stage_labels.get(normalized)
    if label is None:
        return "unknown", "Unknown"
    return "asleep", label


def _new_metric_family_accumulator() -> dict[str, Any]:
    return {
        "values": [],
        "total": 0.0,
        "counts": {},
        "received": 0,
        "aggregated": 0,
        "asleep_intervals": [],
        "in_bed_intervals": [],
        "awake_intervals": [],
        "sleep_stage_intervals": {},
        "unknown_sleep_stages": 0,
    }


def aggregate_metric_families_by_day(
    normalized_metrics: list[dict[str, Any]],
    *,
    tz: Any,
    coverage: dict[str, set[date]],
) -> dict[tuple[date, str], dict[str, Any]]:
    """Aggregate only explicitly covered metric-family/date pairs."""
    groups = {
        (day, family): _new_metric_family_accumulator()
        for family, dates in coverage.items()
        for day in dates
    }
    for metric in normalized_metrics:
        metric_type = str(metric["metric_type"])
        family = metric_family_for_type(metric_type)
        if family is None:
            continue
        day = attribution_date_for_metric(metric, tz)
        key = (day, family)
        if key not in groups:
            raise AppleHealthIngestionError(
                f"snapshot carries a {family} sample for {day.isoformat()} outside "
                f"its declared coverage. {_REIMPORT_GUIDANCE}"
            )

        acc = groups[key]
        metric_key = _metric_key(metric_type)
        value = _to_float(metric["value"])
        unit = str(metric["unit"] or "").lower()
        acc["received"] += 1
        acc["counts"][metric_key] = acc["counts"].get(metric_key, 0) + 1

        if family == METRIC_FAMILY_SLEEP:
            duration = min(
                _sleep_duration_seconds(metric, value, unit),
                _MAX_SLEEP_SAMPLE_SECONDS,
            )
            if duration <= 0:
                continue
            start = metric["recorded_at"]
            end = start + timedelta(seconds=duration)
            kind, label = _sleep_stage(metric)
            if kind == "unknown":
                acc["unknown_sleep_stages"] += 1
                continue
            if kind == "awake":
                acc["awake_intervals"].append((start, end))
            elif kind == "in_bed":
                acc["in_bed_intervals"].append((start, end))
            else:
                acc["asleep_intervals"].append((start, end))
                acc["sleep_stage_intervals"].setdefault(label, []).append((start, end))
            acc["aggregated"] += 1
            continue

        acc["aggregated"] += 1
        if family in {METRIC_FAMILY_STEPS, METRIC_FAMILY_ACTIVE_ENERGY}:
            acc["total"] += value
        else:
            acc["values"].append(value)
    return groups


def _finalize_metric_family(family: str, acc: dict[str, Any]) -> dict[str, Any]:
    total_value = Decimal("0")
    average_value: Decimal | None = None
    sample_count = 0
    details: dict[str, Any] = {"records_by_type": dict(acc["counts"])}

    if family == METRIC_FAMILY_STEPS:
        total_value = Decimal(str(int(round(acc["total"]))))
    elif family == METRIC_FAMILY_ACTIVE_ENERGY:
        total_value = Decimal(str(round(acc["total"], 2)))
    elif family in {METRIC_FAMILY_HEART_RATE, METRIC_FAMILY_HRV}:
        values = acc["values"]
        sample_count = len(values)
        if values:
            average_value = Decimal(str(round(sum(values) / len(values), 2)))
    elif family == METRIC_FAMILY_SLEEP:
        asleep = acc["asleep_intervals"]
        if asleep:
            seconds = _merged_interval_seconds(asleep)
            basis = "asleep_stages"
        elif acc["in_bed_intervals"]:
            seconds = _interval_difference_seconds(
                acc["in_bed_intervals"], acc["awake_intervals"]
            )
            basis = "in_bed_minus_awake"
        else:
            seconds = 0.0
            basis = "no_asleep_intervals"
        total_value = Decimal(str(int(round(seconds))))
        sample_count = acc["aggregated"]
        details["sleep_basis"] = basis
        details["stage_seconds"] = {
            label: int(round(_merged_interval_seconds(intervals)))
            for label, intervals in sorted(acc["sleep_stage_intervals"].items())
        }
        if acc["unknown_sleep_stages"]:
            details["unknown_stage_samples"] = acc["unknown_sleep_stages"]

    return {
        "total_value": total_value,
        "average_value": average_value,
        "sample_count": sample_count,
        "samples_received": acc["received"],
        "samples_aggregated": acc["aggregated"],
        "details": details,
    }


async def get_apple_health_summary(
    pool: Any,
    user_id: int,
    *,
    start_at: datetime,
    end_at: datetime,
) -> dict[str, Any]:
    """Overlay v3, legacy daily, and raw Apple Health data per date/family.

    During the expand/migrate/contract rollout, one requested calendar day may
    be represented by several storage generations. Presence is authoritative,
    including an explicit zero: v3 metric-family rows win first, legacy daily
    rows fill missing families, and raw rows are aggregated only for pairs still
    absent. The v3 query chooses one freshest collector per date/family so
    native Shortcut and Health Auto Export snapshots are never summed.
    """
    sql_start = (start_at - timedelta(days=2)).date()
    sql_end = (end_at + timedelta(days=2)).date()
    v3_rows = await pool.fetch(
        """SELECT metric_date, metric_family, timezone, total_value,
                  average_value, sample_count, samples_received, metrics,
                  snapshot_generated_at, updated_at
           FROM health_daily_metric_aggregates
           WHERE user_id = $1
                 AND source = 'apple_health'
                 AND collector NOT IN ('legacy_daily', 'legacy_backfill')
                 AND metric_date >= $2
                 AND metric_date <= $3
           ORDER BY metric_date, metric_family,
                    snapshot_generated_at DESC, updated_at DESC, collector ASC""",
        user_id,
        sql_start,
        sql_end,
    )
    legacy_rows = await pool.fetch(
        """SELECT metric_date, timezone, steps, active_energy_kcal,
                  avg_heart_rate, heart_rate_samples, avg_hrv_ms, hrv_samples,
                  sleep_seconds, metrics, snapshot_generated_at, updated_at
           FROM health_daily_aggregates
           WHERE user_id = $1
                 AND source = 'apple_health'
                 AND metric_date >= $2
                 AND metric_date <= $3
           ORDER BY metric_date ASC""",
        user_id,
        sql_start,
        sql_end,
    )
    transitional_rows = await pool.fetch(
        """SELECT metric_date, metric_family, timezone, total_value,
                  average_value, sample_count, samples_received, metrics,
                  snapshot_generated_at, updated_at
           FROM health_daily_metric_aggregates
           WHERE user_id = $1
                 AND source = 'apple_health'
                 AND collector IN ('legacy_daily', 'legacy_backfill')
                 AND metric_date >= $2
                 AND metric_date <= $3
           ORDER BY metric_date, metric_family,
                    snapshot_generated_at DESC, updated_at DESC, collector ASC""",
        user_id,
        sql_start,
        sql_end,
    )
    raw_rows = await pool.fetch(
        """SELECT metric_type, metric_subtype, value, unit, recorded_at,
                  duration_seconds, additional_data, created_at
           FROM health_data
           WHERE user_id = $1
                 AND source = 'apple_health'
                 AND recorded_at >= $2
                 AND recorded_at < $3
           ORDER BY recorded_at ASC""",
        user_id,
        start_at - timedelta(days=2),
        end_at + timedelta(days=1),
    )

    def _in_window(metric_date: date, timezone_str: str) -> bool:
        try:
            row_tz = _parse_timezone(timezone_str)
        except AppleHealthIngestionError:
            row_tz = timezone.utc
        if end_at <= start_at:
            return False
        # A processed row represents a whole local calendar day and cannot be
        # prorated against a differently-zoned query window. Anchor it at local
        # noon: adjacent rows cannot both enter a one-day window merely because
        # that window crosses midnight in the row timezone, while fixed-offset
        # HAE rows remain visible across a one-hour DST boundary mismatch.
        row_anchor = datetime(
            metric_date.year,
            metric_date.month,
            metric_date.day,
            12,
            tzinfo=row_tz,
        ).astimezone(timezone.utc)
        return start_at <= row_anchor < end_at

    def _metrics_blob(value: Any) -> dict[str, Any]:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                return {}
        return value if isinstance(value, dict) else {}

    # (local date, family) -> normalized processed-family row.
    selected: dict[tuple[date, str], dict[str, Any]] = {}
    for row in v3_rows:
        family = str(row["metric_family"])
        metric_date = row["metric_date"]
        timezone_str = str(row["timezone"])
        if family not in SUPPORTED_METRIC_FAMILIES or not _in_window(
            metric_date, timezone_str
        ):
            continue
        key = (metric_date, family)
        if key in selected:
            # SQL freshness order is descending, so the first in-window live
            # collector is authoritative for this date/family.
            continue
        selected[key] = {
            "family": family,
            "total": row["total_value"],
            "average": row["average_value"],
            "sample_count": int(row["sample_count"] or 0),
            "metrics": _metrics_blob(row["metrics"]),
            "marker": row["snapshot_generated_at"] or row["updated_at"],
        }

    legacy_columns = {
        METRIC_FAMILY_STEPS: ("steps", None, 0),
        METRIC_FAMILY_ACTIVE_ENERGY: ("active_energy_kcal", None, 0),
        METRIC_FAMILY_HEART_RATE: (None, "avg_heart_rate", "heart_rate_samples"),
        METRIC_FAMILY_HRV: (None, "avg_hrv_ms", "hrv_samples"),
        METRIC_FAMILY_SLEEP: ("sleep_seconds", None, 0),
    }
    for row in legacy_rows:
        metric_date = row["metric_date"]
        timezone_str = str(row["timezone"])
        if not _in_window(metric_date, timezone_str):
            continue
        marker = row["snapshot_generated_at"] or row["updated_at"]
        for family, (total_column, average_column, count_column) in legacy_columns.items():
            key = (metric_date, family)
            if key in selected:
                continue
            selected[key] = {
                "family": family,
                "total": row[total_column] if total_column else 0,
                "average": row[average_column] if average_column else None,
                "sample_count": int(row[count_column] or 0) if count_column else 0,
                "metrics": _metrics_blob(row["metrics"]),
                "marker": marker,
            }

    for row in transitional_rows:
        family = str(row["metric_family"])
        metric_date = row["metric_date"]
        timezone_str = str(row["timezone"])
        key = (metric_date, family)
        if (
            key in selected
            or family not in SUPPORTED_METRIC_FAMILIES
            or not _in_window(metric_date, timezone_str)
        ):
            continue
        selected[key] = {
            "family": family,
            "total": row["total_value"],
            "average": row["average_value"],
            "sample_count": int(row["sample_count"] or 0),
            "metrics": _metrics_blob(row["metrics"]),
            "marker": row["snapshot_generated_at"] or row["updated_at"],
        }

    raw_tz = ZoneInfo("Europe/Kyiv")
    normalized_raw: list[dict[str, Any]] = []
    raw_coverage: dict[str, set[date]] = {}
    raw_markers: dict[tuple[date, str], datetime] = {}
    for row in raw_rows:
        metric_type = str(row["metric_type"])
        family = metric_family_for_type(metric_type)
        if family is None:
            continue
        try:
            metric_value, metric_unit = _normalize_metric_value_and_unit(
                row["value"], metric_type, row["unit"]
            )
        except AppleHealthIngestionError:
            logger.warning(
                "Skipping invalid legacy Apple Health raw metric during read: "
                "type=%s",
                metric_type,
            )
            continue
        additional_data = row["additional_data"]
        if isinstance(additional_data, str):
            try:
                additional_data = json.loads(additional_data)
            except ValueError:
                additional_data = {}
        metric = {
            "metric_type": metric_type,
            "metric_subtype": row["metric_subtype"],
            "value": metric_value,
            "unit": metric_unit,
            "recorded_at": row["recorded_at"],
            "duration_seconds": row["duration_seconds"],
            "additional_data": additional_data if isinstance(additional_data, dict) else {},
        }
        metric_date = attribution_date_for_metric(metric, raw_tz)
        if not _in_window(metric_date, "Europe/Kyiv"):
            continue
        normalized_raw.append(metric)
        raw_coverage.setdefault(family, set()).add(metric_date)
        marker = row["created_at"] or row["recorded_at"]
        pair = (metric_date, family)
        if raw_markers.get(pair) is None or marker > raw_markers[pair]:
            raw_markers[pair] = marker

    raw_groups = aggregate_metric_families_by_day(
        normalized_raw,
        tz=raw_tz,
        coverage=raw_coverage,
    )
    for (metric_date, family), accumulator in raw_groups.items():
        key = (metric_date, family)
        if key in selected:
            continue
        columns = _finalize_metric_family(family, accumulator)
        selected[key] = {
            "family": family,
            "total": columns["total_value"],
            "average": columns["average_value"],
            "sample_count": columns["sample_count"],
            "metrics": columns["details"],
            "marker": raw_markers.get(key),
        }

    steps = 0.0
    active_energy = 0.0
    hr_weighted = 0.0
    hr_samples = 0
    hrv_weighted = 0.0
    hrv_samples = 0
    sleep_seconds = 0.0
    counts: dict[str, int] = {}
    latest_metric_at = None

    for row in selected.values():
        family = row["family"]
        if family == METRIC_FAMILY_STEPS:
            steps += _to_float(row["total"])
        elif family == METRIC_FAMILY_ACTIVE_ENERGY:
            active_energy += _to_float(row["total"])
        elif family == METRIC_FAMILY_SLEEP:
            sleep_seconds += _to_float(row["total"])
        elif family == METRIC_FAMILY_HEART_RATE:
            sample_count = int(row["sample_count"] or 0)
            if sample_count and row["average"] is not None:
                hr_weighted += _to_float(row["average"]) * sample_count
                hr_samples += sample_count
        elif family == METRIC_FAMILY_HRV:
            sample_count = int(row["sample_count"] or 0)
            if sample_count and row["average"] is not None:
                hrv_weighted += _to_float(row["average"]) * sample_count
                hrv_samples += sample_count

        by_type = row["metrics"].get("records_by_type")
        if isinstance(by_type, dict):
            for metric_type, count in by_type.items():
                if metric_family_for_type(str(metric_type)) == family:
                    counts[str(metric_type)] = counts.get(str(metric_type), 0) + int(count)

        marker = row["marker"]
        if marker is not None and (latest_metric_at is None or marker > latest_metric_at):
            latest_metric_at = marker

    avg_heart_rate = round(hr_weighted / hr_samples) if hr_samples else 0
    avg_hrv_ms = round(hrv_weighted / hrv_samples) if hrv_samples else 0
    sleep_hours = round(sleep_seconds / 3600, 1) if sleep_seconds else 0
    active_energy_kcal = round(active_energy)
    total_steps = round(steps)

    parts = []
    if total_steps:
        parts.append(f"Apple Health steps: {total_steps}")
    if active_energy_kcal:
        parts.append(f"Apple Health active energy: {active_energy_kcal} kcal")
    if avg_heart_rate:
        parts.append(f"Apple Health average heart rate: {avg_heart_rate} bpm")
    if sleep_hours:
        parts.append(f"Apple Health sleep: {_format_number(sleep_hours)}h")
    if avg_hrv_ms:
        parts.append(f"Apple Health HRV (stress proxy): {avg_hrv_ms} ms")

    return {
        "steps": total_steps,
        "active_energy_kcal": active_energy_kcal,
        "avg_heart_rate": avg_heart_rate,
        "avg_hrv_ms": avg_hrv_ms,
        "sleep_hours": sleep_hours,
        "metric_counts": counts,
        "latest_metric_at": latest_metric_at,
        "summary": ". ".join(parts),
    }


def _sanitized_request_summary(payload: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in ("sourceType", "dataType", "syncTimestamp"):
        value = payload.get(key)
        if isinstance(value, str):
            summary[key] = value[:128]
    metrics = payload.get("metrics")
    summary["metrics_count"] = len(metrics) if isinstance(metrics, list) else 0
    return summary


def _bounded_persisted_error(error_message: str | None) -> str | None:
    if error_message is None:
        return None
    normalized = " ".join(str(error_message).split())
    return normalized[:MAX_PERSISTED_ERROR_MESSAGE_CHARS] or "Apple Health sync failed"


async def record_apple_health_import_log(
    pool: Any,
    *,
    user_id: int,
    sync_id: int | None,
    http_status: int,
    records_received: int = 0,
    records_processed: int = 0,
    records_failed: int = 0,
    records_skipped: int = 0,  # accepted for backward compatibility; not persisted
    error_message: str | None = None,
    request_summary: dict[str, Any] | None = None,
    response_summary: dict[str, Any] | None = None,
) -> None:
    # The daily-aggregate model has no per-sample "skip" concept, so the
    # response_summary carries the full accounting and the base 007 columns are
    # written (the records_skipped column from removed migration 008 no longer
    # exists).
    error_message = _bounded_persisted_error(error_message)
    await pool.execute(
        """INSERT INTO apple_health_import_logs
               (user_id, sync_id, http_status, records_received, records_processed,
                records_failed, error_message, request_body, response_body)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb)""",
        user_id,
        sync_id,
        http_status,
        records_received,
        records_processed,
        records_failed,
        error_message,
        json.dumps(request_summary or {}),
        json.dumps(response_summary or {}),
    )


async def record_apple_health_failure(
    pool: Any,
    *,
    user_id: int,
    sync_id: int | None,
    http_status: int,
    error_message: str,
    records_received: int = 0,
    records_processed: int = 0,
    records_failed: int = 0,
    records_skipped: int = 0,
    request_summary: dict[str, Any] | None = None,
) -> None:
    error_message = _bounded_persisted_error(error_message) or "Apple Health sync failed"
    if sync_id is not None:
        await pool.execute(
            """UPDATE apple_health_sync
               SET error_count = error_count + 1,
                   last_error_message = $2,
                   updated_at = NOW()
               WHERE id = $1""",
            sync_id,
            error_message,
        )
    await record_apple_health_import_log(
        pool,
        user_id=user_id,
        sync_id=sync_id,
        http_status=http_status,
        records_received=records_received,
        records_processed=records_processed,
        records_failed=records_failed,
        records_skipped=records_skipped,
        error_message=error_message,
        request_summary=request_summary,
        response_summary={"error": error_message},
    )


def _normalize_hae_timestamp(raw: str) -> str:
    """Convert Health Auto Export date format to ISO 8601.

    HAE typically emits ``"2026-05-20 14:30:00 +0300"``; convert to
    ``"2026-05-20T14:30:00+03:00"`` so ``datetime.fromisoformat`` accepts it.
    """
    s = (raw or "").strip()
    if not s:
        return s
    if " " in s and "T" not in s:
        s = s.replace(" ", "T", 1)
    if " +" in s or " -" in s:
        time_part, _, tz = s.rpartition(" ")
        if (
            len(tz) == 5
            and tz[0] in "+-"
            and tz[1:].isdigit()
        ):
            tz = f"{tz[:3]}:{tz[3:]}"
        s = f"{time_part}{tz}"
    return s


def is_health_auto_export_payload(payload: Any) -> bool:
    """Detect the Health Auto Export iOS app JSON shape."""
    if not isinstance(payload, dict):
        return False
    data = payload.get("data")
    if not isinstance(data, dict):
        return False
    metrics = data.get("metrics")
    return isinstance(metrics, list)


def convert_health_auto_export(
    payload: dict[str, Any],
    *,
    telegram_user_id: int,
    automation_period: str,
    snapshot_timezone: str,
    snapshot_generated_at: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Flatten Health Auto Export JSON into the schema-v3 ingestion shape.

    HAE shape:
        {"data": {"metrics": [{"name", "units", "data": [{"date","qty",...}]}]}}
    Returns:
        The route admits exactly one complete, unbatched, unaggregated metric
        per automation. Coverage is derived from the attested HAE period in the
        configured snapshot timezone, so an omitted day is represented as an
        authoritative zero instead of silently preserving a stale total.

        HAE does not send a causal export timestamp, so the caller must supply
        an offset-aware snapshot_generated_at from a timestamp-producing
        wrapper. Source sample timestamps are mutable and cannot order exports:
        deleting the newest HealthKit record makes a newer snapshot's maximum
        sample time older than a delayed pre-deletion snapshot.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    hae_metrics = data.get("metrics", []) if isinstance(data, dict) else []
    if not isinstance(hae_metrics, list) or len(hae_metrics) != 1:
        raise AppleHealthIngestionError(
            "Health Auto Export must send exactly one metric per automation"
        )
    hae_metric = hae_metrics[0]
    if not isinstance(hae_metric, dict):
        raise AppleHealthIngestionError(
            "Health Auto Export metric must be an object"
        )
    name = str(hae_metric.get("name") or "").strip()
    family = metric_family_for_type(name)
    if family is None:
        raise AppleHealthIngestionError(
            "Health Auto Export automation must select one supported metric"
        )
    points = hae_metric.get("data")
    if not isinstance(points, list):
        raise AppleHealthIngestionError(
            "Health Auto Export metric data must be a list"
        )

    timezone_str = str(snapshot_timezone or "").strip()
    tzinfo = _parse_timezone(timezone_str)
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    local_today = current_time.astimezone(tzinfo).date()
    normalized_period = "".join(
        char for char in str(automation_period or "").strip().lower() if char.isalnum()
    )
    if normalized_period == "today":
        period_dates = {local_today}
    elif normalized_period == "yesterday":
        period_dates = {local_today - timedelta(days=1)}
    elif normalized_period == "previous7days":
        period_dates = {
            local_today - timedelta(days=days_ago) for days_ago in range(1, 8)
        }
    elif normalized_period in {"none", "default"}:
        period_dates = {local_today - timedelta(days=1), local_today}
    else:
        raise AppleHealthIngestionError(
            "Health Auto Export automation-period is not supported"
        )

    if family == METRIC_FAMILY_SLEEP:
        for point in points:
            if not isinstance(point, dict) or not all(
                str(point.get(field) or "").strip()
                for field in ("startDate", "endDate", "value")
            ):
                raise AppleHealthIngestionError(
                    "Health Auto Export sleep must be unaggregated segments"
                )

    flat: list[dict[str, Any]] = []
    units = str(hae_metric.get("units") or "").strip() or "unknown"
    for point in points:
        if not isinstance(point, dict):
            raise AppleHealthIngestionError(
                "Health Auto Export metric point must be an object"
            )
        is_sleep = family == METRIC_FAMILY_SLEEP
        raw_ts = point.get("date") or point.get("startDate") or ""
        value = point.get("qty")
        stage = None
        end_raw = None
        if is_sleep:
            raw_ts = point.get("startDate") or raw_ts
            end_raw = point.get("endDate")
            raw_stage = point.get("value")
            if raw_stage is not None and str(raw_stage).strip():
                stage = str(raw_stage).strip()
        if value is None or not str(raw_ts).strip():
            raise AppleHealthIngestionError(
                "Health Auto Export metric point requires qty and timestamp"
            )
        converted = {
            "type": name,
            "value": value,
            "unit": units,
            "timestamp": _normalize_hae_timestamp(str(raw_ts)),
        }
        if end_raw:
            converted["end"] = _normalize_hae_timestamp(str(end_raw))
        if stage:
            converted["stage"] = stage
        flat.append(converted)

    family_key = str(family)
    coverage = {family_key: {day.isoformat() for day in period_dates}}
    coverage_timezones = {
        family_key: {day.isoformat(): timezone_str for day in period_dates}
    }
    generated_at = _parse_aware_datetime(
        snapshot_generated_at,
        "X-Health-Tracker-Generated-At",
    )
    generated_at_by_family_date = {
        family_name: {
            day: generated_at.isoformat() for day in sorted(dates)
        }
        for family_name, dates in sorted(coverage.items())
    }

    return {
        "sourceType": "apple_health",
        "schemaVersion": SNAPSHOT_SCHEMA_VERSION,
        "dataType": "auto_export",
        "userId": telegram_user_id,
        "snapshot": {
            "collector": "health_auto_export",
            "timezone": timezone_str,
            "generatedAt": generated_at.isoformat(),
            "generatedAtProvenance": "export",
            "generatedAtByFamilyDate": generated_at_by_family_date,
            "coveredDatesByFamily": {
                family: sorted(dates) for family, dates in sorted(coverage.items())
            },
            "coveredTimezonesByFamilyDate": coverage_timezones,
        },
        "metrics": flat,
    }


def _validate_metric_container(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if payload.get("sourceType") != "apple_health":
        raise AppleHealthIngestionError("sourceType must be apple_health")

    metrics = payload.get("metrics")
    if not isinstance(metrics, list):
        raise AppleHealthIngestionError("metrics must be a list")
    if len(metrics) > MAX_METRICS_PER_SNAPSHOT:
        raise AppleHealthIngestionError(
            f"metrics must contain at most {MAX_METRICS_PER_SNAPSHOT} items"
        )

    return metrics


def _normalize_metric(
    metric: dict[str, Any],
    *,
    data_type: str | None,
    current_time: datetime,
) -> dict[str, Any]:
    if not isinstance(metric, dict):
        raise AppleHealthIngestionError("metric must be an object")
    metric_type = str(metric.get("type") or "").strip()
    unit = str(metric.get("unit") or "").strip()
    if not metric_type:
        raise AppleHealthIngestionError("metric type is required")
    if not unit:
        raise AppleHealthIngestionError("metric unit is required")
    if len(metric_type) > 100:
        raise AppleHealthIngestionError("metric type must be at most 100 characters")
    if len(unit) > 50:
        raise AppleHealthIngestionError("metric unit must be at most 50 characters")

    value, unit = _normalize_metric_value_and_unit(
        metric.get("value"), metric_type, unit
    )
    recorded_at, local_recorded_date = _parse_datetime_with_local_date(
        str(metric.get("timestamp") or ""), "metric timestamp"
    )
    if recorded_at < current_time - timedelta(days=30):
        raise AppleHealthIngestionError("metric timestamp is older than 30 days")
    if recorded_at > current_time + timedelta(days=1):
        raise AppleHealthIngestionError("metric timestamp is in the future")

    duration = metric.get("duration")
    duration_seconds = None
    if duration is not None:
        parsed_duration = _parse_decimal(
            duration,
            "metric duration",
            allow_quantity_suffix=False,
        )
        if parsed_duration < 0:
            raise AppleHealthIngestionError("metric duration must be non-negative")
        if parsed_duration > _MAX_SLEEP_SAMPLE_SECONDS:
            raise AppleHealthIngestionError(
                f"metric duration must be at most {_MAX_SLEEP_SAMPLE_SECONDS} seconds"
            )
        if parsed_duration != parsed_duration.to_integral_value():
            raise AppleHealthIngestionError(
                "metric duration must be a whole number of seconds"
            )
        duration_seconds = int(parsed_duration)

    # The Shortcut cannot compute numeric durations reliably (Shortcuts renders
    # sleep Value/Duration as localized text), so interval samples send an
    # "end" ISO timestamp instead and the duration is derived here.
    local_end_date = None
    if duration_seconds is None:
        end_raw = metric.get("end") or metric.get("end_timestamp") or metric.get("endDate")
        if end_raw:
            ended_at, local_end_date = _parse_datetime_with_local_date(
                str(end_raw), "metric end"
            )
            if ended_at > recorded_at:
                duration_seconds = int((ended_at - recorded_at).total_seconds())

    additional_data = {
        key: value
        for key, value in metric.items()
        if key not in {"type", "value", "unit", "timestamp", "duration"}
    }
    return {
        "metric_type": metric_type,
        "metric_subtype": data_type,
        "value": value,
        "unit": unit,
        "recorded_at": recorded_at,
        "duration_seconds": duration_seconds,
        "additional_data": additional_data,
        "local_recorded_date": local_recorded_date,
        "local_end_date": local_end_date,
    }


def _metric_summary_label(metric_key: str) -> str:
    """Friendly label for a normalized metric key used in summaries."""
    return _METRIC_SUMMARY_LABELS.get(metric_key, metric_key.replace("_", " "))


def summarize_parsed_metrics(
    normalized_metrics: list[dict[str, Any]],
) -> tuple[dict[str, int], list[str]]:
    """Count parsed metrics per normalized type and list any unmapped types.

    Returns ``(counts_by_type, unmapped_types)`` where ``counts_by_type`` is
    keyed by the normalized metric key (e.g. ``"step_count"``) and
    ``unmapped_types`` are keys ignored because the aggregation layer has no
    supported metric family for them (see ``_SUMMARY_MAPPED_METRICS``).
    """
    counts: dict[str, int] = {}
    unmapped: list[str] = []
    for metric in normalized_metrics:
        key = _metric_key(str(metric["metric_type"]))
        counts[key] = counts.get(key, 0) + 1
        if key not in unmapped and not _is_metric(key, _SUMMARY_MAPPED_METRICS):
            unmapped.append(key)
    return counts, unmapped


def build_ingestion_summary(
    counts_by_type: dict[str, int],
    *,
    received: int,
    aggregated: int,
    failed: int,
    aggregate_rows: int,
    unmapped_types: list[str] | None = None,
) -> str:
    """Human-readable one-line summary of what a sync parsed and stored.

    Example: ``"1239 samples received, 1239 aggregated; 3 daily family rows
    updated, raw stored: 0: 515 steps, 509 active energy, 215 sleep"``. Raw
    HealthKit samples are never persisted (retention 0), so the summary reports
    samples in / processed family rows updated / raw stored 0.
    """
    breakdown_parts = [
        f"{count} {_metric_summary_label(key)}"
        for key, count in sorted(counts_by_type.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    breakdown = ", ".join(breakdown_parts) if breakdown_parts else "no metrics"

    row_word = "row" if aggregate_rows == 1 else "rows"
    summary = (
        f"{received} samples received, {aggregated} aggregated; "
        f"{aggregate_rows} daily family {row_word} updated, "
        f"raw stored: {RAW_ROWS_STORED}"
    )
    if failed:
        summary += f", {failed} failed"
    summary += f": {breakdown}"
    if unmapped_types:
        summary += f" (unsupported and not stored: {', '.join(unmapped_types)})"
    return summary


def attribution_date_for_metric(metric: dict[str, Any], tz: Any) -> date:
    """Local calendar day a normalized metric is attributed to.

    Sleep intervals are attributed to the local day they *end*; everything else
    to the local day of ``recorded_at``. Mirrors the bucketing in
    ``aggregate_metrics_by_day`` so callers (e.g. backfill) can pre-compute the
    set of covered days.
    """
    recorded_at = metric["recorded_at"]
    if _is_metric(str(metric["metric_type"]), _SLEEP_METRICS):
        value = _to_float(metric["value"])
        unit = str(metric["unit"] or "").lower()
        duration = min(
            _sleep_duration_seconds(metric, value, unit), _MAX_SLEEP_SAMPLE_SECONDS
        )
        if duration > 0:
            if metric.get("local_end_date") is not None:
                return metric["local_end_date"]
            return _local_date(recorded_at + timedelta(seconds=duration), tz)
    if metric.get("local_recorded_date") is not None:
        return metric["local_recorded_date"]
    return _local_date(recorded_at, tz)


def _new_day_accumulator() -> dict[str, Any]:
    return {
        "steps": 0.0,
        "active_energy": 0.0,
        "heart_rates": [],
        "hrv_values": [],
        "sleep_intervals": [],
        "counts": {},
        "received": 0,
        "aggregated": 0,
    }


def aggregate_metrics_by_day(
    normalized_metrics: list[dict[str, Any]],
    *,
    tz: Any,
    covered_dates: set[date],
) -> dict[date, dict[str, Any]]:
    """Aggregate normalized samples in memory, one bucket per local calendar day.

    Attribution: non-sleep metrics land on the local date of their ``recorded_at``;
    a sleep interval lands on the local date it *ends* (a night usually starts
    before midnight). Same-second distinct samples (two step counts, or an
    "Awake" and an "In Bed" segment sharing a start second) all contribute —
    there is no natural-key collision because nothing is stored per sample.
    Overlapping sleep intervals are merged (not summed) so a night is not
    double-counted.

    Every sample's attribution date MUST be in ``covered_dates``; a sample
    outside the declared coverage means a partial/ambiguous snapshot and raises
    AppleHealthIngestionError rather than being silently merged.
    """
    days: dict[date, dict[str, Any]] = {}

    def _bucket(day: date) -> dict[str, Any]:
        if day not in covered_dates:
            raise AppleHealthIngestionError(
                f"snapshot carries a {day.isoformat()} sample outside its declared "
                f"coveredDates — partial/ambiguous snapshot rejected. {_REIMPORT_GUIDANCE}"
            )
        return days.setdefault(day, _new_day_accumulator())

    for metric in normalized_metrics:
        metric_type = str(metric["metric_type"])
        key = _metric_key(metric_type)
        value = _to_float(metric["value"])
        unit = str(metric["unit"] or "").lower()
        recorded_at = metric["recorded_at"]

        if _is_metric(metric_type, _SLEEP_METRICS):
            duration = min(
                _sleep_duration_seconds(metric, value, unit),
                _MAX_SLEEP_SAMPLE_SECONDS,
            )
            if duration <= 0:
                # Received but contributes nothing (no derivable interval).
                _bucket(_local_date(recorded_at, tz))["received"] += 1
                continue
            ends_at = recorded_at + timedelta(seconds=duration)
            acc = _bucket(_local_date(ends_at, tz))
            acc["received"] += 1
            acc["aggregated"] += 1
            acc["counts"][key] = acc["counts"].get(key, 0) + 1
            acc["sleep_intervals"].append((recorded_at, ends_at))
            continue

        acc = _bucket(_local_date(recorded_at, tz))
        acc["received"] += 1
        acc["aggregated"] += 1
        acc["counts"][key] = acc["counts"].get(key, 0) + 1

        if _is_metric(metric_type, _STEP_METRICS):
            acc["steps"] += value
        elif _is_metric(metric_type, _ACTIVE_ENERGY_METRICS):
            acc["active_energy"] += value
        elif _is_metric(metric_type, _HRV_METRICS):
            acc["hrv_values"].append(value)
        elif _is_metric(metric_type, _HEART_RATE_METRICS):
            acc["heart_rates"].append(value)
        # Unmapped types still count toward the day's breakdown (counts) but roll
        # up no numeric column — surfaced via unmapped_metric_types.

    return days


def _finalize_day_columns(acc: dict[str, Any]) -> dict[str, Any]:
    """Turn a day accumulator into DB-ready column values (asyncpg types)."""
    heart_rates = acc["heart_rates"]
    hrv_values = acc["hrv_values"]
    sleep_seconds = int(round(_merged_interval_seconds(acc["sleep_intervals"])))
    avg_hr = (
        Decimal(str(round(sum(heart_rates) / len(heart_rates), 2))) if heart_rates else None
    )
    avg_hrv = (
        Decimal(str(round(sum(hrv_values) / len(hrv_values), 2))) if hrv_values else None
    )
    return {
        "steps": int(round(acc["steps"])),
        "active_energy_kcal": Decimal(str(round(acc["active_energy"], 2))),
        "avg_heart_rate": avg_hr,
        "heart_rate_samples": len(heart_rates),
        "avg_hrv_ms": avg_hrv,
        "hrv_samples": len(hrv_values),
        "sleep_seconds": sleep_seconds,
        "samples_received": acc["received"],
        "samples_aggregated": acc["aggregated"],
        "counts": acc["counts"],
    }


def _family_payload_hash(
    *,
    family: str,
    timezone_str: str,
    columns: dict[str, Any],
) -> str:
    canonical = json.dumps(
        {
            "family": family,
            "timezone": timezone_str,
            "total_value": str(columns["total_value"]),
            "average_value": (
                str(columns["average_value"])
                if columns["average_value"] is not None
                else None
            ),
            "sample_count": columns["sample_count"],
            "samples_received": columns["samples_received"],
            "samples_aggregated": columns["samples_aggregated"],
            "details": columns["details"],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@asynccontextmanager
async def _transactional_connection(database: Any):
    """Yield one transaction-scoped connection for Pool, Connection, or test fakes."""
    acquire = getattr(database, "acquire", None)
    if callable(acquire):
        async with acquire() as conn:
            async with conn.transaction():
                yield conn
        return
    transaction = getattr(database, "transaction", None)
    if callable(transaction):
        async with transaction():
            yield database
        return
    yield database


async def _upsert_metric_family(
    conn: Any,
    *,
    user_id: int,
    collector: str,
    day: date,
    family: str,
    timezone_str: str,
    generated_at: datetime,
    columns: dict[str, Any],
    advance_identical_freshness: bool = True,
) -> str:
    payload_hash = _family_payload_hash(
        family=family, timezone_str=timezone_str, columns=columns
    )
    written = await conn.fetchrow(
        """INSERT INTO health_daily_metric_aggregates
               (user_id, source, collector, metric_date, metric_family, timezone,
                total_value, average_value, sample_count, samples_received,
                samples_aggregated, metrics, snapshot_generated_at, payload_hash)
           VALUES ($1, 'apple_health', $2, $3, $4, $5, $6, $7, $8, $9, $10,
                   $11::jsonb, $12, $13)
           ON CONFLICT ON CONSTRAINT health_daily_metric_aggregates_natural_key
           DO UPDATE SET
               timezone = EXCLUDED.timezone,
               total_value = EXCLUDED.total_value,
               average_value = EXCLUDED.average_value,
               sample_count = EXCLUDED.sample_count,
               samples_received = EXCLUDED.samples_received,
               samples_aggregated = EXCLUDED.samples_aggregated,
               metrics = EXCLUDED.metrics,
               snapshot_generated_at = EXCLUDED.snapshot_generated_at,
               payload_hash = EXCLUDED.payload_hash,
               updated_at = NOW()
           WHERE EXCLUDED.snapshot_generated_at >
                 health_daily_metric_aggregates.snapshot_generated_at
             AND ($14::boolean OR EXCLUDED.payload_hash <>
                  health_daily_metric_aggregates.payload_hash)
           RETURNING id""",
        user_id,
        collector,
        day,
        family,
        timezone_str,
        columns["total_value"],
        columns["average_value"],
        columns["sample_count"],
        columns["samples_received"],
        columns["samples_aggregated"],
        json.dumps(columns["details"], sort_keys=True),
        generated_at,
        payload_hash,
        advance_identical_freshness,
    )
    if written is not None:
        return "updated"

    existing = await conn.fetchrow(
        """SELECT snapshot_generated_at, payload_hash
           FROM health_daily_metric_aggregates
           WHERE user_id = $1 AND source = 'apple_health' AND collector = $2
                 AND metric_date = $3 AND metric_family = $4""",
        user_id,
        collector,
        day,
        family,
    )
    if existing is None:
        raise AppleHealthPersistenceError(
            "Apple Health aggregate freshness check lost its target row"
        )
    existing_generated = existing["snapshot_generated_at"]
    if existing["payload_hash"] == payload_hash:
        return "replayed"
    if existing_generated == generated_at:
        raise AppleHealthSnapshotConflictError(
            "snapshot timestamp conflicts with different processed data"
        )
    return "stale"


async def _load_metric_family_columns(
    conn: Any,
    *,
    user_id: int,
    collector: str,
    day: date,
    family: str,
) -> dict[str, Any]:
    """Read the effective stored family after a stale incoming snapshot."""
    row = await conn.fetchrow(
        """SELECT total_value, average_value, sample_count, samples_received,
                  samples_aggregated, metrics
           FROM health_daily_metric_aggregates
           WHERE user_id = $1 AND source = 'apple_health' AND collector = $2
                 AND metric_date = $3 AND metric_family = $4""",
        user_id,
        collector,
        day,
        family,
    )
    if row is None:
        raise AppleHealthPersistenceError(
            "Apple Health effective aggregate row is missing"
        )
    details = row["metrics"] or {}
    if isinstance(details, str):
        details = json.loads(details)
    if not isinstance(details, dict):
        raise AppleHealthPersistenceError(
            "Apple Health effective aggregate details are invalid"
        )
    return {
        "total_value": row["total_value"],
        "average_value": row["average_value"],
        "sample_count": row["sample_count"],
        "samples_received": row["samples_received"],
        "samples_aggregated": row["samples_aggregated"],
        "details": details,
    }


def _daily_result_from_families(
    family_rows: dict[tuple[date, str], dict[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for (day, family), columns in sorted(family_rows.items()):
        entry = result.setdefault(
            day.isoformat(),
            {
                "steps": 0,
                "active_energy_kcal": 0.0,
                "avg_heart_rate": 0.0,
                "avg_hrv_ms": 0.0,
                "sleep_hours": 0.0,
                "samples_received": 0,
                "records_by_type": {},
            },
        )
        if family == METRIC_FAMILY_STEPS:
            entry["steps"] = int(columns["total_value"])
        elif family == METRIC_FAMILY_ACTIVE_ENERGY:
            entry["active_energy_kcal"] = float(columns["total_value"])
        elif family == METRIC_FAMILY_HEART_RATE:
            entry["avg_heart_rate"] = float(columns["average_value"] or 0)
        elif family == METRIC_FAMILY_HRV:
            entry["avg_hrv_ms"] = float(columns["average_value"] or 0)
        elif family == METRIC_FAMILY_SLEEP:
            entry["sleep_hours"] = round(float(columns["total_value"]) / 3600, 1)
        entry["samples_received"] += columns["samples_received"]
        for metric_type, count in columns["details"].get("records_by_type", {}).items():
            entry["records_by_type"][metric_type] = (
                entry["records_by_type"].get(metric_type, 0) + int(count)
            )
    return result


async def _record_failure_safely(database: Any, **kwargs: Any) -> None:
    try:
        await record_apple_health_failure(database, **kwargs)
    except Exception:
        logger.exception("Failed to record Apple Health failure metadata")


async def ingest_apple_health_payload(
    pool: Any,
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate, aggregate, and atomically freshness-upsert a schema-v3 snapshot."""
    received = len(payload.get("metrics", [])) if isinstance(payload.get("metrics"), list) else 0
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        telegram_user_id = int(payload["userId"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AppleHealthIngestionError("userId must be a Telegram user id") from exc

    sync = await _get_active_sync(pool, telegram_user_id)
    user_id = sync["user_id"]
    sync_id = sync["sync_id"]
    request_summary = _sanitized_request_summary(payload)

    try:
        meta = _extract_snapshot_meta(payload, current_time=current_time)
        metrics = _validate_metric_container(payload)
        normalized_metrics = [
            _normalize_metric(
                metric,
                data_type=payload.get("dataType"),
                current_time=current_time,
            )
            for metric in metrics
        ]
        family_accumulators = aggregate_metric_families_by_day(
            normalized_metrics,
            tz=meta.tzinfo,
            coverage=meta.coverage,
        )
    except AppleHealthIngestionError as exc:
        await _record_failure_safely(
            pool,
            user_id=user_id,
            sync_id=sync_id,
            http_status=400,
            records_received=received,
            records_processed=0,
            records_failed=received or 1,
            error_message=str(exc),
            request_summary=request_summary,
        )
        raise

    family_rows = {
        key: _finalize_metric_family(key[1], accumulator)
        for key, accumulator in family_accumulators.items()
    }
    counts_by_type, unmapped_types = summarize_parsed_metrics(normalized_metrics)
    aggregated = sum(row["samples_aggregated"] for row in family_rows.values())
    covered_dates = sorted({day for day, _family in family_rows})
    statuses: list[str] = []
    effective_family_rows: dict[tuple[date, str], dict[str, Any]] = {}

    try:
        async with _transactional_connection(pool) as conn:
            for (day, family), columns in sorted(family_rows.items()):
                status = await _upsert_metric_family(
                    conn,
                    user_id=user_id,
                    collector=meta.collector,
                    day=day,
                    family=family,
                    timezone_str=meta.timezone_by_pair[(day, family)],
                    generated_at=meta.generated_at_by_pair[(day, family)],
                    columns=columns,
                    advance_identical_freshness=meta.advance_identical_freshness,
                )
                statuses.append(status)
                effective_family_rows[(day, family)] = (
                    await _load_metric_family_columns(
                        conn,
                        user_id=user_id,
                        collector=meta.collector,
                        day=day,
                        family=family,
                    )
                    if status == "stale"
                    else columns
                )

            rows_updated = statuses.count("updated")
            rows_replayed = statuses.count("replayed")
            rows_stale = statuses.count("stale")
            summary = build_ingestion_summary(
                counts_by_type,
                received=received,
                aggregated=aggregated,
                failed=0,
                aggregate_rows=rows_updated,
                unmapped_types=unmapped_types,
            )
            response_summary = {
                "records_received": received,
                "records_aggregated": aggregated,
                "aggregate_rows_updated": rows_updated,
                "aggregate_rows_replayed": rows_replayed,
                "aggregate_rows_stale": rows_stale,
                "raw_stored": RAW_ROWS_STORED,
                "covered_dates": [day.isoformat() for day in covered_dates],
                "covered_metric_families": sorted(meta.coverage),
                "collector": meta.collector,
                "records_by_type": counts_by_type,
                "unmapped_metric_types": unmapped_types,
                "summary": summary,
            }
            daily_result = _daily_result_from_families(effective_family_rows)
            await conn.execute(
                """UPDATE apple_health_sync
                   SET last_sync_at = NOW(),
                       next_sync_at = NOW() + make_interval(hours => sync_frequency_hours),
                       success_count = success_count + 1,
                       last_error_message = NULL
                   WHERE id = $1""",
                sync_id,
            )
            await record_apple_health_import_log(
                conn,
                user_id=user_id,
                sync_id=sync_id,
                http_status=200,
                records_received=received,
                records_processed=aggregated,
                records_failed=0,
                records_skipped=rows_replayed + rows_stale,
                request_summary=request_summary,
                response_summary=response_summary,
            )
    except AppleHealthSnapshotConflictError as exc:
        await _record_failure_safely(
            pool,
            user_id=user_id,
            sync_id=sync_id,
            http_status=409,
            records_received=received,
            records_processed=0,
            records_failed=received or 1,
            error_message=str(exc),
            request_summary=request_summary,
        )
        raise
    except Exception as exc:
        error = AppleHealthPersistenceError(
            "failed to persist Apple Health processed aggregates"
        )
        await _record_failure_safely(
            pool,
            user_id=user_id,
            sync_id=sync_id,
            http_status=500,
            records_received=received,
            records_processed=0,
            records_failed=received or 1,
            error_message=str(error),
            request_summary=request_summary,
        )
        raise error from exc

    logger.info(
        "Apple Health schema-v3 snapshot ingested: user_id=%s collector=%s "
        "received=%d aggregated=%d updated=%d replayed=%d stale=%d raw_stored=%d",
        user_id,
        meta.collector,
        received,
        aggregated,
        response_summary["aggregate_rows_updated"],
        response_summary["aggregate_rows_replayed"],
        response_summary["aggregate_rows_stale"],
        RAW_ROWS_STORED,
    )
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        **response_summary,
        "records_failed": 0,
        "daily": daily_result,
    }
