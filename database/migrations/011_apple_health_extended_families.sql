-- Apple Health schema v3: extended metric families.
-- Adds resting_heart_rate, body_mass, distance, and exercise_time to the
-- per-family daily aggregate table. Additive + idempotent: the old family
-- CHECK is replaced by a named v2 CHECK that is a strict superset, so existing
-- rows always satisfy it.

BEGIN;

ALTER TABLE health_daily_metric_aggregates
    DROP CONSTRAINT IF EXISTS health_daily_metric_aggregates_family_check;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'health_daily_metric_aggregates_family_check_v2'
          AND conrelid = 'health_daily_metric_aggregates'::regclass
    ) THEN
        ALTER TABLE health_daily_metric_aggregates
            ADD CONSTRAINT health_daily_metric_aggregates_family_check_v2 CHECK (
                metric_family IN (
                    'steps', 'active_energy', 'heart_rate', 'hrv', 'sleep',
                    'resting_heart_rate', 'body_mass', 'distance', 'exercise_time'
                )
            );
    END IF;
END $$;

-- Latest-body-mass lookups (BMR) scan backwards by date for one family.
CREATE INDEX IF NOT EXISTS idx_health_daily_metric_aggregates_user_family_date
    ON health_daily_metric_aggregates(user_id, metric_family, metric_date DESC);

COMMIT;
