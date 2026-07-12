"""Real-PostgreSQL tests for the Apple Health daily-aggregate model (KRI-30).

Covers the migration matrix (fresh install, upgrade-from-007, repeated preflight,
failure rollback) and the ingestion acceptance criteria (raw-row growth exactly
0, same-second distinct samples, Awake/In-Bed sleep, replay idempotency, and a
newer snapshot replacing an older one without double counting).

These tests need a reachable PostgreSQL. Set ``APPLE_HEALTH_TEST_DATABASE_URL``
(or ``DATABASE_URL`` pointing at a throwaway database) — otherwise they skip.
They DROP and recreate the ``public`` schema, so never point them at real data.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

asyncpg = pytest.importorskip("asyncpg")

from app.db_preflight import (  # noqa: E402
    AppleHealthSchemaError,
    apply_apple_health_migrations,
    verify_apple_health_schema,
)

MIGRATIONS = Path(__file__).resolve().parents[1] / "database" / "migrations"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "apple-health-payload.json"

# Minimal prerequisites the Apple Health migrations (007/009) build on. Mirrors
# what the production base schema (002) provides: a users table, the shared
# updated_at trigger function, and the sync_type enum that 007 extends.
BOOTSTRAP_SQL = """
DROP SCHEMA public CASCADE;
CREATE SCHEMA public;
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    telegram_user_id BIGINT UNIQUE
);
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DO $$ BEGIN
    CREATE TYPE sync_type AS ENUM ('whoop', 'fatsecret');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
