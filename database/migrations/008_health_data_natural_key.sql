-- Apple Health - Widen health_data natural key (KRI-30 / PR #9)
-- Version: 1.0
-- Created: 2026-07-12
-- Requires: PostgreSQL 15+ (uses NULLS NOT DISTINCT).
--
-- Why: UNIQUE(user_id, source, metric_type, recorded_at) collapsed two
-- genuinely-distinct Apple Health samples that share the same wall-clock
-- second:
--   * step_count / active_energy pairs with different `value`
--     (e.g. 3 vs 6 steps at 09:18:00) -> one dropped as a conflict;
--   * sleep_analysis "Awake" + "In Bed" segments sharing a start `timestamp`
--     (value=0/unit=s for both, but different duration) -> the multi-hour
--     "In Bed" envelope was silently discarded, starving nightly-sleep
--     aggregation.
--
-- Fix: widen the natural key with `value` and `duration_seconds` (both
-- already-existing columns). Identical resends carry identical value AND
-- duration, so they remain a no-op ON CONFLICT DO NOTHING and stay idempotent
-- -- no arithmetic-on-conflict, no double counting.
--
-- NULLS NOT DISTINCT (not COALESCE(duration_seconds, -1)): duration_seconds is
-- NULL for step/energy metrics, and the SQL default (NULLS DISTINCT) would let
-- two NULL-duration resends insert duplicates forever. The reviewer's ruling
-- used a COALESCE expression to avoid that, but an *expression* index cannot
-- back a named UNIQUE constraint (ALTER TABLE ... ADD CONSTRAINT ... USING INDEX
-- rejects expression columns), and the app relies on
-- `ON CONFLICT ON CONSTRAINT health_data_natural_key`, which requires a real
-- constraint. NULLS NOT DISTINCT gives identical dedup semantics on plain
-- columns, so the index can be promoted to the named constraint. [DB reviewer:
-- please confirm this substitution on the re-check.]
--
-- NOTE ON TRANSACTIONS: `CREATE UNIQUE INDEX CONCURRENTLY` cannot run inside a
-- transaction block, so it is intentionally OUTSIDE the BEGIN/COMMIT below.
-- Apply this file with psql (each top-level statement then runs in its own
-- implicit transaction). health_data is a growing time-series table, so the
-- index is built CONCURRENTLY to avoid locking Apple Health sync writes during
-- the backfill.
--
-- Rollback: database/migrations/008_health_data_natural_key_rollback.sql

-- 1) Guard duration, add the import-log skipped column, and drop the old
--    minute-granularity key -- all transactional.
BEGIN;

ALTER TABLE health_data
    ADD CONSTRAINT health_data_duration_seconds_check
    CHECK (duration_seconds IS NULL OR duration_seconds >= 0);

ALTER TABLE apple_health_import_logs
    ADD COLUMN IF NOT EXISTS records_skipped INTEGER DEFAULT 0;

-- Drop the old UNIQUE(user_id, source, metric_type, recorded_at) constraint by
-- whatever name Postgres auto-assigned in migration 007 (expected:
-- health_data_user_id_source_metric_type_recorded_at_key). Discovering the name
-- dynamically guarantees the old constraint is actually removed -- leaving it in
-- place would keep rejecting the same-second distinct rows this migration exists
-- to admit.
DO $$
DECLARE
    v_constraint text;
BEGIN
    SELECT c.conname
      INTO v_constraint
      FROM pg_constraint c
     WHERE c.conrelid = 'health_data'::regclass
       AND c.contype = 'u'
       AND (
           SELECT array_agg(a.attname ORDER BY a.attname)
             FROM unnest(c.conkey) AS cols(attnum)
             JOIN pg_attribute a
               ON a.attrelid = c.conrelid
              AND a.attnum = cols.attnum
       ) = ARRAY['metric_type', 'recorded_at', 'source', 'user_id']::name[]
     LIMIT 1;

    IF v_constraint IS NOT NULL THEN
        EXECUTE format('ALTER TABLE health_data DROP CONSTRAINT %I', v_constraint);
    END IF;
END $$;

COMMIT;

-- 2) Build the widened unique index without blocking concurrent sync writes.
--    NULLS NOT DISTINCT so two NULL-duration resends still collide.
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS health_data_natural_key_idx
    ON health_data (
        user_id,
        source,
        metric_type,
        recorded_at,
        value,
        duration_seconds
    )
    NULLS NOT DISTINCT;

-- 3) Promote the (plain-column) index to the named constraint the app targets
--    via `ON CONFLICT ON CONSTRAINT health_data_natural_key`.
ALTER TABLE health_data
    ADD CONSTRAINT health_data_natural_key
    UNIQUE USING INDEX health_data_natural_key_idx;
