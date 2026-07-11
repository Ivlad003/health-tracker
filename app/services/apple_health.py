from __future__ import annotations

import hmac
import json
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

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

# A single sleep sample longer than a day is bogus input; clamp it so one bad
# interval cannot inflate the daily total.
_MAX_SLEEP_SAMPLE_SECONDS = 24 * 3600


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
    """Aggregate Apple Health samples for assistant, sync, and briefing consumers.

    Merge contract:
    - Apple Health is primary for phone/watch-native metrics: steps, heart rate,
      HRV (stress proxy), and imported sleep samples.
    - WHOOP remains primary for proprietary metrics: strain, recovery, and
      workout count.
    - Calories burned use WHOOP when live WHOOP has a non-zero value; otherwise
      Apple Health active energy is the fallback.

    Sleep is attributed to the day the sample *ends* (a night usually starts
    before midnight), so the fetch window is widened by one day and overlapping
    samples (In Bed vs sleep stages, iPhone vs Watch) are merged as intervals
    instead of summed.
    """
    fetch_start = start_at - timedelta(days=1)
    rows = await pool.fetch(
        """SELECT metric_type, value, unit, recorded_at, duration_seconds
           FROM health_data
           WHERE user_id = $1
                 AND source = 'apple_health'
                 AND recorded_at >= $2
                 AND recorded_at < $3
           ORDER BY recorded_at ASC""",
        user_id,
        fetch_start,
        end_at,
    )

    steps = 0.0
    active_energy = 0.0
    heart_rates: list[float] = []
    hrv_values: list[float] = []
    sleep_intervals: list[tuple[datetime, datetime]] = []
    counts: dict[str, int] = {}
    latest_metric_at = None

    def _register(key: str, recorded_at: datetime) -> None:
        nonlocal latest_metric_at
        counts[key] = counts.get(key, 0) + 1
        if latest_metric_at is None or recorded_at > latest_metric_at:
            latest_metric_at = recorded_at

    for row in rows:
        metric_type = str(row["metric_type"])
        key = _metric_key(metric_type)
        value = _to_float(row["value"])
        unit = str(row["unit"] or "").lower()
        recorded_at = row["recorded_at"]

        if _is_metric(metric_type, _SLEEP_METRICS):
            duration = min(
                _sleep_duration_seconds(row, value, unit),
                _MAX_SLEEP_SAMPLE_SECONDS,
            )
            if duration <= 0:
                continue
            ends_at = recorded_at + timedelta(seconds=duration)
            if not (start_at <= ends_at < end_at):
                continue
            sleep_intervals.append((recorded_at, ends_at))
            _register(key, recorded_at)
            continue

        if recorded_at < start_at:
            continue
        _register(key, recorded_at)

        if _is_metric(metric_type, _STEP_METRICS):
            steps += value
        elif _is_metric(metric_type, _ACTIVE_ENERGY_METRICS):
            active_energy += value
        elif _is_metric(metric_type, _HRV_METRICS):
            hrv_values.append(value)
        elif _is_metric(metric_type, _HEART_RATE_METRICS):
            heart_rates.append(value)

    sleep_seconds = _merged_interval_seconds(sleep_intervals)
    avg_heart_rate = round(sum(heart_rates) / len(heart_rates)) if heart_rates else 0
    avg_hrv_ms = round(sum(hrv_values) / len(hrv_values)) if hrv_values else 0
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
    error_message: str | None = None,
    request_summary: dict[str, Any] | None = None,
    response_summary: dict[str, Any] | None = None,
) -> None:
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
        error_message=error_message,
        request_summary=request_summary,
        response_summary={"error": error_message},
    )


async def _get_existing_metric_for_unique_key(
    pool: Any,
    *,
    user_id: int,
    metric_type: str,
    recorded_at: datetime,
) -> dict[str, Any] | None:
    row = await pool.fetchrow(
        """SELECT id, value, unit
           FROM health_data
           WHERE user_id = $1
                 AND source = 'apple_health'
                 AND metric_type = $2
                 AND recorded_at = $3
           LIMIT 1""",
        user_id,
        metric_type,
        recorded_at,
    )
    return dict(row) if row is not None else None


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
    return {
        "sourceType": "apple_health",
        "dataType": "auto_export",
        "userId": telegram_user_id,
        "metrics": flat,
    }


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


