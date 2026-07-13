-- Guarded rollback for schema-v3 Apple Health metric-family aggregates.

BEGIN;

DO $$
DECLARE
    table_has_rows BOOLEAN;
BEGIN
    IF to_regclass('public.health_daily_metric_aggregates') IS NOT NULL THEN
        EXECUTE 'LOCK TABLE health_daily_metric_aggregates IN ACCESS EXCLUSIVE MODE';
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM health_daily_metric_aggregates LIMIT 1)'
            INTO table_has_rows;
        IF table_has_rows THEN
            RAISE EXCEPTION
                'Refusing to drop nonempty health_daily_metric_aggregates; export or forward-fix first';
        END IF;
    END IF;
END $$;

DROP TABLE IF EXISTS health_daily_metric_aggregates;

COMMIT;
