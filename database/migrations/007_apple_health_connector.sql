-- Apple Health Connector - Database Migration
-- Adds tables and columns for Apple Health integration
-- Version: 1.0
-- Created: 2026-05-20

ALTER TYPE sync_type ADD VALUE IF NOT EXISTS 'apple_health';

BEGIN;

-- =============================================================================
-- ENUMS
-- =============================================================================
DO $$ BEGIN
    CREATE TYPE data_source AS ENUM ('whoop', 'apple_health', 'fatsecret', 'manual');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- =============================================================================
-- APPLE HEALTH SYNC TABLE
-- =============================================================================
CREATE TABLE IF NOT EXISTS apple_health_sync (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    is_active BOOLEAN DEFAULT TRUE,
    secret_key VARCHAR(255) NOT NULL UNIQUE,
    last_sync_at TIMESTAMP WITH TIME ZONE,
    next_sync_at TIMESTAMP WITH TIME ZONE,
    sync_frequency_hours INTEGER DEFAULT 6 CHECK (sync_frequency_hours BETWEEN 1 AND 24),
    success_count INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    last_error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(user_id)
);

CREATE INDEX IF NOT EXISTS idx_apple_health_sync_user_id ON apple_health_sync(user_id);
CREATE INDEX IF NOT EXISTS idx_apple_health_sync_secret_key ON apple_health_sync(secret_key);
CREATE INDEX IF NOT EXISTS idx_apple_health_sync_is_active ON apple_health_sync(is_active);

-- =============================================================================
-- HEALTH CONFLICTS TABLE
-- =============================================================================
CREATE TABLE IF NOT EXISTS health_conflicts (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    metric_type VARCHAR(100) NOT NULL,
    recorded_at TIMESTAMP WITH TIME ZONE NOT NULL,
    apple_health_value DECIMAL(15, 4),
    whoop_value DECIMAL(15, 4),
    conflict_resolution VARCHAR(50) DEFAULT 'apple_health_primary',
    notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(user_id, metric_type, recorded_at)
);

CREATE INDEX IF NOT EXISTS idx_health_conflicts_user_id ON health_conflicts(user_id);
CREATE INDEX IF NOT EXISTS idx_health_conflicts_recorded_at ON health_conflicts(recorded_at);
CREATE INDEX IF NOT EXISTS idx_health_conflicts_metric_type ON health_conflicts(metric_type);

-- =============================================================================
-- HEALTH DATA TABLE (NEW - unified table for all health metrics)
-- =============================================================================
CREATE TABLE IF NOT EXISTS health_data (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source data_source NOT NULL,
    metric_type VARCHAR(100) NOT NULL,
    metric_subtype VARCHAR(100),
    value DECIMAL(15, 4) NOT NULL,
    unit VARCHAR(50) NOT NULL,
    recorded_at TIMESTAMP WITH TIME ZONE NOT NULL,
    duration_seconds INTEGER,
    additional_data JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(user_id, source, metric_type, recorded_at)
);

CREATE INDEX IF NOT EXISTS idx_health_data_user_id ON health_data(user_id);
CREATE INDEX IF NOT EXISTS idx_health_data_recorded_at ON health_data(recorded_at);
CREATE INDEX IF NOT EXISTS idx_health_data_metric_type ON health_data(metric_type);
CREATE INDEX IF NOT EXISTS idx_health_data_source ON health_data(source);
CREATE INDEX IF NOT EXISTS idx_health_data_user_metric ON health_data(user_id, metric_type, recorded_at);

-- =============================================================================
-- APPLE HEALTH IMPORT LOGS TABLE
-- =============================================================================
CREATE TABLE IF NOT EXISTS apple_health_import_logs (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    sync_id INTEGER REFERENCES apple_health_sync(id) ON DELETE SET NULL,
    http_status INTEGER,
    records_received INTEGER DEFAULT 0,
    records_processed INTEGER DEFAULT 0,
    records_failed INTEGER DEFAULT 0,
    error_message TEXT,
    request_body JSONB,
    response_body JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_apple_health_import_logs_user_id ON apple_health_import_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_apple_health_import_logs_created_at ON apple_health_import_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_apple_health_import_logs_sync_id ON apple_health_import_logs(sync_id);

-- =============================================================================
-- FUNCTIONS & TRIGGERS
-- =============================================================================

-- Update trigger for apple_health_sync table
DROP TRIGGER IF EXISTS update_apple_health_sync_updated_at ON apple_health_sync;
CREATE TRIGGER update_apple_health_sync_updated_at
    BEFORE UPDATE ON apple_health_sync
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Update trigger for health_data table
DROP TRIGGER IF EXISTS update_health_data_updated_at ON health_data;
CREATE TRIGGER update_health_data_updated_at
    BEFORE UPDATE ON health_data
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

COMMIT;
