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

import asyncio
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

asyncpg = pytest.importorskip("asyncpg")

from app.db_preflight import (  # noqa: E402
    AppleHealthSchemaError,
    apply_apple_health_migrations,
    run_preflight,
    verify_apple_health_schema,
)

MIGRATIONS = Path(__file__).resolve().parents[1] / "database" / "migrations"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "apple-health-payload.json"

# Minimal prerequisites the Apple Health migrations (007/009/010) build on. Mirrors
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
async def test_concurrent_preflight_serializes_migration(monkeypatch):
    conn = await _connect()
    try:
        await _bootstrap(conn)
    finally:
        await conn.close()

    from app.config import settings

    monkeypatch.setattr(settings, "database_url", _dsn())
    await asyncio.gather(*(run_preflight(apply_migration=True) for _ in range(4)))

    conn = await _connect()
    try:
        await verify_apple_health_schema(conn)
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rollback_name", "table_name"),
    [
        ("010_health_daily_metric_aggregates_rollback.sql", "health_daily_metric_aggregates"),
        ("009_health_daily_aggregates_rollback.sql", "health_daily_aggregates"),
    ],
)
async def test_empty_aggregate_rollbacks_are_idempotent(rollback_name, table_name):
    conn = await _connect()
    try:
        await _bootstrap(conn)
        await apply_apple_health_migrations(conn)
        rollback_sql = (MIGRATIONS / rollback_name).read_text(encoding="utf-8")

        await conn.execute(rollback_sql)
        await conn.execute(rollback_sql)

        exists = await conn.fetchval(
            "SELECT to_regclass($1) IS NOT NULL", f"public.{table_name}"
        )
        assert exists is False
    finally:
        await conn.close()


@pytest.mark.parametrize(
    ("rollback_name", "table_name"),
    [
        ("010_health_daily_metric_aggregates_rollback.sql", "health_daily_metric_aggregates"),
        ("009_health_daily_aggregates_rollback.sql", "health_daily_aggregates"),
    ],
)
def test_aggregate_rollbacks_lock_before_checking_for_rows(
    rollback_name, table_name
):
    rollback_sql = (MIGRATIONS / rollback_name).read_text(encoding="utf-8")

    lock = f"LOCK TABLE {table_name} IN ACCESS EXCLUSIVE MODE"
    assert lock in rollback_sql
    assert rollback_sql.index(lock) < rollback_sql.index("SELECT EXISTS")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rollback_name", "table_name", "seed_sql"),
    [
        (
            "010_health_daily_metric_aggregates_rollback.sql",
            "health_daily_metric_aggregates",
            """INSERT INTO health_daily_metric_aggregates
                      (user_id, source, collector, metric_date, metric_family,
                       timezone, total_value, sample_count, samples_received,
                       samples_aggregated, snapshot_generated_at, payload_hash)
                 VALUES (1, 'apple_health', 'shortcut', DATE '2026-07-13',
                         'steps', 'UTC', 1, 1, 1, 1, NOW(), repeat('a', 64))""",
        ),
        (
            "009_health_daily_aggregates_rollback.sql",
            "health_daily_aggregates",
            """INSERT INTO health_daily_aggregates
                      (user_id, source, metric_date, timezone, steps)
                 VALUES (1, 'apple_health', DATE '2026-07-13', 'UTC', 1)""",
        ),
    ],
)
async def test_nonempty_aggregate_rollbacks_fail_closed(
    rollback_name, table_name, seed_sql
):
    conn = await _connect()
    try:
        await _bootstrap(conn)
        await apply_apple_health_migrations(conn)
        await conn.execute("INSERT INTO users (id, telegram_user_id) VALUES (1, 999)")
        await conn.execute(seed_sql)
        rollback_sql = (MIGRATIONS / rollback_name).read_text(encoding="utf-8")

        with pytest.raises(asyncpg.RaiseError, match="Refusing to drop nonempty"):
            await conn.execute(rollback_sql)
        await conn.execute("ROLLBACK")

        exists = await conn.fetchval(
            "SELECT to_regclass($1) IS NOT NULL", f"public.{table_name}"
        )
        row_count = await conn.fetchval(f"SELECT count(*) FROM {table_name}")
        assert exists is True
        assert row_count == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("total_value", "average_value"),
    [("NaN", None), ("0", "NaN"), ("0", "-1")],
)
async def test_metric_family_table_rejects_nonfinite_or_negative_values(
    total_value, average_value
):
    conn = await _connect()
    try:
        await _bootstrap(conn)
        await apply_apple_health_migrations(conn)
        await conn.execute("INSERT INTO users (id, telegram_user_id) VALUES (1, 999)")

        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """INSERT INTO health_daily_metric_aggregates
                          (user_id, source, collector, metric_date, metric_family,
                           timezone, total_value, average_value, sample_count,
                           samples_received, samples_aggregated,
                           snapshot_generated_at, payload_hash)
                     VALUES (1, 'apple_health', 'shortcut', DATE '2026-07-13',
                             'heart_rate', 'UTC', $1::numeric, $2::numeric,
                             1, 1, 1, NOW(), repeat('a', 64))""",
                total_value,
                average_value,
            )
    finally:
        await conn.close()


