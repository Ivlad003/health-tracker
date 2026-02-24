-- Conversation Messages for AI Bot context
-- Version: 4.0.0
-- Created: 2026-02-24

BEGIN;

CREATE TABLE IF NOT EXISTS conversation_messages (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL,
    content TEXT NOT NULL,
    intent VARCHAR(50),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conv_messages_user_created
    ON conversation_messages(user_id, created_at);

COMMIT;
