-- Web App hardening: initData replay bookkeeping and indexes for per-user
-- lookups that the Mini App performs on every screen. Additive + idempotent.

BEGIN;

-- The HMAC `hash` of the initData a session was created from. Re-using the
-- same initData revokes the previous session and is capped
-- (app.services.webapp_auth.INIT_DATA_MAX_USES).
ALTER TABLE webapp_sessions ADD COLUMN IF NOT EXISTS init_data_hash CHAR(64);
CREATE INDEX IF NOT EXISTS idx_webapp_sessions_init_data
    ON webapp_sessions(init_data_hash) WHERE init_data_hash IS NOT NULL;

-- /integrations and the FatSecret disconnect filter the outbox by user.
CREATE INDEX IF NOT EXISTS idx_food_sync_outbox_user_status
    ON food_sync_outbox(user_id, status);

-- Product detail lists the rules that point at one product.
CREATE INDEX IF NOT EXISTS idx_food_default_rules_user_product
    ON food_default_rules(user_id, product_id);

-- FK used by ON DELETE SET NULL.
CREATE INDEX IF NOT EXISTS idx_admin_audit_log_actor
    ON admin_audit_log(actor_user_id);

COMMIT;
