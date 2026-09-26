-- Guarded rollback for 011: refuses to run while extended-family rows exist,
-- because the restored narrow CHECK would reject them.

BEGIN;

DO $$
DECLARE
    has_extended_rows BOOLEAN;
BEGIN
    IF to_regclass('public.health_daily_metric_aggregates') IS NULL THEN
        RETURN;
    END IF;
    EXECUTE 'LOCK TABLE health_daily_metric_aggregates IN ACCESS EXCLUSIVE MODE';
    EXECUTE $q$
        SELECT EXISTS (
            SELECT 1 FROM health_daily_metric_aggregates
            WHERE metric_family IN ('resting_heart_rate', 'body_mass', 'distance', 'exercise_time')
            LIMIT 1
        )
    $q$ INTO has_extended_rows;
    IF has_extended_rows THEN
        RAISE EXCEPTION
            'Refusing to roll back 011 while extended Apple Health family rows exist; export or delete them first';
    END IF;

    ALTER TABLE health_daily_metric_aggregates
        DROP CONSTRAINT IF EXISTS health_daily_metric_aggregates_family_check_v2;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'health_daily_metric_aggregates_family_check'
          AND conrelid = 'health_daily_metric_aggregates'::regclass
    ) THEN
        ALTER TABLE health_daily_metric_aggregates
            ADD CONSTRAINT health_daily_metric_aggregates_family_check CHECK (
                metric_family IN ('steps', 'active_energy', 'heart_rate', 'hrv', 'sleep')
            );
    END IF;
END $$;

DROP INDEX IF EXISTS idx_health_daily_metric_aggregates_user_family_date;

COMMIT;
