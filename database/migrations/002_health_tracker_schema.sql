-- Health Tracker - Schema Migration
-- Extends existing database with health tracking tables
-- Existing: users (id INTEGER, telegram_user_id BIGINT), diary_entries
-- Version: 2.0.0
-- Created: 2026-02-24

BEGIN;

-- =============================================================================
-- ALTER USERS TABLE - Add WHOOP and health tracker columns
-- =============================================================================
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS telegram_username VARCHAR(100),
    ADD COLUMN IF NOT EXISTS whoop_user_id VARCHAR(100),
    ADD COLUMN IF NOT EXISTS whoop_access_token TEXT,
    ADD COLUMN IF NOT EXISTS whoop_refresh_token TEXT,
    ADD COLUMN IF NOT EXISTS whoop_token_expires_at TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS daily_calorie_goal INTEGER DEFAULT 2000,
    ADD COLUMN IF NOT EXISTS timezone VARCHAR(50) DEFAULT 'Europe/Kyiv',
    ADD COLUMN IF NOT EXISTS language VARCHAR(10) DEFAULT 'uk',
    ADD COLUMN IF NOT EXISTS settings JSONB DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW();

-- Populate telegram_username from existing username column if present
UPDATE users SET telegram_username = username WHERE telegram_username IS NULL AND username IS NOT NULL;

-- =============================================================================
-- ENUMS (create only if not exist)
-- =============================================================================
DO $$ BEGIN
    CREATE TYPE meal_type AS ENUM ('breakfast', 'lunch', 'dinner', 'snack');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE workout_score_state AS ENUM ('SCORED', 'PENDING_SCORE', 'UNSCORABLE');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE sync_type AS ENUM ('whoop_workout', 'whoop_recovery', 'whoop_sleep', 'fatsecret');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE sync_status AS ENUM ('started', 'completed', 'failed');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- =============================================================================
