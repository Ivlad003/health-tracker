"""Backfill processed daily aggregates from existing raw Apple Health rows.

Apple Health raw-sample retention is now 0 days (see migration 009 and
``app/services/apple_health.py``). Databases that ran the earlier connector still
hold ``health_data`` rows with ``source='apple_health'``. This job:

  1. For each affected user, reads those raw rows and aggregates them per local
     calendar day in memory (reusing the exact ingest aggregation logic).
  2. Inserts one ``health_daily_aggregates`` row per day *only where none exists
     yet* — ``ON CONFLICT ... DO NOTHING``. Any aggregate row already present
     when this one-time backfill runs was written by a post-cutover live sync
     (historical raw rows predate the cutover), so it is authoritative over a
     reconstruction from old raw rows and is never overwritten. This holds even
     for a genuinely zero-activity day the live sync legitimately recorded with
     ``samples_aggregated = 0`` — backfill fills gaps, it does not replace.
  3. Verifies every expected day-row is present (sufficient now the upsert can
     never clobber: a present row is either our insert or the live row we kept).
  4. Only then deletes that user's ``source='apple_health'`` raw rows.

Each user is processed in its own transaction: if verification (or any step)
fails, the transaction rolls back and no raw rows are deleted for that user;
``backfill_all`` isolates that failure and continues with the next user. Other
sources (whoop/fatsecret/manual) are never touched — ``health_data`` is shared,
so we delete strictly by ``source='apple_health'``.

Timezone caveat: raw ``recorded_at`` is stored as UTC and the original device
timezone was not retained, so historical days are attributed in a single
configurable timezone (default UTC). Going forward, live snapshots carry an
explicit ``snapshot.timezone`` and are attributed correctly.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from typing import Any

import asyncpg

from app.services.apple_health import (
    RAW_ROWS_STORED,
    _SUMMARY_MAPPED_METRICS,
    _finalize_day_columns,
    _is_metric,
    _new_day_accumulator,
    _parse_timezone,
    aggregate_metrics_by_day,
    attribution_date_for_metric,
)

logger = logging.getLogger(__name__)


def _rows_to_normalized(rows: list[Any]) -> list[dict[str, Any]]:
    """Adapt raw health_data rows to the normalized-metric shape the aggregator
    consumes."""
    normalized: list[dict[str, Any]] = []
    for row in rows:
        normalized.append(
            {
                "metric_type": str(row["metric_type"]),
                "metric_subtype": row["metric_subtype"],
                "value": row["value"],
                "unit": row["unit"],
                "recorded_at": row["recorded_at"],
                "duration_seconds": row["duration_seconds"],
                "additional_data": {},
            }
        )
    return normalized


async def backfill_user(
    conn: asyncpg.Connection,
    user_id: int,
    *,
    timezone_str: str = "UTC",
) -> dict[str, Any]:
    """Backfill + verify + delete raw rows for one user, atomically."""
    tz = _parse_timezone(timezone_str)
    async with conn.transaction():
        rows = await conn.fetch(
            """SELECT metric_type, metric_subtype, value, unit, recorded_at,
                      duration_seconds
               FROM health_data
               WHERE user_id = $1 AND source = 'apple_health'
               ORDER BY recorded_at ASC""",
            user_id,
        )
        raw_count = len(rows)
        if raw_count == 0:
            return {"user_id": user_id, "raw_rows": 0, "aggregate_rows": 0, "deleted": 0}

        normalized = _rows_to_normalized(rows)
        # Discover the days present so aggregate_metrics_by_day (which rejects any
        # sample outside covered_dates) accepts every historical sample.
        covered_dates = {attribution_date_for_metric(m, tz) for m in normalized}
        days = aggregate_metrics_by_day(normalized, tz=tz, covered_dates=covered_dates)

        inserted = 0
        preserved = 0
        for day in sorted(days.keys()):
            cols = _finalize_day_columns(days.get(day, _new_day_accumulator()))
            day_metrics_blob = {
                "records_by_type": cols["counts"],
                "unmapped_metric_types": [
                    k for k in cols["counts"] if not _is_metric(k, _SUMMARY_MAPPED_METRICS)
                ],
                "backfilled": True,
            }
            # Gap-fill only: DO NOTHING leaves any row a post-cutover live sync
            # already wrote for this day untouched (authoritative over a
            # reconstruction from pre-cutover raw rows), and only inserts a row
            # for a day that has none. ``execute`` returns e.g. "INSERT 0 1" when
            # a row was written and "INSERT 0 0" when the conflict skipped it.
            status = await conn.execute(
                """INSERT INTO health_daily_aggregates
                       (user_id, source, metric_date, timezone, steps,
                        active_energy_kcal, avg_heart_rate, heart_rate_samples,
                        avg_hrv_ms, hrv_samples, sleep_seconds, samples_received,
                        samples_aggregated, metrics)
                   VALUES ($1, 'apple_health', $2, $3, $4, $5, $6, $7, $8, $9, $10,
                           $11, $12, $13::jsonb)
                   ON CONFLICT ON CONSTRAINT health_daily_aggregates_natural_key
                   DO NOTHING""",
                user_id,
                day,
                timezone_str,
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
            )
            if status.rsplit(" ", 1)[-1] == "1":
                inserted += 1
            else:
                preserved += 1

        # Verify every expected day-row exists before deleting anything. Because
        # the upsert can no longer clobber, "row present" is a sufficient check:
        # each present row is either the one we just inserted or the live-sync
        # row we intentionally preserved.
        present = await conn.fetchval(
            """SELECT COUNT(*) FROM health_daily_aggregates
               WHERE user_id = $1 AND source = 'apple_health'
                     AND metric_date = ANY($2::date[])""",
            user_id,
            sorted(days.keys()),
        )
        if int(present) != len(days):
            raise RuntimeError(
                f"backfill verification failed for user {user_id}: "
                f"expected {len(days)} aggregate rows, found {present}"
            )

        deleted = await conn.fetch(
            """DELETE FROM health_data
               WHERE user_id = $1 AND source = 'apple_health'
               RETURNING id""",
            user_id,
        )
        deleted_count = len(deleted)
        logger.info(
            "Backfilled user_id=%s: raw_rows=%d -> inserted=%d preserved_existing=%d, "
            "deleted_raw=%d, raw_stored_now=%d",
            user_id,
            raw_count,
            inserted,
            preserved,
            deleted_count,
            RAW_ROWS_STORED,
        )
        return {
            "user_id": user_id,
            "raw_rows": raw_count,
            "aggregate_rows": inserted,
            "preserved_existing": preserved,
            "deleted": deleted_count,
        }


async def backfill_all(
    conn: asyncpg.Connection, *, timezone_str: str = "UTC"
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Backfill every affected user; one user's failure never aborts the batch.

    Returns ``(results, failures)``. ``results`` holds the per-user success dicts
    (see :func:`backfill_user`); ``failures`` holds ``{"user_id", "error"}`` for
    each user whose transaction raised. A failed user's transaction rolled back
    (its raw rows are left in place), so re-running the whole job later is safe —
    already-completed users short-circuit on ``raw_count == 0`` and failed users
    are retried.
    """
    user_rows = await conn.fetch(
        "SELECT DISTINCT user_id FROM health_data WHERE source = 'apple_health' ORDER BY user_id"
    )
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for row in user_rows:
        user_id = row["user_id"]
        try:
            results.append(await backfill_user(conn, user_id, timezone_str=timezone_str))
        except Exception as exc:  # noqa: BLE001 - isolate one user's failure from the batch
            logger.exception(
                "Apple Health backfill failed for user_id=%s; skipping and continuing", user_id
            )
            failures.append({"user_id": user_id, "error": str(exc)})
    if failures:
        logger.warning(
            "Apple Health backfill: %d user(s) failed and were skipped (raw rows kept for retry): %s",
            len(failures),
            ", ".join(str(f["user_id"]) for f in failures),
        )
    return results, failures


