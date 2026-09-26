-- Telegram Web App sessions, server-side roles, typed preferences, goal
-- history, feature flags, admin audit and notification de-duplication
-- (docs/en/plans/2026-09-26-food-history-photo-barcode.md §15, §16).
-- Additive + idempotent.

BEGIN;

-- Typed, versioned user preferences. Canonical columns on `users`
-- (language, timezone, daily_calorie_goal, journal_*) stay authoritative.
CREATE TABLE IF NOT EXISTS user_preferences (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    prefs JSONB NOT NULL DEFAULT '{}',
    version INTEGER NOT NULL DEFAULT 1,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT user_preferences_object_check CHECK (jsonb_typeof(prefs) = 'object')
);

-- Date-effective nutrition goals (historical comparisons use the goal that
-- was effective on that local day).
CREATE TABLE IF NOT EXISTS user_goal_history (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    effective_date DATE NOT NULL,
    calories INTEGER NOT NULL,
    protein_g NUMERIC(7, 1),
    fat_g NUMERIC(7, 1),
    carbs_g NUMERIC(7, 1),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT user_goal_history_natural_key UNIQUE (user_id, effective_date),
    CONSTRAINT user_goal_history_values_check CHECK (
        calories BETWEEN 500 AND 10000
        AND (protein_g IS NULL OR protein_g BETWEEN 0 AND 1000)
        AND (fat_g IS NULL OR fat_g BETWEEN 0 AND 1000)
        AND (carbs_g IS NULL OR carbs_g BETWEEN 0 AND 2000)
    )
);

-- Revocable Web App sessions; only SHA-256 hashes of tokens are stored.
CREATE TABLE IF NOT EXISTS webapp_sessions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash CHAR(64) NOT NULL,
    csrf_hash CHAR(64) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    last_seen_at TIMESTAMP WITH TIME ZONE,
    revoked_at TIMESTAMP WITH TIME ZONE,
    CONSTRAINT webapp_sessions_token_hash_key UNIQUE (token_hash)
);
CREATE INDEX IF NOT EXISTS idx_webapp_sessions_user ON webapp_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_webapp_sessions_expires ON webapp_sessions(expires_at);

-- Server-side roles. Being admin does not grant access to other users' diaries.
CREATE TABLE IF NOT EXISTS user_roles (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role VARCHAR(16) NOT NULL,
    granted_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    granted_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMP WITH TIME ZONE,
    CONSTRAINT user_roles_pkey PRIMARY KEY (user_id, role),
    CONSTRAINT user_roles_role_check CHECK (role IN ('admin'))
);

-- Runtime feature flags (typed allowlist in app/services/feature_flags.py).
CREATE TABLE IF NOT EXISTS feature_flags (
    key VARCHAR(64) PRIMARY KEY,
    enabled BOOLEAN NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    updated_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- Compact admin audit: actor, target, revision, result. No provider bodies,
-- no private diary content.
CREATE TABLE IF NOT EXISTS admin_audit_log (
    id SERIAL PRIMARY KEY,
    actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action VARCHAR(64) NOT NULL,
    target_type VARCHAR(32) NOT NULL,
    target_id VARCHAR(64),
    revision INTEGER,
    result VARCHAR(16) NOT NULL,
    details JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_admin_audit_log_created ON admin_audit_log(created_at DESC);

-- Notification de-duplication by user / kind / local date.
CREATE TABLE IF NOT EXISTS notification_sends (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind VARCHAR(32) NOT NULL,
    local_date DATE NOT NULL,
    sent_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT notification_sends_pkey PRIMARY KEY (user_id, kind, local_date)
);

COMMIT;
