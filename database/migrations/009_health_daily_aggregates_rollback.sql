-- Guarded rollback for schema-v2 Apple Health daily aggregates.
-- Refuse data loss: export or forward-fix any stored aggregates first.

BEGIN;

DO $$
DECLARE
    table_has_rows BOOLEAN;
BEGIN
    IF to_regclass('public.health_daily_aggregates') IS NOT NULL THEN
        EXECUTE 'LOCK TABLE health_daily_aggregates IN ACCESS EXCLUSIVE MODE';
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM health_daily_aggregates LIMIT 1)'
            INTO table_has_rows;
        IF table_has_rows THEN
            RAISE EXCEPTION
                'Refusing to drop nonempty health_daily_aggregates; export or forward-fix first';
        END IF;
    END IF;
END $$;

DROP TABLE IF EXISTS health_daily_aggregates;

COMMIT;
