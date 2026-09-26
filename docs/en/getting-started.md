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

# App / security
APP_BASE_URL=https://your-domain.com
WHOOP_REDIRECT_URI=https://your-domain.com/whoop/callback
FATSECRET_SHARED_SECRET=your_oauth1_shared_secret
OAUTH_STATE_SECRET=long_random_string
ADMIN_API_TOKEN=long_random_string   # enables /debug/*; empty = 404
DEFAULT_TIMEZONE=Europe/Kyiv

# Food logging / Web App (see docs/en/food-logging.md)
OFF_USER_AGENT="HealthTrackerBot/1.0 (contact: you@example.com)"
FATSECRET_HISTORY_IMPORT_DAYS=30
WEBAPP_URL=https://your-domain.com/app/     # HTTPS, used for Web App buttons
WEBAPP_ADMIN_TELEGRAM_IDS=123456789         # owner/admin bootstrap
```

### 3. Set Up the Database

```bash
# Create database
createdb healthlog

# Run migrations
bash database/init-db.sh   # applies 002+ in order (001 is NOT used), skips *_rollback.sql
# or, for the Apple Health part only:
python -m app.db_preflight --apply-apple-health-migration
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

1. Send a text or voice message: "cooked buckwheat 180 g"
2. A previously confirmed product is logged immediately (with **Undo**); a new
   food shows up to 3 candidates — pick one and the bot remembers it
3. No weight given? The bot asks — reply "135 g" to its message
4. Barcode photo with the caption "135 g" logs the exact packaged product;
   for an unknown barcode send a nutrition-label photo as a reply
5. After `/connect_fatsecret` your FatSecret diary history fills **My Products**

### Connecting WHOOP

1. Click "Connect WHOOP" button in settings
2. Authorize in WHOOP
3. Grant data access
4. Data will start syncing automatically

## Bot Commands

| Command | Description |
|---------|-------------|
| `/start`, `/help` | Guide |
| `/connect_fatsecret`, `/connect_whoop`, `/connect_apple_health` | Connect services |
| `/sync` | Check connections |
| `/timezone`, `/language`, `/profile` | Timezone, language, BMR profile |
| `/journal`, `/journal_time`, `/journal_on`, `/journal_off` | Journal |
| `/gym_prompt` | Gym coaching profile |
| `/app` | Open the Telegram Web App (needs an HTTPS `WEBAPP_URL`) |

Food is logged by plain messages, voice and photos (no command needed).

## Next Steps

- [API Integration](api-integration.md) - Detailed API documentation
- [Architecture](architecture.md) - How the system works
- [Food Logging](food-logging.md) - History, barcodes, label photos, Web App API
