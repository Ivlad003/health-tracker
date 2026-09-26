-- Store only SHA-256 hashes of Apple Health webhook secrets.
-- Idempotent: already-hashed values ('sha256:...') are left untouched.
-- Existing Shortcut URLs keep working because the server hashes the provided
-- token before comparing. IRREVERSIBLE: there is no rollback; a user who needs
-- the plaintext again runs /connect_apple_health to get a new URL.

BEGIN;

UPDATE apple_health_sync
SET secret_key = 'sha256:' || encode(sha256(convert_to(secret_key, 'UTF8')), 'hex'),
    updated_at = NOW()
WHERE secret_key NOT LIKE 'sha256:%';

COMMIT;
