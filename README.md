# 🏃 Health & Wellness Tracker Bot

[🇺🇦 Українська версія](README.uk.md)

Telegram Web App for tracking calories, physical activity, and mood with FatSecret and WHOOP API integration.

## 📋 Description

The system allows you to:
- 🎤 Log food via voice messages
- 🍎 Automatically determine calorie content (FatSecret API)
- 💪 Sync workout data from WHOOP
- 📊 Receive daily reports on calorie balance
- 😊 Keep a mood and wellness journal

## 🏗 Architecture

```
Telegram Bot → FastAPI (Python) → APIs (FatSecret, WHOOP, OpenAI) → PostgreSQL
```

## 📁 Project Structure

```
health-tracker/
├── app/                  # FastAPI Python application
│   ├── routers/          # API route handlers
│   ├── services/         # Business logic
│   ├── main.py           # App entrypoint
│   └── scheduler.py      # Periodic jobs
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
2. Create `.env` file with credentials
3. Run database migrations
4. Start the app: `uvicorn app.main:app`

### Production database preflight

Dokploy production deploys use the Dockerfile `CMD` as the authoritative migration path. Before Uvicorn starts, the container runs:

```bash
python -m app.db_preflight --apply-apple-health-migration
```

That command applies `database/migrations/007_apple_health_connector.sql` and verifies the required Apple Health tables and indexes. FastAPI repeats the verification during startup and fails before serving traffic if `apple_health_sync`, `health_data`, `apple_health_import_logs`, or their required indexes are missing.

## 📖 Documentation

- [Getting Started](docs/en/getting-started.md)
- [API Integration](docs/en/api-integration.md)
- [Architecture](docs/en/architecture.md)
- [Design Specs](docs/design/)

## 🔗 External APIs

| API | Purpose | Documentation |
|-----|---------|---------------|
| FatSecret | Food calories | [Docs](https://platform.fatsecret.com/docs) |
| WHOOP | Workout data | [Docs](https://developer.whoop.com/docs) |
| OpenAI Whisper | Speech-to-Text | [Docs](https://platform.openai.com/docs) |
| Telegram Bot API | User interface | [Docs](https://core.telegram.org/bots/api) |

## 📄 License

MIT

---

*Created: January 2026*
