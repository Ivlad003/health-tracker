from __future__ import annotations

import hmac
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

logger = logging.getLogger(__name__)


class AppleHealthIngestionError(ValueError):
    """Raised when Apple Health payload validation or ingestion fails."""


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
    """Create or reactivate Apple Health sync credentials for a user."""
    token = secrets.token_urlsafe(32)
    row = await pool.fetchrow(
        """INSERT INTO apple_health_sync (user_id, secret_key, sync_frequency_hours, is_active)
           VALUES ($1, $2, $3, TRUE)
           ON CONFLICT (user_id) DO UPDATE
           SET is_active = TRUE,
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


def _parse_decimal(value: Any, field_name: str) -> Decimal:
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


async def _is_duplicate_metric(
    pool: Any,
    *,
    user_id: int,
    metric_type: str,
    unit: str,
    value: Decimal,
    recorded_at: datetime,
) -> bool:
    row = await pool.fetchrow(
        """SELECT id
           FROM health_data
           WHERE user_id = $1
                 AND source = 'apple_health'
                 AND metric_type = $2
                 AND unit = $3
                 AND value = $4
                 AND recorded_at BETWEEN $5 AND $6
           LIMIT 1""",
        user_id,
        metric_type,
        unit,
        value,
        recorded_at - timedelta(minutes=5),
        recorded_at + timedelta(minutes=5),
    )
    return row is not None


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
            if value is None or not name or not raw_ts:
                continue
            flat.append({
                "type": name,
                "value": value,
                "unit": units,
                "timestamp": _normalize_hae_timestamp(str(raw_ts)),
            })
    return {
        "sourceType": "apple_health",
        "dataType": "auto_export",
        "userId": telegram_user_id,
        "metrics": flat,
    }


def _validate_payload(payload: dict[str, Any]) -> tuple[int, list[dict[str, Any]]]:
    if payload.get("sourceType") != "apple_health":
        raise AppleHealthIngestionError("sourceType must be apple_health")

    try:
        telegram_user_id = int(payload["userId"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AppleHealthIngestionError("userId must be a Telegram user id") from exc

    metrics = payload.get("metrics")
    if not isinstance(metrics, list):
        raise AppleHealthIngestionError("metrics must be a list")

    return telegram_user_id, metrics


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
    telegram_user_id, metrics = _validate_payload(payload)
    sync = await _get_active_sync(pool, telegram_user_id)
    user_id = sync["user_id"]
    sync_id = sync["sync_id"]
    normalized_metrics = [
        _normalize_metric(
            metric,
            data_type=payload.get("dataType"),
            current_time=current_time,
        )
        for metric in metrics
    ]
    processed = 0
    failed = 0

    for metric in normalized_metrics:
        try:
            duplicate = await _is_duplicate_metric(
                pool,
                user_id=user_id,
                metric_type=metric["metric_type"],
                unit=metric["unit"],
                value=metric["value"],
                recorded_at=metric["recorded_at"],
            )
            if duplicate:
                continue

            await pool.execute(
                """INSERT INTO health_data
                       (user_id, source, metric_type, metric_subtype, value, unit,
                        recorded_at, duration_seconds, additional_data)
                   VALUES ($1, 'apple_health', $2, $3, $4, $5, $6, $7, $8::jsonb)
                   ON CONFLICT (user_id, source, metric_type, recorded_at) DO NOTHING""",
                user_id,
                metric["metric_type"],
                metric["metric_subtype"],
                metric["value"],
                metric["unit"],
                metric["recorded_at"],
                metric["duration_seconds"],
                json.dumps(metric["additional_data"]),
            )
            processed += 1
        except AppleHealthIngestionError:
            failed += 1
            raise
        except Exception as exc:
            failed += 1
            raise AppleHealthIngestionError("failed to process Apple Health metric") from exc

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

    logger.info(
        "Apple Health sync ingested: user_id=%s received=%d processed=%d failed=%d",
        user_id,
        received,
        processed,
        failed,
    )
    return {
        "records_received": received,
        "records_processed": processed,
        "records_failed": failed,
    }
