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
        "secret-key",
    )
    return user_id, sync_id


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