"""


def _dsn() -> str | None:
    return os.environ.get("APPLE_HEALTH_TEST_DATABASE_URL") or os.environ.get(
        "APPLE_HEALTH_TEST_DSN"
    )


pytestmark = pytest.mark.skipif(
    _dsn() is None,
    reason="Set APPLE_HEALTH_TEST_DATABASE_URL to run real-PostgreSQL Apple Health tests",
)


async def _connect():
    return await asyncpg.connect(dsn=_dsn())


async def _bootstrap(conn) -> None:
    await conn.execute(BOOTSTRAP_SQL)


async def _apply_007(conn) -> None:
    await conn.execute((MIGRATIONS / "007_apple_health_connector.sql").read_text(encoding="utf-8"))


async def _seed_user(conn, telegram_user_id: int = 999) -> tuple[int, int]:
    user_id = await conn.fetchval(
        "INSERT INTO users (telegram_user_id) VALUES ($1) RETURNING id", telegram_user_id
    )
    sync_id = await conn.fetchval(
        """INSERT INTO apple_health_sync (user_id, secret_key, sync_frequency_hours, is_active)
           VALUES ($1, $2, 6, TRUE) RETURNING id""",
        user_id,
        f"secret-key-{telegram_user_id}",
    )
    return user_id, sync_id


async def _insert_raw(
    conn,
    user_id: int,
    *,
    metric_type: str,
    value: float,
    unit: str,
    recorded_at: datetime,
    subtype: str | None = None,
    duration: int | None = None,
) -> None:
    """Seed a raw pre-cutover ``health_data`` row (source='apple_health')."""
    await conn.execute(
        """INSERT INTO health_data
               (user_id, source, metric_type, metric_subtype, value, unit,
                recorded_at, duration_seconds)
           VALUES ($1, 'apple_health', $2, $3, $4, $5, $6, $7)""",
        user_id,
        metric_type,
        subtype,
        value,
        unit,
        recorded_at,
        duration,
    )


async def _seed_aggregate(
    conn,
    user_id: int,
    day: date,
    *,
    steps: int,
    active_energy: str,
    samples_aggregated: int,
    tz: str = "America/New_York",
) -> None:
    """Seed a pre-existing aggregate row, simulating one a post-cutover live sync
    already wrote for ``day`` (the row backfill must never clobber)."""
    await conn.execute(
        """INSERT INTO health_daily_aggregates
               (user_id, source, metric_date, timezone, steps,
                active_energy_kcal, samples_received, samples_aggregated, metrics)
           VALUES ($1, 'apple_health', $2, $3, $4, $5, $6, $7,
                   '{"source": "live_sync"}'::jsonb)""",
        user_id,
        day,
        tz,
        steps,
        active_energy,
        samples_aggregated,
        samples_aggregated,
    )


# --------------------------------------------------------------------------- #
# Migration matrix
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_fresh_install_applies_and_verifies():
    conn = await _connect()
    try:
        await _bootstrap(conn)
        await apply_apple_health_migrations(conn)
        await verify_apple_health_schema(conn)  # raises on failure
        exists = await conn.fetchval(
            "SELECT to_regclass('public.health_daily_aggregates') IS NOT NULL"
        )
        assert exists is True
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_upgrade_from_007_only_database():
    conn = await _connect()
    try:
        await _bootstrap(conn)
        await _apply_007(conn)  # database already at 007
        with pytest.raises(AppleHealthSchemaError):
            await verify_apple_health_schema(conn)  # 009 objects missing
        await apply_apple_health_migrations(conn)  # applies 009 (007 idempotent)
        await verify_apple_health_schema(conn)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_repeated_preflight_is_idempotent():
    conn = await _connect()
    try:
        await _bootstrap(conn)
        for _ in range(3):
            await apply_apple_health_migrations(conn)
            await verify_apple_health_schema(conn)
        n = await conn.fetchval(
            "SELECT count(*) FROM pg_constraint WHERE conname = 'health_daily_aggregates_natural_key'"
        )
        assert n == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_failure_leaves_no_partial_schema_and_recovers():
    conn = await _connect()
    try:
        await _bootstrap(conn)
        await _apply_007(conn)
        # Force 009 to fail mid-flight by dropping the enum it depends on inside
        # a savepoint-free simple execution: apply a broken variant.
        broken = (MIGRATIONS / "009_health_daily_aggregates.sql").read_text(encoding="utf-8")
        broken = broken.replace("data_source", "nonexistent_enum_type")
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute(broken)
        # Clear the aborted-transaction state the failed BEGIN left on the conn.
        try:
            await conn.execute("ROLLBACK")
        except asyncpg.PostgresError:
            pass
        # The failed migration's BEGIN/COMMIT rolled back: no table left behind.
        exists = await conn.fetchval(
            "SELECT to_regclass('public.health_daily_aggregates') IS NOT NULL"
        )
        assert exists is False
        # A subsequent correct apply still succeeds (no partial-state wedging).
        await apply_apple_health_migrations(conn)
        await verify_apple_health_schema(conn)
    finally:
        await conn.close()


# --------------------------------------------------------------------------- #
# Ingestion acceptance
# --------------------------------------------------------------------------- #
async def _fresh_schema_with_user(conn):
    await _bootstrap(conn)
    await apply_apple_health_migrations(conn)
    return await _seed_user(conn)


def _load_fixture_metrics() -> list[dict]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return payload["metrics"]


def _snapshot_payload(metrics, covered_dates, tz="+03:00", user_id=999):
    return {
        "sourceType": "apple_health",
        "schemaVersion": 2,
        "userId": user_id,
        "snapshot": {"timezone": tz, "coveredDates": covered_dates},
        "metrics": metrics,
    }


@pytest.mark.asyncio
async def test_fixture_produces_daily_aggregates_with_zero_raw_growth():
    from app.services.apple_health import ingest_apple_health_payload

    conn = await _connect()
    try:
        await _fresh_schema_with_user(conn)
        metrics = _load_fixture_metrics()
        payload = _snapshot_payload(metrics, ["2026-07-09", "2026-07-10", "2026-07-11"])

        result = await ingest_apple_health_payload(
            conn, payload, now=datetime(2026, 7, 12, tzinfo=timezone.utc)
        )

        # Raw-row growth is exactly zero.
        raw = await conn.fetchval(
            "SELECT count(*) FROM health_data WHERE source = 'apple_health'"
        )
        assert raw == 0
        assert result["raw_stored"] == 0
        assert result["records_received"] == 1239
        assert result["aggregate_rows_updated"] == 3

        rows = await conn.fetch(
            "SELECT metric_date, steps, samples_received FROM health_daily_aggregates "
            "WHERE source = 'apple_health' ORDER BY metric_date"
        )
        assert [r["metric_date"] for r in rows] == [
            date(2026, 7, 9), date(2026, 7, 10), date(2026, 7, 11)
        ]
        # Expected steps per local day, computed straight from the fixture.
        expected_steps = {date(2026, 7, 9): 0, date(2026, 7, 10): 0, date(2026, 7, 11): 0}
        for m in metrics:
            if m["type"] == "step_count":
                d = datetime.fromisoformat(m["timestamp"]).date()
                expected_steps[d] += float(m["value"])
        for r in rows:
            assert r["steps"] == round(expected_steps[r["metric_date"]])
        total_received = sum(r["samples_received"] for r in rows)
        assert total_received == 1239
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_replay_is_idempotent_and_newer_snapshot_replaces():
    from app.services.apple_health import ingest_apple_health_payload

    conn = await _connect()
    try:
        await _fresh_schema_with_user(conn)
        metrics = _load_fixture_metrics()
        payload = _snapshot_payload(metrics, ["2026-07-09", "2026-07-10", "2026-07-11"])
        now = datetime(2026, 7, 12, tzinfo=timezone.utc)

        await ingest_apple_health_payload(conn, payload, now=now)
        first = await conn.fetch(
            "SELECT metric_date, steps, active_energy_kcal, sleep_seconds "
            "FROM health_daily_aggregates ORDER BY metric_date"
        )
        # Replay the identical snapshot: values unchanged, no double counting.
        await ingest_apple_health_payload(conn, payload, now=now)
        second = await conn.fetch(
            "SELECT metric_date, steps, active_energy_kcal, sleep_seconds "
            "FROM health_daily_aggregates ORDER BY metric_date"
        )
        assert [dict(r) for r in first] == [dict(r) for r in second]

        # A newer, fuller snapshot for one day replaces (not increments) it.
        newer = _snapshot_payload(
            [{"type": "step_count", "value": 12345, "unit": "count",
              "timestamp": "2026-07-11T09:00:00+03:00"}],
            ["2026-07-11"],
        )
        await ingest_apple_health_payload(conn, newer, now=now)
        steps_11 = await conn.fetchval(
            "SELECT steps FROM health_daily_aggregates WHERE metric_date = '2026-07-11'"
        )
        assert steps_11 == 12345  # replaced, not added to the previous total
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_same_second_distinct_samples_both_contribute():
    from app.services.apple_health import ingest_apple_health_payload

    conn = await _connect()
    try:
        await _fresh_schema_with_user(conn)
        ts = "2026-07-11T09:18:00+03:00"
        payload = _snapshot_payload(
            [
                {"type": "step_count", "value": 3, "unit": "count", "timestamp": ts},
                {"type": "step_count", "value": 6, "unit": "count", "timestamp": ts},
                {"type": "active_energy", "value": 1.5, "unit": "kcal", "timestamp": ts},
                {"type": "active_energy", "value": 2.5, "unit": "kcal", "timestamp": ts},
            ],
            ["2026-07-11"],
        )
        await ingest_apple_health_payload(
            conn, payload, now=datetime(2026, 7, 12, tzinfo=timezone.utc)
        )
        row = await conn.fetchrow(
            "SELECT steps, active_energy_kcal FROM health_daily_aggregates "
            "WHERE metric_date = '2026-07-11'"
        )
        assert row["steps"] == 9  # 3 + 6, not collapsed
        assert float(row["active_energy_kcal"]) == 4.0  # 1.5 + 2.5
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_awake_and_in_bed_sleep_same_start_merge_without_double_count():
    from app.services.apple_health import ingest_apple_health_payload

    conn = await _connect()
    try:
        await _fresh_schema_with_user(conn)
        # Two overlapping sleep segments sharing the SAME start second (the
        # KRI-30 raw-key collision case): a short "Awake" blip inside a 2h "In
        # Bed" envelope, both ending on the covered day. Both must contribute
        # (count == 2) and the merged union is 2h, not 2h05m summed.
        payload = _snapshot_payload(
            [
                {"type": "sleep_analysis", "value": 0, "unit": "s",
                 "timestamp": "2026-07-11T13:00:00+03:00",
                 "end": "2026-07-11T13:05:00+03:00", "stage": "Awake"},
                {"type": "sleep_analysis", "value": 0, "unit": "s",
                 "timestamp": "2026-07-11T13:00:00+03:00",
                 "end": "2026-07-11T15:00:00+03:00", "stage": "InBed"},
            ],
            ["2026-07-11"],
        )
        await ingest_apple_health_payload(
            conn, payload, now=datetime(2026, 7, 12, tzinfo=timezone.utc)
        )
        row = await conn.fetchrow(
            "SELECT sleep_seconds, metrics FROM health_daily_aggregates "
            "WHERE metric_date = '2026-07-11'"
        )
        assert row["sleep_seconds"] == 2 * 3600  # union, not 2h05m summed
        blob = json.loads(row["metrics"]) if isinstance(row["metrics"], str) else row["metrics"]
        assert blob["records_by_type"].get("sleep_analysis") == 2  # both contribute
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_legacy_payload_without_envelope_is_rejected():
    from app.services.apple_health import (
        AppleHealthIngestionError,
        ingest_apple_health_payload,
    )

    conn = await _connect()
    try:
        await _fresh_schema_with_user(conn)
        legacy = {
            "sourceType": "apple_health",
            "userId": 999,
            "metrics": [
                {"type": "step_count", "value": 10, "unit": "count",
                 "timestamp": "2026-07-11T09:00:00+03:00"}
            ],
        }
        with pytest.raises(AppleHealthIngestionError) as exc:
            await ingest_apple_health_payload(
                conn, legacy, now=datetime(2026, 7, 12, tzinfo=timezone.utc)
            )
        assert "Re-import" in str(exc.value)
        raw = await conn.fetchval("SELECT count(*) FROM health_daily_aggregates")
        assert raw == 0
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_sample_outside_covered_dates_is_rejected():
    from app.services.apple_health import (
        AppleHealthIngestionError,
        ingest_apple_health_payload,
    )

    conn = await _connect()
    try:
        await _fresh_schema_with_user(conn)
        payload = _snapshot_payload(
            [
                {"type": "step_count", "value": 10, "unit": "count",
                 "timestamp": "2026-07-11T09:00:00+03:00"},
                {"type": "step_count", "value": 10, "unit": "count",
                 "timestamp": "2026-07-08T09:00:00+03:00"},  # outside coverage
            ],
            ["2026-07-11"],
        )
        with pytest.raises(AppleHealthIngestionError):
            await ingest_apple_health_payload(
                conn, payload, now=datetime(2026, 7, 12, tzinfo=timezone.utc)
            )
    finally:
        await conn.close()


# --------------------------------------------------------------------------- #
# Backfill data-safety (KRI-30 DB review follow-up)
#
# The one-time backfill reconstructs daily aggregates from pre-cutover raw rows,
# then deletes those raw rows. Any aggregate row that already exists when it runs
# was written by a post-cutover live sync and is authoritative — backfill must
# only fill genuine gaps, never overwrite an existing row (whether that row is
# populated or a legitimately zero-activity day the live sync recorded with
# samples_aggregated=0). These reproduce the reviewer's clobber repro and the
# genuinely-empty-row variant the architect asked to verify.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_backfill_does_not_clobber_existing_populated_aggregate():
    from app.backfill_apple_health import backfill_all

    conn = await _connect()
    try:
        user_id, _ = await _fresh_schema_with_user(conn)

        # Day with a live-sync aggregate already present (must be preserved).
        await _insert_raw(conn, user_id, metric_type="step_count", value=8,
                          unit="count", recorded_at=datetime(2026, 7, 11, 9, 0, tzinfo=timezone.utc))
        await _insert_raw(conn, user_id, metric_type="step_count", value=6,
                          unit="count", recorded_at=datetime(2026, 7, 11, 9, 1, tzinfo=timezone.utc))
        await _insert_raw(conn, user_id, metric_type="active_energy", value=12.5,
                          unit="kcal", recorded_at=datetime(2026, 7, 11, 9, 2, tzinfo=timezone.utc))
        await _seed_aggregate(conn, user_id, date(2026, 7, 11),
                              steps=9999, active_energy="500.00", samples_aggregated=50)

        # Day with NO pre-existing aggregate (genuine gap → backfill fills it).
        await _insert_raw(conn, user_id, metric_type="step_count", value=100,
                          unit="count", recorded_at=datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc))

        await backfill_all(conn, timezone_str="UTC")

        # The pre-existing row is byte-for-byte untouched (not the 14/12.50/3
        # reconstruction the old DO UPDATE upsert would have written).
        kept = await conn.fetchrow(
            "SELECT steps, active_energy_kcal, samples_aggregated, timezone, metrics "
            "FROM health_daily_aggregates WHERE metric_date = '2026-07-11'"
        )
        assert kept["steps"] == 9999
        assert float(kept["active_energy_kcal"]) == 500.00
        assert kept["samples_aggregated"] == 50
        assert kept["timezone"] == "America/New_York"  # backfill runs UTC → proves untouched
        blob = json.loads(kept["metrics"]) if isinstance(kept["metrics"], str) else kept["metrics"]
        assert blob.get("source") == "live_sync"

        # The genuine gap day WAS filled from the raw rows.
        gap = await conn.fetchrow(
            "SELECT steps, samples_aggregated, metrics "
            "FROM health_daily_aggregates WHERE metric_date = '2026-07-10'"
        )
        assert gap["steps"] == 100
        gap_blob = json.loads(gap["metrics"]) if isinstance(gap["metrics"], str) else gap["metrics"]
        assert gap_blob.get("backfilled") is True

        # Raw rows were still purged for the user (backfill completed).
        raw_left = await conn.fetchval(
            "SELECT count(*) FROM health_data WHERE user_id = $1 AND source = 'apple_health'",
            user_id,
        )
        assert raw_left == 0
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_backfill_does_not_clobber_genuinely_empty_aggregate():
    # The subtle case: a live sync legitimately recorded a covered day as
    # zero-activity (samples_aggregated=0). A `WHERE samples_aggregated = 0`
    # conditional guard would treat this as "no real data yet" and overwrite it;
    # DO NOTHING correctly preserves it.
    from app.backfill_apple_health import backfill_all

    conn = await _connect()
    try:
        user_id, _ = await _fresh_schema_with_user(conn)

        await _insert_raw(conn, user_id, metric_type="step_count", value=14,
                          unit="count", recorded_at=datetime(2026, 7, 11, 9, 0, tzinfo=timezone.utc))
        # Pre-existing authoritative zero-activity row for the same day.
        await _seed_aggregate(conn, user_id, date(2026, 7, 11),
                              steps=0, active_energy="0.00", samples_aggregated=0)

        await backfill_all(conn, timezone_str="UTC")

        kept = await conn.fetchrow(
            "SELECT steps, samples_aggregated, timezone, metrics "
            "FROM health_daily_aggregates WHERE metric_date = '2026-07-11'"
        )
        assert kept["steps"] == 0  # NOT overwritten with the reconstructed 14
        assert kept["samples_aggregated"] == 0
        assert kept["timezone"] == "America/New_York"
        blob = json.loads(kept["metrics"]) if isinstance(kept["metrics"], str) else kept["metrics"]
        assert blob.get("source") == "live_sync"  # still the live-sync row, not backfilled

        raw_left = await conn.fetchval(
            "SELECT count(*) FROM health_data WHERE user_id = $1 AND source = 'apple_health'",
            user_id,
        )
        assert raw_left == 0
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_backfill_all_isolates_per_user_failure_and_reports_it():
    # One user's failure must not abort the batch: later users are still
    # processed and the failure is collected and reported.
    from app.backfill_apple_health import backfill_all

    conn = await _connect()
    try:
        await _bootstrap(conn)
        await apply_apple_health_migrations(conn)
        bad_user, _ = await _seed_user(conn, telegram_user_id=1001)
        good_user, _ = await _seed_user(conn, telegram_user_id=1002)
        assert bad_user < good_user  # bad user is processed first (ORDER BY user_id)

        # bad_user: a negative step reconstruction violates the steps>=0 CHECK,
        # so its per-user transaction raises and rolls back.
        await _insert_raw(conn, bad_user, metric_type="step_count", value=-5,
                          unit="count", recorded_at=datetime(2026, 7, 11, 9, 0, tzinfo=timezone.utc))
        # good_user: a clean day that must still be backfilled after the failure.
        await _insert_raw(conn, good_user, metric_type="step_count", value=42,
                          unit="count", recorded_at=datetime(2026, 7, 11, 9, 0, tzinfo=timezone.utc))

        results, failures = await backfill_all(conn, timezone_str="UTC")

        # The good user was still processed despite the earlier failure.
        assert [r["user_id"] for r in results] == [good_user]
        assert results[0]["aggregate_rows"] == 1
        good_steps = await conn.fetchval(
            "SELECT steps FROM health_daily_aggregates WHERE user_id = $1", good_user
        )
        assert good_steps == 42

        # The failure was collected and reported, and its transaction rolled back
        # (no aggregate row, raw rows still present so a re-run can retry it).
        assert [f["user_id"] for f in failures] == [bad_user]
        bad_agg = await conn.fetchval(
            "SELECT count(*) FROM health_daily_aggregates WHERE user_id = $1", bad_user
        )
        assert bad_agg == 0
        bad_raw = await conn.fetchval(
            "SELECT count(*) FROM health_data WHERE user_id = $1 AND source = 'apple_health'",
            bad_user,
        )
        assert bad_raw == 1
    finally:
        await conn.close()
