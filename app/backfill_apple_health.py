"""Expand legacy Apple Health raw rows into schema-v3 daily metric families.

The default command is deliberately non-destructive: it aggregates and verifies
legacy ``health_data`` rows while retaining them for rollback. Raw deletion is a
separate, explicit ``--delete-raw`` contract-phase operation. Each user runs in
one transaction, and destructive runs delete only the row IDs selected under
lock; rows arriving concurrently remain for a later run and cause the final
residual-row gate to fail closed.

Historical raw rows do not retain the device timezone. They are attributed in a
configurable timezone whose default matches the application contract,
``Europe/Kyiv``. The synthetic schema-v3 collector is ``legacy_backfill`` and
its generated timestamp is deterministically derived from the newest selected
raw-row ``created_at`` value for each metric family/day.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import date, datetime, timezone
from typing import Any

import asyncpg

from app.services.apple_health import (
    _finalize_metric_family,
    _normalize_metric_value_and_unit,
    _parse_timezone,
    _upsert_metric_family,
    aggregate_metric_families_by_day,
    attribution_date_for_metric,
    metric_family_for_type,
)

logger = logging.getLogger(__name__)

DEFAULT_BACKFILL_TIMEZONE = "Europe/Kyiv"
LEGACY_BACKFILL_COLLECTOR = "legacy_backfill"


class AppleHealthBackfillError(RuntimeError):
    """Raised when a backfill run is incomplete and must not be treated as success."""


def _additional_data(row: Any) -> dict[str, Any]:
    value = row["additional_data"]
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _rows_to_normalized(rows: list[Any]) -> list[dict[str, Any]]:
    """Adapt raw ``health_data`` rows to the shared v3 aggregator input."""
    normalized: list[dict[str, Any]] = []
    for row in rows:
        metric_type = str(row["metric_type"])
        value, unit = _normalize_metric_value_and_unit(
            row["value"], metric_type, row["unit"]
        )
        normalized.append({
            "metric_type": metric_type,
            "metric_subtype": row["metric_subtype"],
            "value": value,
            "unit": unit,
            "recorded_at": row["recorded_at"],
            "duration_seconds": row["duration_seconds"],
            "additional_data": _additional_data(row),
        })
    return normalized


def _row_generated_at(row: Any) -> datetime:
    """Return a stable UTC freshness marker for one legacy raw row."""
    marker = row["created_at"] or row["recorded_at"]
    if marker.tzinfo is None or marker.utcoffset() is None:
        return marker.replace(tzinfo=timezone.utc)
    return marker.astimezone(timezone.utc)


async def backfill_user(
    conn: asyncpg.Connection,
    user_id: int,
    *,
    timezone_str: str = DEFAULT_BACKFILL_TIMEZONE,
    delete_raw: bool = False,
) -> dict[str, Any]:
    """Aggregate and verify one user; optionally delete exactly the selected rows."""
    tz = _parse_timezone(timezone_str)
    async with conn.transaction():
        rows = await conn.fetch(
            """SELECT id, metric_type, metric_subtype, value, unit, recorded_at,
                      duration_seconds, additional_data, created_at
               FROM health_data
               WHERE user_id = $1 AND source = 'apple_health'
               ORDER BY recorded_at ASC, id ASC
               FOR UPDATE""",
            user_id,
        )
        raw_count = len(rows)
        if raw_count == 0:
            return {
                "user_id": user_id,
                "raw_rows": 0,
                "aggregate_rows": 0,
                "preserved_existing": 0,
                "deleted": 0,
                "retained": 0,
            }

        normalized = _rows_to_normalized(rows)
        unsupported_ids = [
            int(row["id"])
            for row, metric in zip(rows, normalized, strict=True)
            if metric_family_for_type(str(metric["metric_type"])) is None
        ]
        if delete_raw and unsupported_ids:
            raise AppleHealthBackfillError(
                f"refusing destructive backfill for user {user_id}: unsupported raw "
                f"metric rows cannot be verified ({len(unsupported_ids)} row(s))"
            )
        coverage: dict[str, set[date]] = {}
        generated_at_by_pair: dict[tuple[date, str], datetime] = {}
        for row, metric in zip(rows, normalized, strict=True):
            family = metric_family_for_type(str(metric["metric_type"]))
            if family is None:
                continue
            day = attribution_date_for_metric(metric, tz)
            coverage.setdefault(family, set()).add(day)
            pair = (day, family)
            marker = _row_generated_at(row)
            existing_marker = generated_at_by_pair.get(pair)
            if existing_marker is None or marker > existing_marker:
                generated_at_by_pair[pair] = marker

        groups = aggregate_metric_families_by_day(
            normalized,
            tz=tz,
            coverage=coverage,
        )
        status_counts = {"updated": 0, "replayed": 0, "stale": 0}
        for (day, family), accumulator in sorted(groups.items()):
            columns = _finalize_metric_family(family, accumulator)
            status = await _upsert_metric_family(
                conn,
                user_id=user_id,
                collector=LEGACY_BACKFILL_COLLECTOR,
                day=day,
                family=family,
                timezone_str=timezone_str,
                generated_at=generated_at_by_pair[(day, family)],
                columns=columns,
            )
            status_counts[status] += 1

        expected_pairs = set(groups)
        present_rows = await conn.fetch(
            """SELECT metric_date, metric_family
               FROM health_daily_metric_aggregates
               WHERE user_id = $1 AND source = 'apple_health' AND collector = $2""",
            user_id,
            LEGACY_BACKFILL_COLLECTOR,
        )
        present_pairs = {
            (row["metric_date"], str(row["metric_family"])) for row in present_rows
        }
        missing_pairs = expected_pairs - present_pairs
        if missing_pairs:
            missing = ", ".join(
                f"{day.isoformat()}:{family}" for day, family in sorted(missing_pairs)
            )
            raise RuntimeError(
                f"backfill verification failed for user {user_id}; missing {missing}"
            )

        selected_ids = [int(row["id"]) for row in rows]
        deleted_count = 0
        if delete_raw:
            deleted_rows = await conn.fetch(
                """DELETE FROM health_data
                   WHERE user_id = $1 AND source = 'apple_health'
                         AND id = ANY($2::integer[])
                   RETURNING id""",
                user_id,
                selected_ids,
            )
            deleted_ids = [int(row["id"]) for row in deleted_rows]
            if len(deleted_ids) != len(selected_ids) or set(deleted_ids) != set(selected_ids):
                raise RuntimeError(
                    f"backfill deletion failed for user {user_id}: selected raw-row IDs "
                    "did not match deleted IDs"
                )
            deleted_count = len(deleted_ids)

        preserved = status_counts["replayed"] + status_counts["stale"]
        logger.info(
            "Backfilled user_id=%s: raw_rows=%d aggregate_rows=%d updated=%d "
            "preserved_existing=%d deleted_raw=%d retained_selected=%d",
            user_id,
            raw_count,
            len(expected_pairs),
            status_counts["updated"],
            preserved,
            deleted_count,
            raw_count - deleted_count,
        )
        return {
            "user_id": user_id,
            "raw_rows": raw_count,
            "aggregate_rows": len(expected_pairs),
            "preserved_existing": preserved,
            "deleted": deleted_count,
            "retained": raw_count - deleted_count,
        }


async def backfill_all(
    conn: asyncpg.Connection,
    *,
    timezone_str: str = DEFAULT_BACKFILL_TIMEZONE,
    delete_raw: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Process every affected user while collecting per-user failures."""
    user_rows = await conn.fetch(
        "SELECT DISTINCT user_id FROM health_data WHERE source = 'apple_health' ORDER BY user_id"
    )
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for row in user_rows:
        user_id = int(row["user_id"])
        try:
            results.append(
                await backfill_user(
                    conn,
                    user_id,
                    timezone_str=timezone_str,
                    delete_raw=delete_raw,
                )
            )
        except Exception as exc:  # noqa: BLE001 - preserve later users for diagnosis
            logger.exception(
                "Apple Health backfill failed for user_id=%s; continuing", user_id
            )
            failures.append({"user_id": user_id, "error": str(exc)})
    return results, failures


