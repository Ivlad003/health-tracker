-- Journal Module
-- Personal journal with mood/energy tracking and configurable reminders
-- Version: 006
-- Created: 2026-02-26

BEGIN;

-- Journal reminder settings on users table
ALTER TABLE users ADD COLUMN IF NOT EXISTS journal_time_1 TIME DEFAULT '10:00';
ALTER TABLE users ADD COLUMN IF NOT EXISTS journal_time_2 TIME DEFAULT '20:00';
ALTER TABLE users ADD COLUMN IF NOT EXISTS journal_enabled BOOLEAN DEFAULT true;

-- Journal entries table
CREATE TABLE IF NOT EXISTS journal_entries (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    mood_score INTEGER CHECK (mood_score >= 1 AND mood_score <= 10),
    energy_level INTEGER CHECK (energy_level >= 1 AND energy_level <= 10),
    tags TEXT[],
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_journal_entries_user_id ON journal_entries(user_id);
CREATE INDEX IF NOT EXISTS idx_journal_entries_created_at ON journal_entries(created_at);
CREATE INDEX IF NOT EXISTS idx_journal_entries_user_created ON journal_entries(user_id, created_at);

COMMIT;
