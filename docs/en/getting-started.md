# 🚀 Getting Started

[🇺🇦 Українська версія](../uk/getting-started.md)

## Introduction

Health & Wellness Tracker Bot helps you track calories, physical activity, and mood through a convenient Telegram interface.

## Prerequisites

### For Users
- Telegram account
- (Optional) WHOOP device for activity tracking

### For Developers
- Python 3.12+
- PostgreSQL 15+
- API keys (see below)

## Installation

### 1. Clone the Repository

```bash
git clone https://github.com/your-username/health-tracker.git
cd health-tracker
```

### 2. Configure Environment Variables

Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

```env
# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token

# FatSecret
FATSECRET_CLIENT_ID=your_client_id
FATSECRET_CLIENT_SECRET=your_client_secret

# WHOOP
WHOOP_CLIENT_ID=your_client_id
WHOOP_CLIENT_SECRET=your_client_secret

# OpenAI
OPENAI_API_KEY=your_api_key

# Database
DATABASE_URL=postgresql://user:pass@localhost:5432/healthlog
```

### 3. Set Up the Database

```bash
# Create database
createdb healthlog

# Run migrations
psql -d healthlog -f database/migrations/001_initial_schema.sql
```

### 4. Start the Application

```bash
uvicorn app.main:app --reload
```

## Getting API Keys

### Telegram Bot Token
1. Open [@BotFather](https://t.me/botfather) in Telegram
2. Create a new bot with `/newbot` command
3. Copy the token

### FatSecret API
1. Register at [platform.fatsecret.com](https://platform.fatsecret.com/register)
2. Create a new application
3. Copy Client ID and Client Secret

### WHOOP API
1. Register at [developer.whoop.com](https://developer.whoop.com)
2. Create a new application
3. Configure redirect URI
4. Copy Client ID and Client Secret

### OpenAI API
1. Log in at [platform.openai.com](https://platform.openai.com)
2. Go to API Keys
3. Create a new key

## First Interaction

### Logging Food with Voice

1. Send a voice message to the bot
2. Describe what you ate: "I had oatmeal with banana for breakfast"
3. The bot will recognize products and show calories
4. Confirm or edit

### Connecting WHOOP

1. Click "Connect WHOOP" button in settings
2. Authorize in WHOOP
3. Grant data access
4. Data will start syncing automatically

## Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Start working |
| `/help` | Help |
| `/log` | Manual logging |
| `/summary` | Daily summary |
| `/week` | Weekly report |
| `/settings` | Settings |

## Next Steps

- [API Integration](api-integration.md) - Detailed API documentation
- [Architecture](architecture.md) - How the system works
