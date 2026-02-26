-- Gym Workout Module
-- Adds gym exercise logging with progression tracking
-- Version: 005
-- Created: 2026-02-26

BEGIN;

-- Add gym prompt to users table
ALTER TABLE users ADD COLUMN IF NOT EXISTS gym_prompt TEXT;

-- Gym exercises table (one row per exercise entry)
CREATE TABLE IF NOT EXISTS gym_exercises (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    exercise_name TEXT NOT NULL,
    exercise_key TEXT NOT NULL,
    weight_kg NUMERIC(6, 2),
    sets INTEGER,
    reps INTEGER,
    rpe NUMERIC(3, 1),
    notes TEXT,
    set_details JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gym_exercises_user_id ON gym_exercises(user_id);
CREATE INDEX IF NOT EXISTS idx_gym_exercises_user_key ON gym_exercises(user_id, exercise_key);
CREATE INDEX IF NOT EXISTS idx_gym_exercises_created_at ON gym_exercises(created_at);

COMMIT;
