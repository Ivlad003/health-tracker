-- User profile fields for the BMR (Mifflin-St Jeor) estimate.
-- Additive + idempotent. Set via the /profile bot command.

BEGIN;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS birth_year SMALLINT,
    ADD COLUMN IF NOT EXISTS sex VARCHAR(10),
    ADD COLUMN IF NOT EXISTS height_cm NUMERIC(5, 1);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'users_profile_check' AND conrelid = 'users'::regclass
    ) THEN
        ALTER TABLE users ADD CONSTRAINT users_profile_check CHECK (
            (birth_year IS NULL OR birth_year BETWEEN 1900 AND 2100)
            AND (sex IS NULL OR sex IN ('male', 'female'))
            AND (height_cm IS NULL OR height_cm BETWEEN 50 AND 272)
        );
    END IF;
END $$;

-- users.language already exists (002, default 'uk'); widen allowed values in
-- the app only (uk/en).

COMMIT;
