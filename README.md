# 🏃 Health & Wellness Tracker Bot

[🇺🇦 Українська версія](README.uk.md)

Telegram bot for tracking calories, physical activity, sleep, and mood with FatSecret, WHOOP, and Apple Health integration.

## 📋 Description

The system allows you to:
- 🎤 Log food via voice messages
- 🍎 Automatically determine calorie content (FatSecret API), reusing your
  previously chosen products and your FatSecret diary history ("My Products")
- 📷 Log packaged food from a barcode photo (Open Food Facts) or a
  nutrition-label photo, with the weight you send ("135 g")
- ↩️ Undo/correct entries; every entry is stored locally and synced to FatSecret
  through a reliable outbox (no double counting)
- 💪 Read live WHOOP data (sleep, recovery, strain, workouts)
- ❤️ Import Apple Health via a ready iOS Shortcut: steps, active calories,
  sleep, HRV, resting heart rate, walking/running distance, exercise minutes,
  and weight; workouts via Health Auto Export
- 🧬 BMR (Mifflin-St Jeor) from `/profile` + your latest weight
- 🌐 Ukrainian and English interface (`/language`)
- 📊 Receive daily reports on calorie balance at 08:00 / 21:00 in your own timezone
- 😊 Keep a mood and wellness journal

## 🏗 Architecture

```
Telegram Bot (long polling) → FastAPI (Python) → APIs (FatSecret, WHOOP, OpenAI) → PostgreSQL
iPhone Shortcut ──POST──────▶ /api/v1/health/apple-health/sync ─────────────▶ PostgreSQL
Telegram Web App ─────────────▶ /api/v1/webapp/*, /api/v1/admin/* (React Mini App at /app/, see docs/en/webapp.md)
```

## 📁 Project Structure

```
health-tracker/
├── app/                  # FastAPI Python application
│   ├── routers/          # API route handlers
│   ├── services/         # Business logic (WHOOP, FatSecret, Apple Health, AI, bot)
│   ├── security.py       # Signed OAuth state, admin guard
│   ├── timeutils.py      # Per-user timezone helpers
│   ├── db_preflight.py   # Apple Health migrations + schema check
│   ├── main.py           # App entrypoint
│   └── scheduler.py      # Periodic jobs
├── docs/shortcuts/       # Apple Health Shortcut (signed + editable plist)
├── tests/                # pytest suite (+ real-PostgreSQL tests)
├── .github/specs/        # GitHub Spec Kit specifications
├── docs/                 # Bilingual documentation (uk/en)
├── database/migrations/  # SQL migrations
├── CLAUDE.md             # AI assistant instructions
├── README.md             # This file
└── README.uk.md          # Ukrainian README
```

## 🚀 Quick Start

### Prerequisites

- Python 3.12+
- PostgreSQL 15+
- API keys: Telegram, FatSecret, WHOOP, OpenAI

### Setup

1. Clone the repository
2. Copy `.env.example` to `.env` and fill in credentials. Set
   `OAUTH_STATE_SECRET` and (to use `/debug/*`) `ADMIN_API_TOKEN`.
3. Run database migrations: `bash database/init-db.sh`
4. Start the app: `uvicorn app.main:app`

### Tests

```bash
pytest
# Real-PostgreSQL Apple Health tests (they DROP the public schema!):
APPLE_HEALTH_TEST_DATABASE_URL=postgresql://postgres:test@localhost:55432/ahtest pytest tests/test_apple_health_db.py
# Real-PostgreSQL food ledger / catalog / Web App tests (also DROP the schema):
FOOD_TEST_DATABASE_URL=postgresql://postgres:test@localhost:55432/ahtest pytest tests/test_food_ledger_db.py
```

### Production database preflight

Dokploy production deploys use the Dockerfile `CMD` as the authoritative migration path. Before Uvicorn starts, the container runs:

```bash
python -m app.db_preflight --apply-apple-health-migration
```

That command applies migrations `007` and `009`–`018` (Apple Health aggregates, user profile, workouts, secret hashing, guarded cleanup of unused tables, food ledger/catalog/outbox, Web App sessions/roles/preferences) under a PostgreSQL advisory lock and verifies the required tables, indexes, and constraints. FastAPI repeats the verification during startup and fails before serving traffic if anything is missing.

## 📖 Documentation

- [Getting Started](docs/en/getting-started.md)
- [API Integration](docs/en/api-integration.md)
- [Architecture](docs/en/architecture.md)
- [Food Logging](docs/en/food-logging.md)
- [Design Specs](docs/design/)

## 🔗 External APIs

| API | Purpose | Documentation |
|-----|---------|---------------|
| FatSecret | Food calories | [Docs](https://platform.fatsecret.com/docs) |
| WHOOP | Sleep, recovery, strain, workouts | [Docs](https://developer.whoop.com/docs) |
| Apple Health | iOS Shortcut webhook (no server API) | [API Integration](docs/en/api-integration.md#apple-health-sync) |
| Open Food Facts | Packaged food by barcode (API v3.4) | [Docs](https://openfoodfacts.github.io/openfoodfacts-server/api/) |
| OpenAI Whisper / vision | Speech-to-Text, label/packaging extraction | [Docs](https://platform.openai.com/docs) |
| Telegram Bot API | User interface | [Docs](https://core.telegram.org/bots/api) |

## 📄 License

MIT

---

*Created: January 2026*