async def run_backfill(timezone_str: str = "UTC") -> None:
    from app.config import settings

    conn = await asyncpg.connect(dsn=settings.database_url)
    try:
        results, failures = await backfill_all(conn, timezone_str=timezone_str)
        total_raw = sum(r["raw_rows"] for r in results)
        total_inserted = sum(r["aggregate_rows"] for r in results)
        total_preserved = sum(r["preserved_existing"] for r in results)
        total_deleted = sum(r["deleted"] for r in results)
        logger.info(
            "Apple Health backfill complete: users=%d failed=%d raw_rows=%d "
            "inserted=%d preserved_existing=%d deleted_raw=%d",
            len(results),
            len(failures),
            total_raw,
            total_inserted,
            total_preserved,
            total_deleted,
        )
        if failures:
            logger.warning(
                "Apple Health backfill: %d user(s) need investigation: %s",
                len(failures),
                "; ".join(f"user {f['user_id']}: {f['error']}" for f in failures),
            )
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill Apple Health daily aggregates from raw rows.")
    parser.add_argument(
        "--timezone",
        default="UTC",
        help="Timezone used to attribute historical raw rows to calendar days (default: UTC).",
    )
    args = parser.parse_args()
    asyncio.run(run_backfill(timezone_str=args.timezone))


if __name__ == "__main__":
    main()