# --------------------------------------------------------------------------- #
# Ingestion acceptance
# --------------------------------------------------------------------------- #
async def _fresh_schema_with_user(conn):
    await _bootstrap(conn)
    await apply_apple_health_migrations(conn)
    return await _seed_user(conn)


def _load_fixture_payload() -> dict:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["userId"] = 999
    return payload


def _snapshot_payload(
    metrics,
    covered_dates,
    tz="+03:00",
    user_id=999,
    *,
    collector="shortcut",
    generated_at=None,
    covered_families=None,
):
    family_by_type = {
        "step_count": "steps",
        "active_energy": "active_energy",
        "heart_rate": "heart_rate",
        "heart_rate_variability": "hrv",
        "sleep_analysis": "sleep",
    }
    if covered_families is None:
        covered_families = sorted(
            {family_by_type[metric["type"]] for metric in metrics if metric["type"] in family_by_type}
        )
    if generated_at is None:
        generated_at = max(
            datetime.fromisoformat(str(metric.get("end") or metric["timestamp"]))
            for metric in metrics
        ).isoformat()
    return {
        "sourceType": "apple_health",
        "schemaVersion": 3,
        "userId": user_id,
        "snapshot": {
            "collector": collector,
            "generatedAt": generated_at,
            "timezone": tz,
            "coveredDates": covered_dates,
            "coveredMetricFamilies": covered_families,
        },
        "metrics": metrics,
    }


