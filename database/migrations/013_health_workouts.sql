-- Workouts imported from Apple Health (Health Auto Export workouts, or a
-- Shortcut sending the optional `workouts` array). Event rows, not daily
-- aggregates: one row per workout, upserted by a stable external id.

BEGIN;

CREATE TABLE IF NOT EXISTS health_workouts (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source data_source NOT NULL,
    collector VARCHAR(32) NOT NULL,
    external_id VARCHAR(128) NOT NULL,
    workout_type VARCHAR(64) NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE NOT NULL,
    ended_at TIMESTAMP WITH TIME ZONE NOT NULL,
    duration_seconds INTEGER NOT NULL,
    active_energy_kcal NUMERIC(10, 2),
    distance_m NUMERIC(12, 2),
    avg_heart_rate NUMERIC(6, 2),
    max_heart_rate NUMERIC(6, 2),
    timezone VARCHAR(64),
    metrics JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT health_workouts_natural_key UNIQUE (user_id, source, external_id),
    CONSTRAINT health_workouts_values_check CHECK (
        ended_at >= started_at
        AND duration_seconds BETWEEN 0 AND 86400
        AND (active_energy_kcal IS NULL OR (active_energy_kcal >= 0 AND active_energy_kcal <> 'NaN'::numeric))
        AND (distance_m IS NULL OR (distance_m >= 0 AND distance_m <> 'NaN'::numeric))
        AND (avg_heart_rate IS NULL OR avg_heart_rate BETWEEN 0 AND 300)
        AND (max_heart_rate IS NULL OR max_heart_rate BETWEEN 0 AND 300)
    )
);

CREATE INDEX IF NOT EXISTS idx_health_workouts_user_started
    ON health_workouts(user_id, started_at DESC);

DROP TRIGGER IF EXISTS update_health_workouts_updated_at ON health_workouts;
CREATE TRIGGER update_health_workouts_updated_at
    BEFORE UPDATE ON health_workouts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

COMMIT;
