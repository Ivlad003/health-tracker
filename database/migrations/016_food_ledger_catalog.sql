-- Food ledger, personal catalog and FatSecret sync outbox
-- (docs/en/plans/2026-09-26-food-history-photo-barcode.md §6, §7, §14).
--
-- Additive + idempotent: safe on fresh installs, on upgrades from 015 and on
-- repeated preflight runs. INTEGER/SERIAL keys only.
--
-- Storage policy: FatSecret identifiers (food_id, serving_id, food_entry_id)
-- are stored indefinitely; other FatSecret-returned data (names, nutrition)
-- is cached with an expiry (`expires_at` / `provider_cached_until` /
-- `nutrition_expires_at`) and purged by the scheduler. Open Food Facts,
-- label and manual data are durable revisions with provenance.

BEGIN;

-- Self-sufficient on databases that only ran the preflight chain: the
-- enum and the legacy ledger table come from 002 in production.
DO $$ BEGIN
    CREATE TYPE meal_type AS ENUM ('breakfast', 'lunch', 'dinner', 'snack');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS food_entries (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    food_name VARCHAR(255) NOT NULL,
    fatsecret_food_id VARCHAR(50),
    calories DECIMAL(10, 2),
    protein DECIMAL(10, 2),
    fat DECIMAL(10, 2),
    carbs DECIMAL(10, 2),
    fiber DECIMAL(10, 2),
    serving_size DECIMAL(10, 2),
    serving_unit VARCHAR(50),
    meal_type meal_type NOT NULL,
    logged_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    source_text TEXT,
    source_audio_file_id VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Products (shared provider identities + personal products)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS food_products (
    id SERIAL PRIMARY KEY,
    owner_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    provider VARCHAR(16) NOT NULL,
    external_id VARCHAR(64),
    barcode VARCHAR(14),
    barcode_symbology VARCHAR(8),
    name VARCHAR(255),
    brand VARCHAR(255),
    preparation VARCHAR(16) NOT NULL DEFAULT 'unknown',
    provider_name VARCHAR(255),
    provider_brand VARCHAR(255),
    provider_cached_until TIMESTAMP WITH TIME ZONE,
    is_starter BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(16) NOT NULL DEFAULT 'active',
    version INTEGER NOT NULL DEFAULT 1,
    created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT food_products_provider_check CHECK (
        provider IN ('fatsecret', 'off', 'label', 'manual', 'recipe')
    ),
    CONSTRAINT food_products_preparation_check CHECK (
        preparation IN ('raw', 'cooked', 'as_sold', 'prepared', 'unknown')
    ),
    CONSTRAINT food_products_status_check CHECK (status IN ('active', 'archived')),
    CONSTRAINT food_products_barcode_check CHECK (barcode IS NULL OR barcode ~ '^[0-9]{8,14}$'),
    -- Personal user data (label/manual/recipe) always has an owner and a name.
    CONSTRAINT food_products_owner_check CHECK (
        provider IN ('fatsecret', 'off') OR (owner_user_id IS NOT NULL OR is_starter)
    ),
    CONSTRAINT food_products_name_check CHECK (
        provider = 'fatsecret' OR name IS NOT NULL
    )
);

-- Provider identity: one shared row per (provider, external_id); personal rows
-- are unique per owner. Products are never identified by name.
CREATE UNIQUE INDEX IF NOT EXISTS uq_food_products_shared_identity
    ON food_products(provider, external_id)
    WHERE owner_user_id IS NULL AND external_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_food_products_personal_identity
    ON food_products(owner_user_id, provider, external_id)
    WHERE owner_user_id IS NOT NULL AND external_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_food_products_barcode ON food_products(barcode)
    WHERE barcode IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_food_products_owner ON food_products(owner_user_id)
    WHERE owner_user_id IS NOT NULL;

DROP TRIGGER IF EXISTS update_food_products_updated_at ON food_products;
CREATE TRIGGER update_food_products_updated_at
    BEFORE UPDATE ON food_products
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ---------------------------------------------------------------------------
-- Nutrition revisions
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS food_nutrition_versions (
    id SERIAL PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES food_products(id) ON DELETE CASCADE,
    owner_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    basis_quantity NUMERIC(10, 3) NOT NULL,
    basis_unit VARCHAR(8) NOT NULL,
    grams_per_basis NUMERIC(10, 3),
    serving_id VARCHAR(32),
    serving_description VARCHAR(255),
    fatsecret_units_per_basis NUMERIC(12, 4),
    energy_kcal NUMERIC(10, 3),
    protein_g NUMERIC(10, 3),
    fat_g NUMERIC(10, 3),
    carbs_g NUMERIC(10, 3),
    fiber_g NUMERIC(10, 3),
    sugar_g NUMERIC(10, 3),
    salt_g NUMERIC(10, 3),
    source VARCHAR(16) NOT NULL,
    source_revision VARCHAR(64),
    storage_policy VARCHAR(16) NOT NULL DEFAULT 'durable',
    is_current BOOLEAN NOT NULL DEFAULT TRUE,
    retrieved_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMP WITH TIME ZONE,
    created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT food_nutrition_versions_unit_check CHECK (basis_unit IN ('g', 'ml', 'serving')),
    CONSTRAINT food_nutrition_versions_source_check CHECK (
        source IN ('fatsecret', 'off', 'label', 'manual', 'recipe')
    ),
    CONSTRAINT food_nutrition_versions_policy_check CHECK (
        storage_policy IN ('durable', 'provider_cache')
        AND (storage_policy = 'durable' OR expires_at IS NOT NULL)
    ),
    CONSTRAINT food_nutrition_versions_values_check CHECK (
        basis_quantity > 0
        AND (grams_per_basis IS NULL OR grams_per_basis > 0)
        AND (energy_kcal IS NULL OR energy_kcal >= 0)
        AND (protein_g IS NULL OR protein_g >= 0)
        AND (fat_g IS NULL OR fat_g >= 0)
        AND (carbs_g IS NULL OR carbs_g >= 0)
        AND (fiber_g IS NULL OR fiber_g >= 0)
        AND (sugar_g IS NULL OR sugar_g >= 0)
        AND (salt_g IS NULL OR salt_g >= 0)
    )
);

CREATE INDEX IF NOT EXISTS idx_food_nutrition_versions_product
    ON food_nutrition_versions(product_id, is_current);
CREATE INDEX IF NOT EXISTS idx_food_nutrition_versions_expires
    ON food_nutrition_versions(expires_at) WHERE expires_at IS NOT NULL;
-- One current FatSecret cache row per product/serving (shared) and one
-- current durable revision per product/owner scope.
CREATE UNIQUE INDEX IF NOT EXISTS uq_food_nutrition_current_serving
    ON food_nutrition_versions(product_id, COALESCE(owner_user_id, 0), COALESCE(serving_id, ''))
    WHERE is_current;

-- ---------------------------------------------------------------------------
-- My Products membership (FatSecret history / bot / manual / web / starter)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_product_memberships (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES food_products(id) ON DELETE CASCADE,
    origin VARCHAR(24) NOT NULL,
    state VARCHAR(16) NOT NULL DEFAULT 'active',
    display_name VARCHAR(255),
    preparation VARCHAR(16),
    preferred_serving_id VARCHAR(32),
    known_serving_ids JSONB NOT NULL DEFAULT '[]',
    usual_portion_g NUMERIC(10, 2),
    overrides JSONB NOT NULL DEFAULT '{}',
    confirmed_count INTEGER NOT NULL DEFAULT 0,
    history_count INTEGER NOT NULL DEFAULT 0,
    last_used_at TIMESTAMP WITH TIME ZONE,
    history_last_date DATE,
    provider_rank INTEGER,
    excluded_at TIMESTAMP WITH TIME ZONE,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT user_product_memberships_natural_key UNIQUE (user_id, product_id),
    CONSTRAINT user_product_memberships_origin_check CHECK (
        origin IN ('fatsecret_history', 'bot', 'manual', 'web', 'starter', 'label', 'barcode')
    ),
    CONSTRAINT user_product_memberships_state_check CHECK (
        state IN ('active', 'excluded', 'archived')
        AND ((state = 'excluded') = (excluded_at IS NOT NULL))
    ),
    CONSTRAINT user_product_memberships_portion_check CHECK (
        usual_portion_g IS NULL OR (usual_portion_g > 0 AND usual_portion_g <= 5000)
    )
);

CREATE INDEX IF NOT EXISTS idx_user_product_memberships_user_state
    ON user_product_memberships(user_id, state);

DROP TRIGGER IF EXISTS update_user_product_memberships_updated_at ON user_product_memberships;
CREATE TRIGGER update_user_product_memberships_updated_at
    BEFORE UPDATE ON user_product_memberships
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ---------------------------------------------------------------------------
-- Default-product rules and learned aliases
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS food_default_rules (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    alias_normalized VARCHAR(255) NOT NULL,
    alias_display VARCHAR(255) NOT NULL,
    product_id INTEGER NOT NULL REFERENCES food_products(id) ON DELETE CASCADE,
    serving_id VARCHAR(32),
    preparation VARCHAR(16),
    suggested_portion_g NUMERIC(10, 2),
    origin VARCHAR(16) NOT NULL DEFAULT 'manual',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    priority INTEGER NOT NULL DEFAULT 0,
    use_count INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT food_default_rules_origin_check CHECK (origin IN ('manual', 'learned')),
    CONSTRAINT food_default_rules_alias_check CHECK (length(alias_normalized) > 0),
    CONSTRAINT food_default_rules_portion_check CHECK (
        suggested_portion_g IS NULL OR (suggested_portion_g > 0 AND suggested_portion_g <= 5000)
    )
);

-- One active pinned (manual) default per user + alias; learned aliases are
-- unique per user + alias + product.
CREATE UNIQUE INDEX IF NOT EXISTS uq_food_default_rules_manual
    ON food_default_rules(user_id, alias_normalized)
    WHERE enabled AND origin = 'manual';
CREATE UNIQUE INDEX IF NOT EXISTS uq_food_default_rules_learned
    ON food_default_rules(user_id, alias_normalized, product_id)
    WHERE origin = 'learned';
CREATE INDEX IF NOT EXISTS idx_food_default_rules_user ON food_default_rules(user_id, enabled);

DROP TRIGGER IF EXISTS update_food_default_rules_updated_at ON food_default_rules;
CREATE TRIGGER update_food_default_rules_updated_at
    BEFORE UPDATE ON food_default_rules
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ---------------------------------------------------------------------------
-- Drafts (shared by bot and Web App)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS food_log_drafts (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    chat_id BIGINT,
    message_id BIGINT,
    reply_message_id BIGINT,
    media_group_id VARCHAR(64),
    origin VARCHAR(16) NOT NULL,
    state VARCHAR(16) NOT NULL DEFAULT 'received',
    version INTEGER NOT NULL DEFAULT 1,
    items JSONB NOT NULL DEFAULT '[]',
    media JSONB NOT NULL DEFAULT '[]',
    meal_type meal_type,
    local_date DATE,
    commit_key VARCHAR(96) NOT NULL,
    committed_entry_ids INTEGER[] NOT NULL DEFAULT '{}',
    error VARCHAR(64),
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW() + INTERVAL '24 hours',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT food_log_drafts_state_check CHECK (
        state IN ('received', 'recognizing', 'needs_product', 'needs_weight', 'needs_label',
                  'ready', 'committed', 'cancelled', 'expired', 'failed')
    ),
    CONSTRAINT food_log_drafts_origin_check CHECK (
        origin IN ('bot_text', 'bot_voice', 'bot_photo', 'web_manual', 'web_upload')
    ),
    CONSTRAINT food_log_drafts_commit_key UNIQUE (commit_key)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_food_log_drafts_message
    ON food_log_drafts(chat_id, message_id) WHERE message_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_food_log_drafts_media_group
    ON food_log_drafts(user_id, media_group_id) WHERE media_group_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_food_log_drafts_reply
    ON food_log_drafts(chat_id, reply_message_id) WHERE reply_message_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_food_log_drafts_open
    ON food_log_drafts(user_id, state, expires_at);

DROP TRIGGER IF EXISTS update_food_log_drafts_updated_at ON food_log_drafts;
CREATE TRIGGER update_food_log_drafts_updated_at
    BEFORE UPDATE ON food_log_drafts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ---------------------------------------------------------------------------
-- food_entries: the local consumption ledger (extends 002)
-- ---------------------------------------------------------------------------
ALTER TABLE food_entries
    ADD COLUMN IF NOT EXISTS product_id INTEGER REFERENCES food_products(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS nutrition_version_id INTEGER
        REFERENCES food_nutrition_versions(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS grams NUMERIC(10, 2),
    ADD COLUMN IF NOT EXISTS quantity_source VARCHAR(16),
    ADD COLUMN IF NOT EXISTS local_date DATE,
    ADD COLUMN IF NOT EXISTS timezone VARCHAR(64),
    ADD COLUMN IF NOT EXISTS origin VARCHAR(24) NOT NULL DEFAULT 'legacy',
    ADD COLUMN IF NOT EXISTS entry_status VARCHAR(16) NOT NULL DEFAULT 'committed',
    ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(128),
    ADD COLUMN IF NOT EXISTS draft_id INTEGER REFERENCES food_log_drafts(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS preparation VARCHAR(16),
    ADD COLUMN IF NOT EXISTS nutrition_source VARCHAR(16),
    ADD COLUMN IF NOT EXISTS nutrition_expires_at TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS remote_provider VARCHAR(16),
    ADD COLUMN IF NOT EXISTS remote_food_id VARCHAR(50),
    ADD COLUMN IF NOT EXISTS remote_serving_id VARCHAR(32),
    ADD COLUMN IF NOT EXISTS remote_units NUMERIC(12, 4),
    ADD COLUMN IF NOT EXISTS remote_entry_id VARCHAR(50),
    ADD COLUMN IF NOT EXISTS sync_status VARCHAR(16) NOT NULL DEFAULT 'local_only',
    ADD COLUMN IF NOT EXISTS revision INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS voided_at TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW();

-- Unknown nutrition is NULL, not 0 (legacy rows keep their stored values).
ALTER TABLE food_entries ALTER COLUMN calories DROP NOT NULL;
ALTER TABLE food_entries ALTER COLUMN calories DROP DEFAULT;
ALTER TABLE food_entries ALTER COLUMN protein DROP DEFAULT;
ALTER TABLE food_entries ALTER COLUMN fat DROP DEFAULT;
ALTER TABLE food_entries ALTER COLUMN carbs DROP DEFAULT;
ALTER TABLE food_entries ALTER COLUMN fiber DROP DEFAULT;

DO $$ BEGIN
    ALTER TABLE food_entries ADD CONSTRAINT food_entries_ledger_check CHECK (
        origin IN ('legacy', 'bot_text', 'bot_voice', 'bot_photo', 'web_manual', 'web_upload')
        AND entry_status IN ('committed', 'voided')
        AND sync_status IN ('local_only', 'pending', 'synced', 'failed', 'unknown',
                            'not_supported', 'delete_pending', 'deleted', 'delete_failed')
        AND (quantity_source IS NULL OR quantity_source IN ('explicit', 'reused', 'label'))
        AND (grams IS NULL OR (grams > 0 AND grams <= 5000))
        AND (calories IS NULL OR calories >= 0)
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_food_entries_idempotency
    ON food_entries(user_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_food_entries_remote_entry
    ON food_entries(user_id, remote_provider, remote_entry_id) WHERE remote_entry_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_food_entries_user_local_date
    ON food_entries(user_id, local_date);

-- Backfill legacy rows: local calendar date in the user's timezone.
-- Invalid stored timezones fall back to Europe/Kyiv (the historical default).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'timezone') THEN
        EXECUTE $q$
            WITH valid_tz AS (SELECT name FROM pg_timezone_names)
            UPDATE food_entries fe
            SET local_date = (fe.logged_at AT TIME ZONE COALESCE(tz.name, 'Europe/Kyiv'))::date
            FROM users u
            LEFT JOIN valid_tz tz ON tz.name = NULLIF(u.timezone, '')
            WHERE fe.user_id = u.id
              AND fe.local_date IS NULL
              AND fe.logged_at IS NOT NULL
        $q$;
    ELSE
        UPDATE food_entries
        SET local_date = (logged_at AT TIME ZONE 'Europe/Kyiv')::date
        WHERE local_date IS NULL AND logged_at IS NOT NULL;
    END IF;
END $$;

DROP TRIGGER IF EXISTS update_food_entries_updated_at ON food_entries;
CREATE TRIGGER update_food_entries_updated_at
    BEFORE UPDATE ON food_entries
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ---------------------------------------------------------------------------
-- FatSecret sync outbox
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS food_sync_outbox (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    food_entry_id INTEGER NOT NULL REFERENCES food_entries(id) ON DELETE CASCADE,
    entry_revision INTEGER NOT NULL,
    operation VARCHAR(8) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    lease_until TIMESTAMP WITH TIME ZONE,
    next_attempt_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    dispatched_at TIMESTAMP WITH TIME ZONE,
    remote_entry_id VARCHAR(50),
    last_error VARCHAR(128),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT food_sync_outbox_natural_key UNIQUE (food_entry_id, entry_revision, operation),
    CONSTRAINT food_sync_outbox_operation_check CHECK (operation IN ('create', 'edit', 'delete')),
    CONSTRAINT food_sync_outbox_status_check CHECK (
        status IN ('pending', 'sending', 'succeeded', 'failed', 'unknown', 'not_supported', 'cancelled')
    )
);

CREATE INDEX IF NOT EXISTS idx_food_sync_outbox_due
    ON food_sync_outbox(status, next_attempt_at);

DROP TRIGGER IF EXISTS update_food_sync_outbox_updated_at ON food_sync_outbox;
CREATE TRIGGER update_food_sync_outbox_updated_at
    BEFORE UPDATE ON food_sync_outbox
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ---------------------------------------------------------------------------
-- FatSecret history → My Products import jobs (resumable)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS catalog_import_jobs (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider VARCHAR(16) NOT NULL DEFAULT 'fatsecret',
    kind VARCHAR(16) NOT NULL DEFAULT 'initial',
    mode VARCHAR(16) NOT NULL DEFAULT 'auto',
    date_from DATE NOT NULL,
    date_to DATE NOT NULL,
    checkpoint_date DATE,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    days_total INTEGER NOT NULL DEFAULT 0,
    days_done INTEGER NOT NULL DEFAULT 0,
    entries_seen INTEGER NOT NULL DEFAULT 0,
    products_found INTEGER NOT NULL DEFAULT 0,
    products_added INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error VARCHAR(128),
    lease_until TIMESTAMP WITH TIME ZONE,
    next_attempt_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT catalog_import_jobs_check CHECK (
        provider = 'fatsecret'
        AND kind IN ('initial', 'range', 'refresh')
        AND mode IN ('auto', 'selective')
        AND status IN ('pending', 'running', 'partial', 'completed', 'failed', 'cancelled')
        AND date_from <= date_to
    )
);

-- At most one active job per user.
CREATE UNIQUE INDEX IF NOT EXISTS uq_catalog_import_jobs_active
    ON catalog_import_jobs(user_id) WHERE status IN ('pending', 'running', 'partial');
CREATE INDEX IF NOT EXISTS idx_catalog_import_jobs_due
    ON catalog_import_jobs(status, next_attempt_at);

DROP TRIGGER IF EXISTS update_catalog_import_jobs_updated_at ON catalog_import_jobs;
CREATE TRIGGER update_catalog_import_jobs_updated_at
    BEFORE UPDATE ON catalog_import_jobs
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS catalog_import_candidates (
    id SERIAL PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES catalog_import_jobs(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES food_products(id) ON DELETE CASCADE,
    state VARCHAR(16) NOT NULL DEFAULT 'pending',
    serving_ids JSONB NOT NULL DEFAULT '[]',
    label VARCHAR(255),
    occurrences INTEGER NOT NULL DEFAULT 0,
    last_seen_date DATE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT catalog_import_candidates_natural_key UNIQUE (job_id, product_id),
    CONSTRAINT catalog_import_candidates_state_check CHECK (
        state IN ('pending', 'added', 'skipped', 'already_member', 'excluded')
    )
);

DROP TRIGGER IF EXISTS update_catalog_import_candidates_updated_at ON catalog_import_candidates;
CREATE TRIGGER update_catalog_import_candidates_updated_at
    BEFORE UPDATE ON catalog_import_candidates
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ---------------------------------------------------------------------------
-- External lookup cache (Open Food Facts hits + short negative cache)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS external_lookup_cache (
    provider VARCHAR(16) NOT NULL,
    lookup_key VARCHAR(64) NOT NULL,
    status VARCHAR(8) NOT NULL,
    payload JSONB,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT external_lookup_cache_pkey PRIMARY KEY (provider, lookup_key),
    CONSTRAINT external_lookup_cache_status_check CHECK (status IN ('hit', 'miss'))
);

COMMIT;
