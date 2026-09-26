-- Contract migration: drop tables from 002/007 that the application never
-- writes (WHOOP data is fetched live; mood lives in journal_entries).
-- Guarded: a table that unexpectedly contains rows is KEPT and reported with a
-- NOTICE instead of being dropped, so this can never destroy data. Idempotent.

BEGIN;

-- Reads whoop_activities; must go first.
DROP VIEW IF EXISTS v_daily_calorie_balance;

DO $$
DECLARE
    tbl TEXT;
    has_rows BOOLEAN;
BEGIN
    FOREACH tbl IN ARRAY ARRAY[
        'mood_entries', 'whoop_activities', 'whoop_recovery', 'whoop_sleep',
        'daily_summaries', 'sync_logs', 'health_conflicts'
    ]
    LOOP
        IF to_regclass('public.' || tbl) IS NULL THEN
            CONTINUE;
        END IF;
        EXECUTE format('LOCK TABLE %I IN ACCESS EXCLUSIVE MODE', tbl);
        EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I LIMIT 1)', tbl) INTO has_rows;
        IF has_rows THEN
            RAISE NOTICE 'Keeping %: it contains rows (export it, then drop manually)', tbl;
        ELSE
            EXECUTE format('DROP TABLE %I', tbl);
        END IF;
    END LOOP;
END $$;

-- Enum types (workout_score_state, sync_type, sync_status) are kept: 007
-- still extends sync_type and dropping types buys nothing.

COMMIT;