async def run_backfill(
    timezone_str: str = DEFAULT_BACKFILL_TIMEZONE,
    *,
    delete_raw: bool = False,
) -> list[dict[str, Any]]:
    """Run the migration and fail closed on partial or destructive results."""
    from app.config import settings

    conn = await asyncpg.connect(dsn=settings.database_url)
    try:
        if delete_raw:
            async with conn.transaction():
                await conn.execute(
                    "LOCK TABLE health_data IN SHARE ROW EXCLUSIVE MODE"
                )
                results, failures = await backfill_all(
                    conn,
                    timezone_str=timezone_str,
                    delete_raw=True,
                )
                await _validate_backfill_results(conn, results, failures, delete_raw=True)
                return results

        results, failures = await backfill_all(
            conn, timezone_str=timezone_str, delete_raw=False
        )
        await _validate_backfill_results(conn, results, failures, delete_raw=False)
        return results
    finally:
        await conn.close()


async def _validate_backfill_results(
    conn: asyncpg.Connection,
    results: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    *,
    delete_raw: bool,
) -> None:
    """Log totals and fail while a destructive run still holds its table lock."""
    total_raw = sum(int(result["raw_rows"]) for result in results)
    total_aggregates = sum(int(result["aggregate_rows"]) for result in results)
    total_preserved = sum(int(result["preserved_existing"]) for result in results)
    total_deleted = sum(int(result["deleted"]) for result in results)
    logger.info(
        "Apple Health backfill complete: users=%d failed=%d raw_rows=%d "
        "aggregate_rows=%d preserved_existing=%d deleted_raw=%d destructive=%s",
        len(results),
        len(failures),
        total_raw,
        total_aggregates,
        total_preserved,
        total_deleted,
        delete_raw,
    )

    if failures:
        details = "; ".join(
            f"user {failure['user_id']}: {failure['error']}" for failure in failures
        )
        raise AppleHealthBackfillError(
            f"Apple Health backfill incomplete; {len(failures)} user(s) failed: {details}"
        )

    if delete_raw:
        residual = int(
            await conn.fetchval(
                "SELECT COUNT(*) FROM health_data WHERE source = 'apple_health'"
            )
        )
        if residual:
            row_word = "row" if residual == 1 else "rows"
            raise AppleHealthBackfillError(
                f"Apple Health destructive purge incomplete; {residual} raw {row_word} remain"
            )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill schema-v3 Apple Health daily metric aggregates from raw rows."
    )
    parser.add_argument(
        "--timezone",
        default=DEFAULT_BACKFILL_TIMEZONE,
        help=(
            "Timezone used to attribute historical raw rows to calendar days "
            f"(default: {DEFAULT_BACKFILL_TIMEZONE})."
        ),
    )
    parser.add_argument(
        "--delete-raw",
        action="store_true",
        help=(
            "Irreversibly delete only raw Apple Health rows verified in this run. "
            "Without this flag, raw rows are retained for rollback."
        ),
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    try:
        asyncio.run(
            run_backfill(
                timezone_str=args.timezone,
                delete_raw=args.delete_raw,
            )
        )
    except AppleHealthBackfillError as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from exc
    except Exception as exc:
        logger.error("Apple Health backfill failed: %s", exc.__class__.__name__)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
