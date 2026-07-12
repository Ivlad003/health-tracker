-- Apple Health - Processed daily aggregates (KRI-30 / PR #9)
-- Version: 1.0
-- Created: 2026-07-12
-- Compatible with PostgreSQL 14 and 15 (plain-column UNIQUE only; no
-- NULLS NOT DISTINCT, no expression indexes, no PG15-only features).
--
-- Why: Apple Health raw-sample retention is now 0 days. Individual HealthKit
-- samples are parsed and aggregated in memory, then the processed daily result
-- is stored here -- one row per (user_id, source, metric_date). Raw `health_data`
-- is never written for source='apple_health' anymore (migration 008, which
-- widened the raw natural key, is superseded and removed).
--
-- Idempotency contract: the app upserts with
--   ON CONFLICT ON CONSTRAINT health_daily_aggregates_natural_key DO UPDATE
--   SET <columns> = EXCLUDED.<columns>
-- i.e. a *replace*, never an increment. An identical resend of a day's snapshot
-- leaves the row unchanged; a newer, fuller snapshot for the same day overwrites
-- it -- no double counting, ever.
--
-- Aggregates are kept in a dedicated table, never overloaded onto raw
-- `health_data` metric rows.

BEGIN;

CREATE TABLE IF NOT EXISTS health_daily_aggregates (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source data_source NOT NULL,
    metric_date DATE NOT NULL,
    timezone VARCHAR(64) NOT NULL,
    steps INTEGER NOT NULL DEFAULT 0,
    active_energy_kcal NUMERIC(12, 2) NOT NULL DEFAULT 0,
    avg_heart_rate NUMERIC(6, 2),
    heart_rate_samples INTEGER NOT NULL DEFAULT 0,
    avg_hrv_ms NUMERIC(8, 2),
    hrv_samples INTEGER NOT NULL DEFAULT 0,
    sleep_seconds INTEGER NOT NULL DEFAULT 0,
    samples_received INTEGER NOT NULL DEFAULT 0,
    samples_aggregated INTEGER NOT NULL DEFAULT 0,
    metrics JSONB NOT NULL DEFAULT '{}',
    snapshot_generated_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT health_daily_aggregates_natural_key UNIQUE (user_id, source, metric_date),
    CONSTRAINT health_daily_aggregates_steps_check CHECK (steps >= 0),
    CONSTRAINT health_daily_aggregates_sleep_check CHECK (sleep_seconds >= 0)
);

CREATE INDEX IF NOT EXISTS idx_health_daily_aggregates_user_id
    ON health_daily_aggregates(user_id);
CREATE INDEX IF NOT EXISTS idx_health_daily_aggregates_user_date
    ON health_daily_aggregates(user_id, metric_date);
CREATE INDEX IF NOT EXISTS idx_health_daily_aggregates_source
    ON health_daily_aggregates(source);

-- Reuse the shared updated_at trigger function (created in migration 007).
DROP TRIGGER IF EXISTS update_health_daily_aggregates_updated_at ON health_daily_aggregates;
CREATE TRIGGER update_health_daily_aggregates_updated_at
    BEFORE UPDATE ON health_daily_aggregates
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

COMMIT;
