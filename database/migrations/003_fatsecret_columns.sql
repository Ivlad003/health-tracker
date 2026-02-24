-- FatSecret OAuth 1.0 columns for user food diary access
-- Version: 3.0.0
-- Created: 2026-02-24

BEGIN;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS fatsecret_access_token TEXT,
    ADD COLUMN IF NOT EXISTS fatsecret_access_secret TEXT;

COMMIT;