@pytest.mark.asyncio
async def test_fixture_produces_daily_aggregates_with_zero_raw_growth():
    from app.services.apple_health import ingest_apple_health_payload

    conn = await _connect()
    try:
        await _fresh_schema_with_user(conn)
        payload = _load_fixture_payload()
        metrics = payload["metrics"]

        result = await ingest_apple_health_payload(
            conn, payload, now=datetime(2026, 7, 12, tzinfo=timezone.utc)
        )

        # Raw-row growth is exactly zero.
        raw = await conn.fetchval(
            "SELECT count(*) FROM health_data WHERE source = 'apple_health'"
        )
        assert raw == 0
        assert result["raw_stored"] == 0
        assert result["records_received"] == len(metrics)
        expected_family_rows = sum(
            len(dates) for dates in payload["snapshot"]["coveredDatesByFamily"].values()
        )
        assert result["aggregate_rows_updated"] == expected_family_rows

        rows = await conn.fetch(
            "SELECT metric_date, total_value AS steps, samples_received "
            "FROM health_daily_metric_aggregates "
            "WHERE source = 'apple_health' AND metric_family = 'steps' "
            "ORDER BY metric_date"
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
        total_received = await conn.fetchval(
            "SELECT sum(samples_received) FROM health_daily_metric_aggregates"
        )
        assert total_received == len(metrics)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_replay_is_idempotent_and_newer_snapshot_replaces():
    from app.services.apple_health import (
        AppleHealthSnapshotConflictError,
        ingest_apple_health_payload,
    )

    conn = await _connect()
    try:
        await _fresh_schema_with_user(conn)
        payload = _load_fixture_payload()
        now = datetime(2026, 7, 12, tzinfo=timezone.utc)

        await ingest_apple_health_payload(conn, payload, now=now)
        first = await conn.fetch(
            "SELECT metric_date, metric_family, total_value, average_value, sample_count "
            "FROM health_daily_metric_aggregates ORDER BY metric_date, metric_family"
        )
        # Replay the identical snapshot: values unchanged, no double counting.
        await ingest_apple_health_payload(conn, payload, now=now)
        second = await conn.fetch(
            "SELECT metric_date, metric_family, total_value, average_value, sample_count "
            "FROM health_daily_metric_aggregates ORDER BY metric_date, metric_family"
        )
        assert [dict(r) for r in first] == [dict(r) for r in second]

        # A newer, fuller snapshot for one day replaces (not increments) it.
        newer = _snapshot_payload(
            [{"type": "step_count", "value": 12345, "unit": "count",
              "timestamp": "2026-07-11T09:00:00+03:00"}],
            ["2026-07-11"],
            generated_at="2026-07-11T23:59:30+03:00",
        )
        await ingest_apple_health_payload(conn, newer, now=now)
        steps_11 = await conn.fetchval(
            "SELECT total_value FROM health_daily_metric_aggregates "
            "WHERE metric_date = '2026-07-11' AND metric_family = 'steps' "
            "AND collector = 'shortcut'"
        )
        assert steps_11 == 12345  # replaced, not added to the previous total

        # A delayed older snapshot is accepted as stale but cannot overwrite it.
        delayed = _snapshot_payload(
            [{"type": "step_count", "value": 1, "unit": "count",
              "timestamp": "2026-07-11T08:00:00+03:00"}],
            ["2026-07-11"],
            generated_at="2026-07-11T22:00:00+03:00",
        )
        delayed_result = await ingest_apple_health_payload(conn, delayed, now=now)
        assert delayed_result["aggregate_rows_stale"] == 1
        assert delayed_result["daily"]["2026-07-11"]["steps"] == 12345
        assert await conn.fetchval(
            "SELECT total_value FROM health_daily_metric_aggregates "
            "WHERE metric_date = '2026-07-11' AND metric_family = 'steps' "
            "AND collector = 'shortcut'"
        ) == 12345

        # One logical snapshot timestamp cannot identify different processed
        # content. Reject it rather than choosing an arrival-order winner.
        same_time_different = _snapshot_payload(
            [{"type": "step_count", "value": 54321, "unit": "count",
              "timestamp": "2026-07-11T10:00:00+03:00"}],
            ["2026-07-11"],
            generated_at="2026-07-11T23:59:30+03:00",
        )
        with pytest.raises(AppleHealthSnapshotConflictError, match="conflicts"):
            await ingest_apple_health_payload(conn, same_time_different, now=now)
        assert await conn.fetchval(
            "SELECT total_value FROM health_daily_metric_aggregates "
            "WHERE metric_date = '2026-07-11' AND metric_family = 'steps' "
            "AND collector = 'shortcut'"
        ) == 12345
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
            "SELECT "
            "max(total_value) FILTER (WHERE metric_family = 'steps') AS steps, "
            "max(total_value) FILTER (WHERE metric_family = 'active_energy') AS active_energy_kcal "
            "FROM health_daily_metric_aggregates WHERE metric_date = '2026-07-11'"
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
        # Bed" envelope, both ending on the covered day. Both are accounted for,
        # while the fallback subtracts the 5-minute Awake interval from In Bed.
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
            "SELECT total_value AS sleep_seconds, metrics "
            "FROM health_daily_metric_aggregates "
            "WHERE metric_date = '2026-07-11' AND metric_family = 'sleep'"
        )
        assert row["sleep_seconds"] == 2 * 3600 - 5 * 60
        blob = json.loads(row["metrics"]) if isinstance(row["metrics"], str) else row["metrics"]
        assert blob["records_by_type"].get("sleep_analysis") == 2  # both contribute
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_multi_family_persistence_failure_rolls_back_entire_snapshot():
    from app.services.apple_health import (
        AppleHealthPersistenceError,
        ingest_apple_health_payload,
    )

    conn = await _connect()
    try:
        _user_id, sync_id = await _fresh_schema_with_user(conn)
        await conn.execute(
            """CREATE OR REPLACE FUNCTION reject_step_family_for_test()
               RETURNS trigger AS $$
               BEGIN
                   IF NEW.metric_family = 'steps' THEN
                       RAISE EXCEPTION 'forced later-family persistence failure';
                   END IF;
                   RETURN NEW;
               END;
               $$ LANGUAGE plpgsql;
               CREATE TRIGGER reject_step_family_for_test
               BEFORE INSERT OR UPDATE ON health_daily_metric_aggregates
               FOR EACH ROW EXECUTE FUNCTION reject_step_family_for_test();"""
        )
        payload = _snapshot_payload(
            [
                {"type": "active_energy", "value": 12.5, "unit": "kcal",
                 "timestamp": "2026-07-11T09:00:00+03:00"},
                {"type": "step_count", "value": 1, "unit": "count",
                 "timestamp": "2026-07-11T09:01:00+03:00"},
            ],
            ["2026-07-11"],
        )

        with pytest.raises(AppleHealthPersistenceError):
            await ingest_apple_health_payload(
                conn, payload, now=datetime(2026, 7, 12, tzinfo=timezone.utc)
            )

        assert await conn.fetchval(
            "SELECT count(*) FROM health_daily_metric_aggregates"
        ) == 0
        failure = await conn.fetchrow(
            "SELECT http_status, records_processed, error_message "
            "FROM apple_health_import_logs ORDER BY id DESC LIMIT 1"
        )
        assert dict(failure) == {
            "http_status": 500,
            "records_processed": 0,
            "error_message": "failed to persist Apple Health processed aggregates",
        }
        assert await conn.fetchval(
            "SELECT success_count FROM apple_health_sync WHERE id = $1", sync_id
        ) == 0
    finally:
        await conn.execute(
            "DROP TRIGGER IF EXISTS reject_step_family_for_test "
            "ON health_daily_metric_aggregates"
        )
        await conn.execute("DROP FUNCTION IF EXISTS reject_step_family_for_test()")
        await conn.close()


