# 🏗 Architecture

[🇺🇦 Українська версія](../uk/architecture.md)

## System Overview

Health & Wellness Tracker is built as a FastAPI Python application serving as both a Telegram bot backend and API server.

```
┌─────────────────────────────────────────────────────────────────┐
│                      TELEGRAM BOT                                │
│               (python-telegram-bot v21)                          │
└──────────────────────────┬──────────────────────────────────────┘
                           │ Webhook / Polling
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                      FastAPI App                                 │
│                                                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │  Telegram   │  │   AI        │  │   Food      │              │
│  │  Bot Handler│  │  Assistant  │  │   Logging   │              │
│  └─────────────┘  └─────────────┘  └─────────────┘              │
│                                                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │   WHOOP     │  │  FatSecret  │  │  Scheduler  │              │
│  │    Sync     │  │    Sync     │  │  (APSched)  │              │
│  └─────────────┘  └─────────────┘  └─────────────┘              │
└──────────────────────────┬──────────────────────────────────────┘
                           │
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
    ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
    │  FatSecret  │ │    WHOOP    │ │  PostgreSQL │
    │     API     │ │   API v2   │ │  Database   │
    └─────────────┘ └─────────────┘ └─────────────┘
           │               │               │
           └───────────────┴───────────────┘
                           │
                           ▼
                    ┌─────────────┐
                    │   OpenAI    │
                    │   (Whisper  │
                    │    + GPT)   │
                    └─────────────┘
```

---

## Components

### 1. FastAPI Application

**Technologies:**
- Python 3.12+, FastAPI, uvicorn
- asyncpg (PostgreSQL async driver)
- python-telegram-bot v21
- APScheduler (periodic jobs)

**Modules:**
- `app/main.py` — App entrypoint, lifespan management
- `app/config.py` — Settings from environment variables
- `app/database.py` — PostgreSQL connection pool
- `app/scheduler.py` — Periodic job scheduling

### 2. Services

| Service | File | Purpose |
|---------|------|---------|
| Telegram Bot | `app/services/telegram_bot.py` | Message handling, commands |
| AI Assistant | `app/services/ai_assistant.py` | GPT intent classification + response |
| WHOOP Sync | `app/services/whoop_sync.py` | OAuth 2.0, data sync, token refresh |
| FatSecret API | `app/services/fatsecret_api.py` | OAuth 1.0, food search, diary sync |
| FatSecret Auth | `app/services/fatsecret_auth.py` | OAuth 1.0 HMAC-SHA1 signing |
| Briefings | `app/services/briefings.py` | Morning/evening scheduled messages |

### 3. API Routers

| Router | Path | Purpose |
|--------|------|---------|
| WHOOP | `app/routers/whoop.py` | `/whoop/callback` OAuth flow |
| FatSecret | `app/routers/fatsecret.py` | `/fatsecret/connect`, `/fatsecret/callback` |
| Utils | `app/routers/utils.py` | `/ip` health check |

### 4. Scheduled Jobs

| Job | Frequency | Purpose |
|-----|-----------|---------|
| WHOOP Data Sync | Every 1h | Sync workouts, sleep, recovery |
| WHOOP Token Refresh | Every 30min | Proactive token refresh |
| FatSecret Data Sync | Every 1h | Sync food diary |
| FatSecret Token Check | Every 30min | Validate tokens, notify on expiry |
| Morning Briefing | 08:00 Kyiv | Daily health summary |
| Evening Summary | 21:00 Kyiv | End-of-day report |
| Conversation Cleanup | 03:00 UTC | Remove old conversation history |

### 5. PostgreSQL Database

**Characteristics:**
- PostgreSQL 15+
- INTEGER primary keys
- asyncpg for async operations

**Main tables:**
- `users` — user profiles, OAuth tokens
- `food_entries` — food records with calories/macros
- `whoop_activities` — workouts from WHOOP
- `whoop_sleep` — sleep data
- `whoop_recovery` — recovery scores
- `conversation_messages` — chat history for GPT context

---

## Data Flow

### Voice Food Logging

```
┌──────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│ User │───▶│ Telegram │───▶│ FastAPI  │───▶│ OpenAI   │
│      │    │   Bot    │    │          │    │ Whisper  │
└──────┘    └──────────┘    └──────────┘    └──────────┘
                                 │               │
                                 │◀──────────────┘
                                 │         Text
                                 ▼
                           ┌──────────┐    ┌──────────┐
                           │  OpenAI  │───▶│ FatSecret│
                           │   GPT    │    │   API    │
                           └──────────┘    └──────────┘
                                 │               │
                                 │◀──────────────┘
                                 │       Calories
                                 ▼
                           ┌──────────┐
                           │ PostgreSQL│
                           └──────────┘
                                 │
                                 ▼
                            ┌──────────┐
                            │ Telegram │
                            │ Response │
                            └──────────┘
```

---

## Security

### Authentication

- **Telegram:** Bot token for webhook verification
- **WHOOP:** OAuth 2.0 tokens with auto-refresh
- **FatSecret:** OAuth 1.0 HMAC-SHA1 signed requests

### Secret Storage

All secrets stored in environment variables (`.env` file).

### GDPR Compliance

- User can export all their data
- User can delete account and all data
- Minimal data collection
- Data not shared with third parties

---

## Deployment

### Docker

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Dokploy

The system is deployed via Dokploy:

1. Create a new project
2. Add PostgreSQL service
3. Add the app as Docker application
4. Configure environment variables
5. Set up domain and SSL

---

## Monitoring

### Metrics

| Metric | Description | Threshold |
|--------|-------------|-----------|
| API Response Time | Response time | < 5 sec |
| Sync Success Rate | % successful syncs | > 99% |
| Error Rate | % errors | < 1% |
| Active Users | DAU/MAU | - |

### Logging

- Python `logging` module (structured logs)
- PostgreSQL query logs
- API error tracking

---

## Scaling

### Horizontal Scaling

Multiple FastAPI instances behind a load balancer with PostgreSQL primary/replica setup.

### Caching

- Redis for sessions and API cache
- CDN for Web App static files