async def ingest_apple_health_payload(
    pool: Any,
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    """Normalize and store Apple Health metrics sent by an iOS Shortcut."""
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
    inserted = 0
    skipped = 0
    duplicates = 0
    conflicts = 0
    failed = 0

    try:
        metrics = _validate_metric_container(payload)
        normalized_metrics = [
            _normalize_metric(
                metric,
                data_type=payload.get("dataType"),
                current_time=current_time,
            )
            for metric in metrics
        ]
    except AppleHealthIngestionError as exc:
        failed = received or 1
        await record_apple_health_failure(
            pool,
            user_id=user_id,
            sync_id=sync_id,
            http_status=400,
            records_received=received,
            records_processed=inserted,
            records_failed=failed,
            error_message=str(exc),
            request_summary=request_summary,
        )
        raise

    for metric in normalized_metrics:
        try:
            existing = await _get_existing_metric_for_unique_key(
                pool,
                user_id=user_id,
                metric_type=metric["metric_type"],
                recorded_at=metric["recorded_at"],
            )
            if existing is not None:
                skipped += 1
                if existing["unit"] == metric["unit"] and Decimal(str(existing["value"])) == metric["value"]:
                    duplicates += 1
                else:
                    conflicts += 1
                continue

            row = await pool.fetchrow(
                """INSERT INTO health_data
                       (user_id, source, metric_type, metric_subtype, value, unit,
                        recorded_at, duration_seconds, additional_data)
                   VALUES ($1, 'apple_health', $2, $3, $4, $5, $6, $7, $8::jsonb)
                   ON CONFLICT (user_id, source, metric_type, recorded_at) DO NOTHING
                   RETURNING id""",
                user_id,
                metric["metric_type"],
                metric["metric_subtype"],
                metric["value"],
                metric["unit"],
                metric["recorded_at"],
                metric["duration_seconds"],
                json.dumps(metric["additional_data"]),
            )
            if row is None:
                skipped += 1
                conflicts += 1
                continue
            inserted += 1
        except AppleHealthIngestionError:
            failed += 1
            raise
        except Exception as exc:
            failed += 1
            error = AppleHealthIngestionError("failed to process Apple Health metric")
            await record_apple_health_failure(
                pool,
                user_id=user_id,
                sync_id=sync_id,
                http_status=500,
                records_received=received,
                records_processed=inserted,
                records_failed=failed,
                error_message=str(error),
                request_summary=request_summary,
            )
            raise error from exc

    await pool.execute(
        """UPDATE apple_health_sync
           SET last_sync_at = NOW(),
               next_sync_at = NOW() + make_interval(hours => sync_frequency_hours),
               success_count = success_count + 1,
               error_count = error_count + $2,
               last_error_message = NULL
           WHERE id = $1""",
        sync_id,
        failed,
    )
    await record_apple_health_import_log(
        pool,
        user_id=user_id,
        sync_id=sync_id,
        http_status=200,
        records_received=received,
        records_processed=inserted,
        records_failed=failed,
        request_summary=request_summary,
        response_summary={
            "records_received": received,
            "records_processed": inserted,
            "records_inserted": inserted,
            "records_skipped": skipped,
            "records_duplicate": duplicates,
            "records_conflict": conflicts,
            "records_failed": failed,
        },
    )

    logger.info(
        "Apple Health sync ingested: user_id=%s received=%d inserted=%d skipped=%d "
        "duplicates=%d conflicts=%d failed=%d",
        user_id,
        received,
        inserted,
        skipped,
        duplicates,
        conflicts,
        failed,
    )
    return {
        "records_received": received,
        "records_processed": inserted,
        "records_inserted": inserted,
        "records_skipped": skipped,
        "records_duplicate": duplicates,
        "records_conflict": conflicts,
        "records_failed": failed,
    }
