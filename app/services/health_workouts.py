"""Apple Health workouts: validation, persistence, and read-side summary.

Workouts are events, not daily totals, so they bypass the schema-v3 coverage
machinery: each workout is upserted by a stable external id (the HealthKit /
Health Auto Export UUID when present, otherwise a hash of type + start time).
Re-sending the same workout is idempotent; deletions on the phone are not
propagated (documented limitation).

Accepted shapes (one list per request, at most MAX_WORKOUTS_PER_REQUEST):

* native / Shortcut: ``payload["workouts"] = [{"type", "start", "end",
  "duration"?, "active_energy"?, "active_energy_unit"?, "distance"?,
  "distance_unit"?, "avg_heart_rate"?, "max_heart_rate"?, "id"?}]``
* Health Auto Export: ``payload["data"]["workouts"] = [{"id", "name", "start",
  "end", "duration", "activeEnergyBurned": {"qty", "units"},
  "distance": {"qty", "units"}, "avgHeartRate": {"qty"} | "heartRate":
  {"avg": {"qty"}, "max": {"qty"}}, "maxHeartRate": {"qty"}}]``
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from app.services.apple_health import (
    AppleHealthIngestionError,
    _normalize_hae_timestamp,
    _normalize_metric_value_and_unit,
    _parse_decimal,
)

logger = logging.getLogger(__name__)

MAX_WORKOUTS_PER_REQUEST = 200
MAX_WORKOUT_SECONDS = 24 * 3600
MAX_WORKOUT_AGE_DAYS = 30
MAX_HEART_RATE = Decimal("300")


def _aware(value: Any, field: str) -> datetime:
    raw = _normalize_hae_timestamp(str(value or "").strip())
    if not raw:
        raise AppleHealthIngestionError(f"workout {field} is required")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AppleHealthIngestionError(f"workout {field} must be ISO 8601") from exc
    if parsed.tzinfo is None:
        raise AppleHealthIngestionError(f"workout {field} must include a timezone offset")
    return parsed.astimezone(timezone.utc)


def _qty(value: Any) -> tuple[Any, Optional[str]]:
    """HAE wraps quantities as {"qty": x, "units": u}; natives send bare values."""
    if isinstance(value, dict):
        return value.get("qty"), value.get("units") or value.get("unit")
    return value, None


def _energy_kcal(value: Any, unit: Optional[str]) -> Optional[Decimal]:
    if value is None or str(value).strip() == "":
        return None
    normalized, _ = _normalize_metric_value_and_unit(value, "active_energy", unit or "kcal")
    return normalized.quantize(Decimal("0.01"))


def _distance_m(value: Any, unit: Optional[str]) -> Optional[Decimal]:
    if value is None or str(value).strip() == "":
        return None
    normalized, _ = _normalize_metric_value_and_unit(
        value, "walking_running_distance", unit or "m"
    )
    return normalized.quantize(Decimal("0.01"))


def _heart_rate(value: Any) -> Optional[Decimal]:
    raw, _ = _qty(value)
    if raw is None or str(raw).strip() == "":
        return None
    parsed = _parse_decimal(raw, "workout heart rate")
    if parsed < 0 or parsed > MAX_HEART_RATE:
        raise AppleHealthIngestionError("workout heart rate is out of range")
    return parsed.quantize(Decimal("0.01"))


def normalize_workout(
    raw: Any,
    *,
    collector: str,
    current_time: Optional[datetime] = None,
) -> dict[str, Any]:
    """Validate one workout from either shape into DB-ready columns."""
    if not isinstance(raw, dict):
        raise AppleHealthIngestionError("workout must be an object")
    now = (current_time or datetime.now(timezone.utc)).astimezone(timezone.utc)

    workout_type = str(raw.get("type") or raw.get("name") or "").strip()
    if not workout_type:
        raise AppleHealthIngestionError("workout type is required")
    workout_type = workout_type[:64]

    started_at = _aware(raw.get("start") or raw.get("startDate"), "start")
    ended_at = _aware(raw.get("end") or raw.get("endDate"), "end")
    if ended_at < started_at:
        raise AppleHealthIngestionError("workout end is before start")
    if started_at < now - timedelta(days=MAX_WORKOUT_AGE_DAYS):
        raise AppleHealthIngestionError("workout is older than 30 days")
    if ended_at > now + timedelta(days=1):
        raise AppleHealthIngestionError("workout is in the future")

    duration_raw, duration_unit = _qty(raw.get("duration"))
    if duration_raw is not None and str(duration_raw).strip() != "":
        duration = _parse_decimal(duration_raw, "workout duration", allow_quantity_suffix=False)
        if duration_unit and str(duration_unit).lower().startswith("min"):
            duration *= 60
        duration_seconds = int(duration)
    else:
        duration_seconds = int((ended_at - started_at).total_seconds())
    if not 0 <= duration_seconds <= MAX_WORKOUT_SECONDS:
        raise AppleHealthIngestionError("workout duration is out of range")

    energy_value, energy_unit = _qty(
        raw.get("activeEnergyBurned", raw.get("active_energy"))
    )
    distance_value, distance_unit = _qty(raw.get("distance"))
    heart = raw.get("heartRate") if isinstance(raw.get("heartRate"), dict) else {}

    external_id = str(raw.get("id") or "").strip()[:128]
    if not external_id:
        digest = hashlib.sha256(f"{workout_type}|{started_at.isoformat()}".encode()).hexdigest()
        external_id = f"derived:{digest[:40]}"

    return {
        "collector": collector,
        "external_id": external_id,
        "workout_type": workout_type,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_seconds": duration_seconds,
        "active_energy_kcal": _energy_kcal(
            energy_value, energy_unit or raw.get("active_energy_unit")
        ),
        "distance_m": _distance_m(distance_value, distance_unit or raw.get("distance_unit")),
        "avg_heart_rate": _heart_rate(
            raw.get("avg_heart_rate", raw.get("avgHeartRate", heart.get("avg")))
        ),
        "max_heart_rate": _heart_rate(
            raw.get("max_heart_rate", raw.get("maxHeartRate", heart.get("max")))
        ),
    }


def normalize_workouts(
    raw_list: Any,
    *,
    collector: str,
    current_time: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    if not isinstance(raw_list, list):
        raise AppleHealthIngestionError("workouts must be a list")
    if len(raw_list) > MAX_WORKOUTS_PER_REQUEST:
        raise AppleHealthIngestionError(
            f"workouts must contain at most {MAX_WORKOUTS_PER_REQUEST} items"
        )
    normalized = [
        normalize_workout(item, collector=collector, current_time=current_time)
        for item in raw_list
    ]
    # Duplicate ids inside one request would make ON CONFLICT ambiguous.
    unique: dict[str, dict[str, Any]] = {}
    for workout in normalized:
        unique[workout["external_id"]] = workout
    return list(unique.values())


async def persist_workouts(
    pool: Any,
    *,
    user_id: int,
    workouts: list[dict[str, Any]],
    timezone_str: Optional[str] = None,
) -> dict[str, int]:
    """Upsert workouts; returns inserted/updated counts."""
    inserted = updated = 0
    for w in workouts:
        row = await pool.fetchrow(
            """INSERT INTO health_workouts
                   (user_id, source, collector, external_id, workout_type,
                    started_at, ended_at, duration_seconds, active_energy_kcal,
                    distance_m, avg_heart_rate, max_heart_rate, timezone, metrics)
               VALUES ($1, 'apple_health', $2, $3, $4, $5, $6, $7, $8, $9, $10,
                       $11, $12, $13::jsonb)
               ON CONFLICT ON CONSTRAINT health_workouts_natural_key DO UPDATE SET
                   collector = EXCLUDED.collector,
                   workout_type = EXCLUDED.workout_type,
                   started_at = EXCLUDED.started_at,
                   ended_at = EXCLUDED.ended_at,
                   duration_seconds = EXCLUDED.duration_seconds,
                   active_energy_kcal = EXCLUDED.active_energy_kcal,
                   distance_m = EXCLUDED.distance_m,
                   avg_heart_rate = EXCLUDED.avg_heart_rate,
                   max_heart_rate = EXCLUDED.max_heart_rate,
                   timezone = EXCLUDED.timezone,
                   updated_at = NOW()
               RETURNING (xmax = 0) AS inserted""",
            user_id,
            w["collector"],
            w["external_id"],
            w["workout_type"],
            w["started_at"],
            w["ended_at"],
            w["duration_seconds"],
            w["active_energy_kcal"],
            w["distance_m"],
            w["avg_heart_rate"],
            w["max_heart_rate"],
            timezone_str,
            json.dumps({}),
        )
        if row is not None and row["inserted"]:
            inserted += 1
        else:
            updated += 1
    logger.info(
        "Apple Health workouts upserted: user_id=%s inserted=%d updated=%d",
        user_id, inserted, updated,
    )
    return {"workouts_received": len(workouts), "workouts_inserted": inserted, "workouts_updated": updated}


async def get_workouts_summary(
    pool: Any,
    user_id: int,
    *,
    start_at: datetime,
    end_at: datetime,
) -> dict[str, Any]:
    """Workouts that started in [start_at, end_at) plus a GPT-ready sentence."""
    rows = await pool.fetch(
        """SELECT workout_type, started_at, duration_seconds, active_energy_kcal,
                  distance_m, avg_heart_rate
           FROM health_workouts
           WHERE user_id = $1 AND started_at >= $2 AND started_at < $3
           ORDER BY started_at ASC
           LIMIT 20""",
        user_id,
        start_at,
        end_at,
    )
    parts = []
    total_kcal = 0.0
    for r in rows:
        bits = [f"{round(r['duration_seconds'] / 60)} min"]
        if r["active_energy_kcal"] is not None:
            kcal = float(r["active_energy_kcal"])
            total_kcal += kcal
            bits.append(f"{round(kcal)} kcal")
        if r["distance_m"]:
            bits.append(f"{float(r['distance_m']) / 1000:.2f} km")
        if r["avg_heart_rate"]:
            bits.append(f"avg HR {round(float(r['avg_heart_rate']))}")
        parts.append(f"{r['workout_type']} ({', '.join(bits)})")
    summary = ("Apple Health workouts today: " + "; ".join(parts)) if parts else ""
    return {"count": len(rows), "active_energy_kcal": round(total_kcal), "summary": summary}