@pytest.mark.asyncio
async def test_reader_chooses_freshest_collector_per_family_without_summing():
    from app.services.apple_health import (
        get_apple_health_summary,
        ingest_apple_health_payload,
    )

    conn = await _connect()
    try:
        user_id, _sync_id = await _fresh_schema_with_user(conn)
        now = datetime(2026, 7, 12, tzinfo=timezone.utc)
        native = _snapshot_payload(
            [
                {"type": "step_count", "value": 500, "unit": "count",
                 "timestamp": "2026-07-11T09:00:00+03:00"},
                {"type": "sleep_analysis", "value": 0, "unit": "s",
                 "timestamp": "2026-07-10T23:00:00+03:00",
                 "end": "2026-07-11T06:00:00+03:00", "stage": "Core"},
            ],
            ["2026-07-11"],
            generated_at="2026-07-11T12:00:00+03:00",
        )
        hae_steps = _snapshot_payload(
            [{"type": "step_count", "value": 1000, "unit": "count",
              "timestamp": "2026-07-11T10:00:00+03:00"}],
            ["2026-07-11"],
            collector="health_auto_export",
            generated_at="2026-07-11T13:00:00+03:00",
        )
        hae_steps["snapshot"]["generatedAtProvenance"] = "receipt"
        await ingest_apple_health_payload(conn, native, now=now)
        await ingest_apple_health_payload(conn, hae_steps, now=now)

        start_at = datetime(2026, 7, 10, 21, 0, tzinfo=timezone.utc)
        summary = await get_apple_health_summary(
            conn,
            user_id,
            start_at=start_at,
            end_at=start_at + timedelta(days=1),
        )

        assert summary["steps"] == 1000
        assert summary["sleep_hours"] == 7.0

        # A new explicit native observation advances freshness even when its
        # processed content matches the older native row.
        native_again = _snapshot_payload(
            [{"type": "step_count", "value": 500, "unit": "count",
              "timestamp": "2026-07-11T09:00:00+03:00"}],
            ["2026-07-11"],
            generated_at="2026-07-11T14:00:00+03:00",
        )
        native_result = await ingest_apple_health_payload(conn, native_again, now=now)
        assert native_result["aggregate_rows_updated"] == 1

        # A byte-equivalent HAE retry whose freshness was synthesized from
        # receipt time is a replay and must not leapfrog that native snapshot.
        hae_retry = _snapshot_payload(
            [{"type": "step_count", "value": 1000, "unit": "count",
              "timestamp": "2026-07-11T10:00:00+03:00"}],
            ["2026-07-11"],
            collector="health_auto_export",
            generated_at="2026-07-11T15:00:00+03:00",
        )
        hae_retry["snapshot"]["generatedAtProvenance"] = "receipt"
        retry_result = await ingest_apple_health_payload(conn, hae_retry, now=now)
        assert retry_result["aggregate_rows_replayed"] == 1

        final_summary = await get_apple_health_summary(
            conn,
            user_id,
            start_at=start_at,
            end_at=start_at + timedelta(days=1),
        )
        assert final_summary["steps"] == 500
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_reader_selects_freshest_in_window_collector_after_timezone_filter():
    from app.services.apple_health import get_apple_health_summary

    conn = await _connect()
    try:
        user_id, _sync_id = await _fresh_schema_with_user(conn)
        await conn.executemany(
            """INSERT INTO health_daily_metric_aggregates
                      (user_id, source, collector, metric_date, metric_family,
                       timezone, total_value, sample_count, samples_received,
                       samples_aggregated, snapshot_generated_at, payload_hash)
               VALUES ($1, 'apple_health', $2, DATE '2026-07-11', 'steps',
                       $3, $4, 1, 1, 1, $5, $6)""",
            [
                (
                    user_id,
                    "shortcut",
                    "+03:00",
                    500,
                    datetime(2026, 7, 11, 9, 0, tzinfo=timezone.utc),
                    "a" * 64,
                ),
                (
                    user_id,
                    "health_auto_export",
                    "-12:00",
                    1000,
                    datetime(2026, 7, 11, 11, 0, tzinfo=timezone.utc),
                    "b" * 64,
                ),
            ],
        )

        start_at = datetime(2026, 7, 10, 21, 0, tzinfo=timezone.utc)
        summary = await get_apple_health_summary(
            conn,
            user_id,
            start_at=start_at,
            end_at=start_at + timedelta(days=1),
        )

        assert summary["steps"] == 500
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_hae_negative_offset_without_top_timezone_remains_visible():
    from app.services.apple_health import (
        convert_health_auto_export,
        get_apple_health_summary,
        ingest_apple_health_payload,
    )

    conn = await _connect()
    try:
        user_id, _sync_id = await _fresh_schema_with_user(conn)
        now = datetime(2026, 7, 12, tzinfo=timezone.utc)
        payload = convert_health_auto_export(
            {
                "data": {
                    "metrics": [
                        {
                            "name": "step_count",
                            "units": "count",
                            "data": [
                                {
                                    "date": "2026-07-11 09:00:00 -0700",
                                    "qty": 4321,
                                }
                            ],
                        }
                    ]
                }
            },
            telegram_user_id=999,
            automation_period="today",
            snapshot_timezone="-07:00",
            snapshot_generated_at="2026-07-11T17:00:00Z",
            now=now,
        )

        await ingest_apple_health_payload(conn, payload, now=now)
        start_at = datetime(2026, 7, 11, 7, 0, tzinfo=timezone.utc)
        summary = await get_apple_health_summary(
            conn,
            user_id,
            start_at=start_at,
            end_at=start_at + timedelta(days=1),
        )

        assert summary["steps"] == 4321
        assert await conn.fetchval(
            "SELECT timezone FROM health_daily_metric_aggregates "
            "WHERE collector = 'health_auto_export' AND metric_family = 'steps'"
        ) == "-07:00"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_hae_fixed_sample_offset_remains_visible_across_dst_day_boundary():
    from app.services.apple_health import (
        convert_health_auto_export,
        get_apple_health_summary,
        ingest_apple_health_payload,
    )

    conn = await _connect()
    try:
        user_id, _sync_id = await _fresh_schema_with_user(conn)
        now = datetime(2026, 3, 30, tzinfo=timezone.utc)
        payload = convert_health_auto_export(
            {
                "data": {
                    "metrics": [
                        {
                            "name": "step_count",
                            "units": "count",
                            "data": [
                                {
                                    "date": "2026-03-29 23:00:00 +0300",
                                    "qty": 7654,
                                }
                            ],
                        }
                    ]
                }
            },
            telegram_user_id=999,
            automation_period="yesterday",
            snapshot_timezone="Europe/Kyiv",
            snapshot_generated_at="2026-03-29T21:00:00Z",
            now=now,
        )
        await ingest_apple_health_payload(conn, payload, now=now)

        # Europe/Kyiv's 2026-03-29 calendar day spans 22:00Z..21:00Z because
        # the offset changes from +02 to +03 after local midnight.
        summary = await get_apple_health_summary(
            conn,
            user_id,
            start_at=datetime(2026, 3, 28, 22, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 3, 29, 21, 0, tzinfo=timezone.utc),
        )

        assert summary["steps"] == 7654
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_reader_skips_nonfinite_legacy_raw_metric_without_crashing():
    from app.services.apple_health import get_apple_health_summary

    conn = await _connect()
    try:
        user_id, _sync_id = await _fresh_schema_with_user(conn)
        await conn.execute(
            """INSERT INTO health_data
                      (user_id, source, metric_type, value, unit, recorded_at)
               VALUES ($1, 'apple_health', 'step_count', 'NaN'::numeric,
                       'count', '2026-07-11T08:00:00+03:00'),
                      ($1, 'apple_health', 'step_count', 123,
                       'count', '2026-07-11T09:00:00+03:00')""",
            user_id,
        )
        start_at = datetime(2026, 7, 10, 21, 0, tzinfo=timezone.utc)

        summary = await get_apple_health_summary(
            conn,
            user_id,
            start_at=start_at,
            end_at=start_at + timedelta(days=1),
        )

        assert summary["steps"] == 123
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
        raw = await conn.fetchval("SELECT count(*) FROM health_daily_metric_aggregates")
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

        # The genuine gap day was filled into the v3 transition collector.
        gap = await conn.fetchrow(
            "SELECT total_value, samples_aggregated, metrics, collector "
            "FROM health_daily_metric_aggregates "
            "WHERE metric_date = '2026-07-10' AND metric_family = 'steps' "
            "AND collector = 'legacy_backfill'"
        )
        assert gap["total_value"] == 100
        gap_blob = json.loads(gap["metrics"]) if isinstance(gap["metrics"], str) else gap["metrics"]
        assert gap_blob["records_by_type"] == {"step_count": 1}

        # Expand mode is non-destructive by default.
        raw_left = await conn.fetchval(
            "SELECT count(*) FROM health_data WHERE user_id = $1 AND source = 'apple_health'",
            user_id,
        )
        assert raw_left == 4

        # The separate contract phase removes only the rows selected in its
        # transaction after replay-verifying the same aggregate families.
        results, failures = await backfill_all(
            conn, timezone_str="UTC", delete_raw=True
        )
        assert failures == []
        assert results[0]["deleted"] == 4
        assert await conn.fetchval(
            "SELECT count(*) FROM health_data WHERE user_id = $1 AND source = 'apple_health'",
            user_id,
        ) == 0
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
        assert raw_left == 1
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
            "SELECT total_value FROM health_daily_metric_aggregates "
            "WHERE user_id = $1 AND collector = 'legacy_backfill' "
            "AND metric_family = 'steps'",
            good_user,
        )
        assert good_steps == 42

        # The failure was collected and reported, and its transaction rolled back
        # (no aggregate row, raw rows still present so a re-run can retry it).
        assert [f["user_id"] for f in failures] == [bad_user]
        bad_agg = await conn.fetchval(
            "SELECT count(*) FROM health_daily_metric_aggregates "
            "WHERE user_id = $1 AND collector = 'legacy_backfill'",
            bad_user,
        )
        assert bad_agg == 0
        bad_raw = await conn.fetchval(
            "SELECT count(*) FROM health_data WHERE user_id = $1 AND source = 'apple_health'",
            bad_user,
        )
        assert bad_raw == 1
    finally:
        await conn.close()