-- FOOD ENTRIES TABLE
-- =============================================================================
CREATE TABLE IF NOT EXISTS food_entries (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    food_name VARCHAR(255) NOT NULL,
    fatsecret_food_id VARCHAR(50),
    calories DECIMAL(10, 2) NOT NULL DEFAULT 0,
    protein DECIMAL(10, 2) DEFAULT 0,
    fat DECIMAL(10, 2) DEFAULT 0,
    carbs DECIMAL(10, 2) DEFAULT 0,
    fiber DECIMAL(10, 2) DEFAULT 0,
    serving_size DECIMAL(10, 2),
    serving_unit VARCHAR(50),
    meal_type meal_type NOT NULL,
    logged_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    source_text TEXT,
    source_audio_file_id VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_food_entries_user_id ON food_entries(user_id);
CREATE INDEX IF NOT EXISTS idx_food_entries_logged_at ON food_entries(logged_at);
-- Composite index on user_id + logged_at for daily queries
CREATE INDEX IF NOT EXISTS idx_food_entries_user_logged ON food_entries(user_id, logged_at);

-- =============================================================================
-- MOOD ENTRIES TABLE
-- =============================================================================
CREATE TABLE IF NOT EXISTS mood_entries (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    mood_score INTEGER CHECK (mood_score >= 1 AND mood_score <= 10),
    mood_description VARCHAR(255),
    energy_level INTEGER CHECK (energy_level >= 1 AND energy_level <= 10),
    sleep_quality VARCHAR(50),
    sleep_hours DECIMAL(4, 2),
    stress_level INTEGER CHECK (stress_level >= 1 AND stress_level <= 10),
    notes TEXT,
    logged_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    source_text TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mood_entries_user_id ON mood_entries(user_id);
CREATE INDEX IF NOT EXISTS idx_mood_entries_logged_at ON mood_entries(logged_at);

-- =============================================================================
-- WHOOP ACTIVITIES TABLE
-- =============================================================================
CREATE TABLE IF NOT EXISTS whoop_activities (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    whoop_workout_id VARCHAR(100) UNIQUE NOT NULL,
    sport_id INTEGER,
    sport_name VARCHAR(100) NOT NULL,
    score_state workout_score_state DEFAULT 'PENDING_SCORE',
    kilojoules DECIMAL(10, 2),
    calories DECIMAL(10, 2),
    strain DECIMAL(5, 2),
    avg_heart_rate INTEGER,
    max_heart_rate INTEGER,
    percent_recorded DECIMAL(5, 2),
    distance_meter DECIMAL(10, 2),
    altitude_gain_meter DECIMAL(10, 2),
    zone_zero_seconds INTEGER DEFAULT 0,
    zone_one_seconds INTEGER DEFAULT 0,
    zone_two_seconds INTEGER DEFAULT 0,
    zone_three_seconds INTEGER DEFAULT 0,
    zone_four_seconds INTEGER DEFAULT 0,
    zone_five_seconds INTEGER DEFAULT 0,
    started_at TIMESTAMP WITH TIME ZONE NOT NULL,
    ended_at TIMESTAMP WITH TIME ZONE NOT NULL,
    timezone_offset VARCHAR(10),
    whoop_created_at TIMESTAMP WITH TIME ZONE,
    whoop_updated_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_whoop_activities_user_id ON whoop_activities(user_id);
CREATE INDEX IF NOT EXISTS idx_whoop_activities_started_at ON whoop_activities(started_at);
CREATE INDEX IF NOT EXISTS idx_whoop_activities_whoop_id ON whoop_activities(whoop_workout_id);

-- =============================================================================
-- WHOOP RECOVERY TABLE
-- =============================================================================
CREATE TABLE IF NOT EXISTS whoop_recovery (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    whoop_cycle_id VARCHAR(100) UNIQUE NOT NULL,
    recovery_score DECIMAL(5, 2),
    resting_heart_rate DECIMAL(5, 2),
    hrv_rmssd_milli DECIMAL(10, 2),
    spo2_percentage DECIMAL(5, 2),
    skin_temp_celsius DECIMAL(5, 2),
    recorded_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_whoop_recovery_user_id ON whoop_recovery(user_id);
CREATE INDEX IF NOT EXISTS idx_whoop_recovery_recorded_at ON whoop_recovery(recorded_at);

-- =============================================================================
-- WHOOP SLEEP TABLE
-- =============================================================================
CREATE TABLE IF NOT EXISTS whoop_sleep (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    whoop_sleep_id VARCHAR(100) UNIQUE NOT NULL,
    score_state workout_score_state DEFAULT 'PENDING_SCORE',
    sleep_performance_percentage DECIMAL(5, 2),
    sleep_consistency_percentage DECIMAL(5, 2),
    sleep_efficiency_percentage DECIMAL(5, 2),
    total_sleep_time_milli BIGINT,
    total_slow_wave_sleep_milli BIGINT,
    total_rem_sleep_milli BIGINT,
    total_light_sleep_milli BIGINT,
    total_awake_milli BIGINT,
    sleep_cycle_count INTEGER,
    disturbance_count INTEGER,
    respiratory_rate DECIMAL(5, 2),
    started_at TIMESTAMP WITH TIME ZONE NOT NULL,
    ended_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_whoop_sleep_user_id ON whoop_sleep(user_id);
CREATE INDEX IF NOT EXISTS idx_whoop_sleep_started_at ON whoop_sleep(started_at);

-- =============================================================================
-- DAILY SUMMARIES TABLE
-- =============================================================================
CREATE TABLE IF NOT EXISTS daily_summaries (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    summary_date DATE NOT NULL,
    total_calories_in DECIMAL(10, 2) DEFAULT 0,
    total_protein DECIMAL(10, 2) DEFAULT 0,
    total_fat DECIMAL(10, 2) DEFAULT 0,
    total_carbs DECIMAL(10, 2) DEFAULT 0,
    total_calories_out DECIMAL(10, 2) DEFAULT 0,
    calorie_balance DECIMAL(10, 2) DEFAULT 0,
    workout_count INTEGER DEFAULT 0,
    total_workout_minutes INTEGER DEFAULT 0,
    total_strain DECIMAL(5, 2) DEFAULT 0,
    avg_mood DECIMAL(3, 1),
    avg_energy DECIMAL(3, 1),
    recovery_score DECIMAL(5, 2),
    sleep_hours DECIMAL(4, 2),
    sleep_performance DECIMAL(5, 2),
    notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(user_id, summary_date)
);

CREATE INDEX IF NOT EXISTS idx_daily_summaries_user_date ON daily_summaries(user_id, summary_date);

-- =============================================================================
-- SYNC LOG TABLE
-- =============================================================================
CREATE TABLE IF NOT EXISTS sync_logs (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    sync_type sync_type NOT NULL,
    sync_status sync_status NOT NULL,
    records_synced INTEGER DEFAULT 0,
    error_message TEXT,
    started_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    completed_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_sync_logs_user_id ON sync_logs(user_id);

-- =============================================================================
-- FUNCTIONS & TRIGGERS
-- =============================================================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Drop and recreate triggers (safe with IF EXISTS)
DROP TRIGGER IF EXISTS update_users_updated_at ON users;
CREATE TRIGGER update_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_whoop_activities_updated_at ON whoop_activities;
CREATE TRIGGER update_whoop_activities_updated_at
    BEFORE UPDATE ON whoop_activities
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_daily_summaries_updated_at ON daily_summaries;
CREATE TRIGGER update_daily_summaries_updated_at
    BEFORE UPDATE ON daily_summaries
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- =============================================================================
-- VIEWS
-- =============================================================================
CREATE OR REPLACE VIEW v_daily_calorie_balance AS
SELECT
    u.id as user_id,
    u.telegram_user_id,
    COALESCE(f.total_in, 0) as calories_in,
    COALESCE(w.total_out, 0) as calories_out,
    COALESCE(f.total_in, 0) - COALESCE(w.total_out, 0) as balance,
    u.daily_calorie_goal,
    COALESCE(f.total_in, 0) - u.daily_calorie_goal as goal_difference
FROM users u
LEFT JOIN (
    SELECT user_id, SUM(calories) as total_in
    FROM food_entries
    WHERE DATE(logged_at) = CURRENT_DATE
    GROUP BY user_id
) f ON u.id = f.user_id
LEFT JOIN (
    SELECT user_id, SUM(calories) as total_out
    FROM whoop_activities
    WHERE DATE(started_at) = CURRENT_DATE
    GROUP BY user_id
) w ON u.id = w.user_id;

COMMIT;
