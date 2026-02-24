-- Health Tracker Database Schema
-- Version: 1.0.0
-- Created: 2026-01-28

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- =============================================================================
-- USERS TABLE
-- =============================================================================
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    telegram_id VARCHAR(50) UNIQUE NOT NULL,
    telegram_username VARCHAR(100),
    whoop_user_id VARCHAR(100),
    whoop_access_token TEXT,
    whoop_refresh_token TEXT,
    whoop_token_expires_at TIMESTAMP WITH TIME ZONE,
    daily_calorie_goal INTEGER DEFAULT 2000,
    timezone VARCHAR(50) DEFAULT 'Europe/Kyiv',
    language VARCHAR(10) DEFAULT 'uk',
    settings JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_users_telegram_id ON users(telegram_id);

-- =============================================================================
-- FOOD ENTRIES TABLE
-- =============================================================================
CREATE TYPE meal_type AS ENUM ('breakfast', 'lunch', 'dinner', 'snack');

CREATE TABLE food_entries (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
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

CREATE INDEX idx_food_entries_user_id ON food_entries(user_id);
CREATE INDEX idx_food_entries_logged_at ON food_entries(logged_at);
CREATE INDEX idx_food_entries_user_date ON food_entries(user_id, DATE(logged_at));

-- =============================================================================
-- MOOD ENTRIES TABLE
-- =============================================================================
CREATE TABLE mood_entries (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
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

CREATE INDEX idx_mood_entries_user_id ON mood_entries(user_id);
CREATE INDEX idx_mood_entries_logged_at ON mood_entries(logged_at);

-- =============================================================================
-- WHOOP ACTIVITIES TABLE
-- =============================================================================
CREATE TYPE workout_score_state AS ENUM ('SCORED', 'PENDING_SCORE', 'UNSCORABLE');

CREATE TABLE whoop_activities (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
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

CREATE INDEX idx_whoop_activities_user_id ON whoop_activities(user_id);
CREATE INDEX idx_whoop_activities_started_at ON whoop_activities(started_at);
CREATE INDEX idx_whoop_activities_whoop_id ON whoop_activities(whoop_workout_id);

-- =============================================================================
-- WHOOP RECOVERY TABLE
-- =============================================================================
CREATE TABLE whoop_recovery (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    whoop_cycle_id VARCHAR(100) UNIQUE NOT NULL,
    recovery_score DECIMAL(5, 2),
    resting_heart_rate DECIMAL(5, 2),
    hrv_rmssd_milli DECIMAL(10, 2),
    spo2_percentage DECIMAL(5, 2),
    skin_temp_celsius DECIMAL(5, 2),
    recorded_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_whoop_recovery_user_id ON whoop_recovery(user_id);
CREATE INDEX idx_whoop_recovery_recorded_at ON whoop_recovery(recorded_at);

-- =============================================================================
-- WHOOP SLEEP TABLE
-- =============================================================================
CREATE TABLE whoop_sleep (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
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

CREATE INDEX idx_whoop_sleep_user_id ON whoop_sleep(user_id);
CREATE INDEX idx_whoop_sleep_started_at ON whoop_sleep(started_at);

-- =============================================================================
-- DAILY SUMMARIES TABLE
-- =============================================================================
CREATE TABLE daily_summaries (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
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

CREATE INDEX idx_daily_summaries_user_date ON daily_summaries(user_id, summary_date);

-- =============================================================================
-- SYNC LOG TABLE
-- =============================================================================
CREATE TYPE sync_type AS ENUM ('whoop_workout', 'whoop_recovery', 'whoop_sleep', 'fatsecret');
CREATE TYPE sync_status AS ENUM ('started', 'completed', 'failed');

CREATE TABLE sync_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    sync_type sync_type NOT NULL,
    sync_status sync_status NOT NULL,
    records_synced INTEGER DEFAULT 0,
    error_message TEXT,
    started_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    completed_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX idx_sync_logs_user_id ON sync_logs(user_id);

-- =============================================================================
-- FUNCTIONS
-- =============================================================================

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Triggers for updated_at
CREATE TRIGGER update_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_whoop_activities_updated_at
    BEFORE UPDATE ON whoop_activities
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_daily_summaries_updated_at
    BEFORE UPDATE ON daily_summaries
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- =============================================================================
-- VIEWS
-- =============================================================================

-- View for daily calorie balance
CREATE VIEW v_daily_calorie_balance AS
SELECT 
    u.id as user_id,
    u.telegram_id,
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
