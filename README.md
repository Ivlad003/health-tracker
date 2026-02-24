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
Telegram Web App → n8n Workflows → APIs (FatSecret, WHOOP, OpenAI) → PostgreSQL
```

## 📁 Project Structure

```
health-tracker/
├── .github/specs/        # GitHub Spec Kit specifications
├── docs/
│   ├── uk/               # 🇺🇦 Ukrainian documentation
│   ├── en/               # 🇬🇧 English documentation
│   └── design/pages/     # Design specifications
├── database/migrations/  # SQL migrations
├── n8n/workflows/        # n8n workflow files
├── CLAUDE.md             # AI assistant instructions
├── README.md             # This file
└── README.uk.md          # Ukrainian README
```

## 🚀 Quick Start

### Prerequisites

- n8n (self-hosted or cloud)
- PostgreSQL 15+
- API keys: Telegram, FatSecret, WHOOP, OpenAI

### Setup

1. Clone the repository
2. Create `.env` file with credentials
3. Import n8n workflows
4. Run database migrations

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
