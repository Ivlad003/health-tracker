-- Rollback for 009_health_daily_aggregates.sql (KRI-30 / PR #9)
-- Drops the processed daily aggregate table and its trigger.
--
-- WARNING: this discards every stored daily aggregate. If raw Apple Health
-- samples were already deleted (retention 0), that data is not recoverable from
-- the database. Export health_daily_aggregates first if the values matter.

BEGIN;

DROP TRIGGER IF EXISTS update_health_daily_aggregates_updated_at ON health_daily_aggregates;
DROP TABLE IF EXISTS health_daily_aggregates;

COMMIT;
