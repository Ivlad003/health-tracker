from __future__ import annotations

import hmac
import json
import logging
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)


class AppleHealthIngestionError(ValueError):
    """Raised when Apple Health payload validation or ingestion fails."""


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

# Metric types the summary/aggregation layer knows how to interpret. Storage is
# type-agnostic (any metric_type is persisted), but these are the ones
# get_apple_health_summary rolls up; anything outside this set is stored yet
# not aggregated, and is flagged in the ingestion summary.
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
# aggregated in memory and only the processed daily result is persisted (in
# health_daily_aggregates). This is the count of raw health_data rows written
# per sync -- always zero -- surfaced in results/summaries so operators can
# confirm the retention guarantee.
RAW_ROWS_STORED = 0

# The ingestion contract version the Shortcut must send. A payload without
# schemaVersion 2 + snapshot.{timezone,coveredDates} is a legacy/partial payload
# and is rejected (see _extract_snapshot_meta) rather than merged, because we
# cannot tell which calendar days it fully covers and replacing a daily
# aggregate from a partial window would undercount it.
SNAPSHOT_SCHEMA_VERSION = 2

_REIMPORT_GUIDANCE = (
    "Re-import the latest Apple Health Shortcut from the bot and run it again — "
    "this server now requires a complete calendar-day snapshot "
    "(schemaVersion 2 with snapshot.timezone and snapshot.coveredDates)."
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
        offset = timedelta(hours=int(match.group(2)), minutes=int(match.group(3)))
        return timezone(sign * offset)
    try:
        return ZoneInfo(raw)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        raise AppleHealthIngestionError(
            f"snapshot timezone is not a valid IANA name or UTC offset: {raw!r}"
        ) from exc


def _local_date(dt: datetime, tz: Any) -> date:
    """Calendar date of an instant in the snapshot's timezone."""
    return dt.astimezone(tz).date()


def _parse_coverage_date(value: Any) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise AppleHealthIngestionError(
            f"snapshot coveredDates entry is not an ISO date: {value!r}"
        ) from exc


def _extract_snapshot_meta(payload: dict[str, Any]) -> tuple[Any, str, set[date] | None, datetime | None]:
    """Validate and extract the completeness envelope from a v2 payload.

    Returns ``(tzinfo, timezone_str, covered_dates, generated_at)``. Raises
    AppleHealthIngestionError (with re-import guidance) for any legacy or
    partial/ambiguous payload:
      * schemaVersion != 2, or missing snapshot object;
      * missing/invalid timezone;
      * missing/empty coveredDates, or a non-date entry.
    """
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

    covered_raw = snapshot.get("coveredDates")
    if covered_raw == "auto":
        # Trusted-complete export (e.g. Health Auto Export): coverage is derived
        # from the samples' own attribution days at ingest time.
        covered_dates = None
    elif isinstance(covered_raw, list) and covered_raw:
        covered_dates = {_parse_coverage_date(entry) for entry in covered_raw}
    else:
        raise AppleHealthIngestionError(
            f"snapshot.coveredDates must be a non-empty list of ISO dates. {_REIMPORT_GUIDANCE}"
        )

    generated_at = None
    generated_raw = snapshot.get("generatedAt")
    if generated_raw:
        generated_at = _parse_datetime(str(generated_raw), "snapshot.generatedAt")

    return tzinfo, tz_str, covered_dates, generated_at


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


def _parse_decimal(value: Any, field_name: str) -> Decimal:
    if isinstance(value, str):
        value = _normalize_numeric_text(value)
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise AppleHealthIngestionError(f"{field_name} must be numeric") from exc


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


async def get_apple_health_summary(
    pool: Any,
    user_id: int,
    *,
    start_at: datetime,
    end_at: datetime,
) -> dict[str, Any]:
    """Roll up processed Apple Health daily aggregates for the given window.

    Reads from ``health_daily_aggregates`` (raw samples are no longer retained:
    they are aggregated in memory at ingest time). Each aggregate row is keyed by
    its local ``metric_date`` in the snapshot's stored ``timezone``; a row is
    included when its local midnight falls inside ``[start_at, end_at)``, so a
    "today" window expressed in UTC correctly selects the matching local day.

    Merge contract (unchanged for consumers):
    - Apple Health is primary for steps, heart rate, HRV (stress proxy), sleep.
    - WHOOP remains primary for strain, recovery, and workout count; calories
      burned fall back to Apple Health active energy only when WHOOP is zero.
    """
    rows = await pool.fetch(
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
        (start_at - timedelta(days=2)).date(),
        (end_at + timedelta(days=2)).date(),
    )

    steps = 0.0
    active_energy = 0.0
    hr_weighted = 0.0
    hr_samples = 0
    hrv_weighted = 0.0
    hrv_samples = 0
    sleep_seconds = 0.0
    counts: dict[str, int] = {}
    latest_metric_at = None

    for row in rows:
        try:
            row_tz = _parse_timezone(str(row["timezone"]))
        except AppleHealthIngestionError:
            row_tz = timezone.utc
        local_midnight = datetime.combine(
            row["metric_date"], datetime.min.time(), tzinfo=row_tz
        )
        if not (start_at <= local_midnight < end_at):
            continue

        steps += _to_float(row["steps"])
        active_energy += _to_float(row["active_energy_kcal"])
        sleep_seconds += _to_float(row["sleep_seconds"])

        n_hr = int(row["heart_rate_samples"] or 0)
        if n_hr and row["avg_heart_rate"] is not None:
            hr_weighted += _to_float(row["avg_heart_rate"]) * n_hr
            hr_samples += n_hr
        n_hrv = int(row["hrv_samples"] or 0)
        if n_hrv and row["avg_hrv_ms"] is not None:
            hrv_weighted += _to_float(row["avg_hrv_ms"]) * n_hrv
            hrv_samples += n_hrv

        metrics_blob = row["metrics"]
        if isinstance(metrics_blob, str):
            try:
                metrics_blob = json.loads(metrics_blob)
            except ValueError:
                metrics_blob = {}
        by_type = (metrics_blob or {}).get("records_by_type") if isinstance(metrics_blob, dict) else None
        if isinstance(by_type, dict):
            for key, count in by_type.items():
                counts[key] = counts.get(key, 0) + int(count)

        marker = row["snapshot_generated_at"] or row["updated_at"]
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
        if payload.get(key) is not None:
            summary[key] = payload[key]
    metrics = payload.get("metrics")
    summary["metrics_count"] = len(metrics) if isinstance(metrics, list) else 0
    return summary


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
) -> dict[str, Any]:
    """Flatten Health Auto Export JSON into our internal ingestion shape.

    HAE shape:
        {"data": {"metrics": [{"name", "units", "data": [{"date","qty",...}]}]}}
    Returns:
        {"sourceType":"apple_health", "dataType":"auto_export",
         "userId": <telegram>, "metrics": [{type,value,unit,timestamp}, ...]}
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    hae_metrics = data.get("metrics", []) if isinstance(data, dict) else []
    flat: list[dict[str, Any]] = []
    for hae_metric in hae_metrics:
        if not isinstance(hae_metric, dict):
            continue
        name = str(hae_metric.get("name") or "").strip()
        units = str(hae_metric.get("units") or "").strip() or "unknown"
        for point in hae_metric.get("data") or []:
            if not isinstance(point, dict):
                continue
            raw_ts = point.get("date") or point.get("startDate") or ""
            value = (
                point.get("qty")
                if point.get("qty") is not None
                else point.get("Avg")
                if point.get("Avg") is not None
                else point.get("Min")
                if point.get("Min") is not None
                else point.get("Max")
            )
            unit = units
            if value is None and _is_metric(name, _SLEEP_METRICS):
                # HAE sleep points carry hour buckets (asleep/inBed/...) instead
                # of qty; prefer actual sleep over time in bed.
                for field in ("asleep", "totalSleep", "inBed"):
                    if point.get(field) is not None:
                        value = point[field]
                        break
                if value is not None:
                    raw_ts = point.get("sleepStart") or raw_ts
                    if unit == "unknown":
                        unit = "hr"
            if value is None or not name or not raw_ts:
                continue
            flat.append({
                "type": name,
                "value": value,
                "unit": unit,
                "timestamp": _normalize_hae_timestamp(str(raw_ts)),
            })

    # Health Auto Export is a complete historical export (the user picks a full
    # date range), so we synthesize the v2 completeness envelope the ingestion
    # contract requires. The timezone comes from the samples' own UTC offset;
    # coveredDates is left as "auto" so ingestion derives it from every local day
    # the (normalized) samples actually attribute to — including sleep that ends
    # after midnight. Days are then aggregated and replaced exactly like a native
    # snapshot; no raw samples are retained.
    return {
        "sourceType": "apple_health",
        "schemaVersion": SNAPSHOT_SCHEMA_VERSION,
        "dataType": "auto_export",
        "userId": telegram_user_id,
        "snapshot": {
            "timezone": _first_sample_offset(flat),
            "coveredDates": "auto",
        },
        "metrics": flat,
    }


def _first_sample_offset(metrics: list[dict[str, Any]]) -> str:
    """UTC offset ("+03:00") of the first parseable sample, or "+00:00"."""
    for metric in metrics:
        try:
            parsed = datetime.fromisoformat(str(metric.get("timestamp", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            continue
        offset = parsed.utcoffset() or timedelta(0)
        total_minutes = int(offset.total_seconds() // 60)
        sign = "+" if total_minutes >= 0 else "-"
        total_minutes = abs(total_minutes)
        return f"{sign}{total_minutes // 60:02d}:{total_minutes % 60:02d}"
    return "+00:00"


def _validate_metric_container(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if payload.get("sourceType") != "apple_health":
        raise AppleHealthIngestionError("sourceType must be apple_health")

    metrics = payload.get("metrics")
    if not isinstance(metrics, list):
        raise AppleHealthIngestionError("metrics must be a list")

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

    value = _parse_decimal(metric.get("value"), "metric value")
    recorded_at = _parse_datetime(str(metric.get("timestamp") or ""), "metric timestamp")
    if recorded_at < current_time - timedelta(days=30):
        raise AppleHealthIngestionError("metric timestamp is older than 30 days")
    if recorded_at > current_time + timedelta(days=1):
        raise AppleHealthIngestionError("metric timestamp is in the future")

    duration = metric.get("duration")
    duration_seconds = int(duration) if duration is not None else None
    if duration_seconds is not None and duration_seconds < 0:
        raise AppleHealthIngestionError("metric duration must be non-negative")

    # The Shortcut cannot compute numeric durations reliably (Shortcuts renders
    # sleep Value/Duration as localized text), so interval samples send an
    # "end" ISO timestamp instead and the duration is derived here.
    if duration_seconds is None:
        end_raw = metric.get("end") or metric.get("end_timestamp") or metric.get("endDate")
        if end_raw:
            ended_at = _parse_datetime(str(end_raw), "metric end")
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
    ``unmapped_types`` are keys stored but not rolled up by the aggregation
    layer (see ``_SUMMARY_MAPPED_METRICS``).
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

    Example: ``"1239 samples received, 1239 aggregated into 3 daily rows, raw
    stored: 0: 515 steps, 509 active energy, 215 sleep"``. Raw HealthKit samples
    are never persisted (retention 0) — only the processed daily aggregate rows
    are, so the summary reports samples in / aggregate rows out / raw stored 0.
    """
    breakdown_parts = [
        f"{count} {_metric_summary_label(key)}"
        for key, count in sorted(counts_by_type.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    breakdown = ", ".join(breakdown_parts) if breakdown_parts else "no metrics"

    row_word = "row" if aggregate_rows == 1 else "rows"
    summary = (
        f"{received} samples received, {aggregated} aggregated into "
        f"{aggregate_rows} daily {row_word}, raw stored: {RAW_ROWS_STORED}"
    )
    if failed:
        summary += f", {failed} failed"
    summary += f": {breakdown}"
    if unmapped_types:
        summary += f" (stored but not summarized: {', '.join(unmapped_types)})"
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
            return _local_date(recorded_at + timedelta(seconds=duration), tz)
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


async def ingest_apple_health_payload(
    pool: Any,
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Aggregate an Apple Health day-snapshot in memory and upsert daily rows.

    Contract (schemaVersion 2): the Shortcut posts a complete calendar-day
    snapshot with an explicit ``snapshot.timezone`` and ``snapshot.coveredDates``
    (the local days it fully covers, each queried from local midnight; the
    current day is a complete-so-far snapshot that a later sync replaces).
    Individual HealthKit samples are parsed and aggregated in memory, then the
    processed result for each covered day is atomically upserted into
    ``health_daily_aggregates`` keyed ``(user_id, source, metric_date)``. **No
    raw samples are persisted** (``raw stored: 0``).

    Idempotency: the upsert *replaces* each day's row with EXCLUDED values, never
    increments. An identical resend leaves values unchanged; a newer, fuller
    snapshot for the same day overwrites it — no double counting.

    Legacy or partial/ambiguous payloads (missing the envelope, or carrying
    samples outside the declared coverage) are rejected with sanitized re-import
    guidance instead of being merged.
    """
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
        tz, tz_str, covered_dates, generated_at = _extract_snapshot_meta(payload)
        metrics = _validate_metric_container(payload)
        normalized_metrics = [
            _normalize_metric(
                metric,
                data_type=payload.get("dataType"),
                current_time=current_time,
            )
            for metric in metrics
        ]
        if covered_dates is None:
            # "auto" coverage (trusted-complete export): every local day the
            # samples attribute to is covered.
            covered_dates = {
                attribution_date_for_metric(m, tz) for m in normalized_metrics
            }
        days = aggregate_metrics_by_day(
            normalized_metrics, tz=tz, covered_dates=covered_dates
        )
    except AppleHealthIngestionError as exc:
        await record_apple_health_failure(
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

    counts_by_type, unmapped_types = summarize_parsed_metrics(normalized_metrics)
    aggregated = sum(acc["aggregated"] for acc in days.values())

    # Upsert-replace every covered day, including days with zero samples (a
    # declared-complete day with no activity is truthfully zero). Ordered for
    # deterministic behaviour under replay.
    daily_result: dict[str, Any] = {}
    try:
        for day in sorted(covered_dates):
            cols = _finalize_day_columns(days.get(day, _new_day_accumulator()))
            day_metrics_blob = {
                "records_by_type": cols["counts"],
                "unmapped_metric_types": [
                    k for k in cols["counts"] if not _is_metric(k, _SUMMARY_MAPPED_METRICS)
                ],
            }
            await pool.execute(
                """INSERT INTO health_daily_aggregates
                       (user_id, source, metric_date, timezone, steps,
                        active_energy_kcal, avg_heart_rate, heart_rate_samples,
                        avg_hrv_ms, hrv_samples, sleep_seconds, samples_received,
                        samples_aggregated, metrics, snapshot_generated_at)
                   VALUES ($1, 'apple_health', $2, $3, $4, $5, $6, $7, $8, $9, $10,
                           $11, $12, $13::jsonb, $14)
                   ON CONFLICT ON CONSTRAINT health_daily_aggregates_natural_key
                   DO UPDATE SET
                       timezone = EXCLUDED.timezone,
                       steps = EXCLUDED.steps,
                       active_energy_kcal = EXCLUDED.active_energy_kcal,
                       avg_heart_rate = EXCLUDED.avg_heart_rate,
                       heart_rate_samples = EXCLUDED.heart_rate_samples,
                       avg_hrv_ms = EXCLUDED.avg_hrv_ms,
                       hrv_samples = EXCLUDED.hrv_samples,
                       sleep_seconds = EXCLUDED.sleep_seconds,
                       samples_received = EXCLUDED.samples_received,
                       samples_aggregated = EXCLUDED.samples_aggregated,
                       metrics = EXCLUDED.metrics,
                       snapshot_generated_at = EXCLUDED.snapshot_generated_at,
                       updated_at = NOW()""",
                user_id,
                day,
                tz_str,
                cols["steps"],
                cols["active_energy_kcal"],
                cols["avg_heart_rate"],
                cols["heart_rate_samples"],
                cols["avg_hrv_ms"],
                cols["hrv_samples"],
                cols["sleep_seconds"],
                cols["samples_received"],
                cols["samples_aggregated"],
                json.dumps(day_metrics_blob),
                generated_at,
            )
            daily_result[day.isoformat()] = {
                "steps": cols["steps"],
                "active_energy_kcal": float(cols["active_energy_kcal"]),
                "avg_heart_rate": float(cols["avg_heart_rate"]) if cols["avg_heart_rate"] is not None else 0,
                "avg_hrv_ms": float(cols["avg_hrv_ms"]) if cols["avg_hrv_ms"] is not None else 0,
                "sleep_hours": round(cols["sleep_seconds"] / 3600, 1) if cols["sleep_seconds"] else 0,
                "samples_received": cols["samples_received"],
                "records_by_type": cols["counts"],
            }
    except Exception as exc:
        # A DB failure mid-upsert must be recorded and surfaced, not swallowed.
        # Each day's upsert is atomic; a partial run leaves already-written days
        # in place (each is an idempotent replace, so a retry converges).
        error = AppleHealthIngestionError("failed to persist Apple Health daily aggregate")
        await record_apple_health_failure(
            pool,
            user_id=user_id,
            sync_id=sync_id,
            http_status=500,
            records_received=received,
            records_processed=aggregated,
            records_failed=received or 1,
            error_message=str(error),
            request_summary=request_summary,
        )
        raise error from exc

    aggregate_rows = len(covered_dates)
    summary = build_ingestion_summary(
        counts_by_type,
        received=received,
        aggregated=aggregated,
        failed=0,
        aggregate_rows=aggregate_rows,
        unmapped_types=unmapped_types,
    )

    await pool.execute(
        """UPDATE apple_health_sync
           SET last_sync_at = NOW(),
               next_sync_at = NOW() + make_interval(hours => sync_frequency_hours),
               success_count = success_count + 1,
               last_error_message = NULL
           WHERE id = $1""",
        sync_id,
    )
    await record_apple_health_import_log(
        pool,
        user_id=user_id,
        sync_id=sync_id,
        http_status=200,
        records_received=received,
        records_processed=aggregated,
        records_failed=0,
        records_skipped=0,
        request_summary=request_summary,
        response_summary={
            "records_received": received,
            "records_aggregated": aggregated,
            "aggregate_rows_updated": aggregate_rows,
            "raw_stored": RAW_ROWS_STORED,
            "covered_dates": [d.isoformat() for d in sorted(covered_dates)],
            "records_by_type": counts_by_type,
            "unmapped_metric_types": unmapped_types,
            "summary": summary,
        },
    )

    logger.info(
        "Apple Health snapshot ingested: user_id=%s received=%d aggregated=%d "
        "aggregate_rows=%d raw_stored=%d",
        user_id,
        received,
        aggregated,
        aggregate_rows,
        RAW_ROWS_STORED,
    )
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "records_received": received,
        "records_aggregated": aggregated,
        "aggregate_rows_updated": aggregate_rows,
        "raw_stored": RAW_ROWS_STORED,
        "records_failed": 0,
        "covered_dates": [d.isoformat() for d in sorted(covered_dates)],
        "daily": daily_result,
        "records_by_type": counts_by_type,
        "unmapped_metric_types": unmapped_types,
        "summary": summary,
    }
