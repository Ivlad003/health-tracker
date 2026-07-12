-- Rollback for 008_health_data_natural_key.sql (KRI-30 / PR #9)
-- Restores the minute-granularity UNIQUE(user_id, source, metric_type,
-- recorded_at) key and removes the widened key + records_skipped column.
--
-- WARNING: this rollback can FAIL if, after 008 shipped, rows were stored that
-- share (user_id, source, metric_type, recorded_at) but differ only by value or
-- duration_seconds -- exactly the same-second distinct samples 008 was written
-- to admit. The narrow unique index below will reject them. That data would
-- have to be de-duplicated first; there is no lossless automatic rollback once
-- distinct same-second rows exist.
--
-- Same transaction rule as the forward migration: CONCURRENTLY runs OUTSIDE the
-- BEGIN/COMMIT block.

BEGIN;

ALTER TABLE health_data
    DROP CONSTRAINT IF EXISTS health_data_natural_key;  -- also drops its backing index

ALTER TABLE apple_health_import_logs
    DROP COLUMN IF EXISTS records_skipped;

ALTER TABLE health_data
    DROP CONSTRAINT IF EXISTS health_data_duration_seconds_check;

COMMIT;

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS health_data_user_id_source_metric_type_recorded_at_key
    ON health_data (user_id, source, metric_type, recorded_at);

ALTER TABLE health_data
    ADD CONSTRAINT health_data_user_id_source_metric_type_recorded_at_key
    UNIQUE USING INDEX health_data_user_id_source_metric_type_recorded_at_key;
