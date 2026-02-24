# 🏗 Architecture

[🇺🇦 Українська версія](../uk/architecture.md)

## System Overview

Health & Wellness Tracker is built on an event-driven architecture using n8n as the central orchestrator.

```
┌─────────────────────────────────────────────────────────────────┐
│                      TELEGRAM WEB APP                            │
│                    (Frontend - React/Vue)                        │
└──────────────────────────┬──────────────────────────────────────┘
                           │ HTTPS
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                         TELEGRAM BOT                             │
│                    (Webhook Receiver)                            │
└──────────────────────────┬──────────────────────────────────────┘
                           │ Webhook
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                           n8n                                    │
│                 (Automation & Orchestration)                     │
│                                                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │  Telegram   │  │   Voice     │  │   Food      │              │
│  │  Trigger    │  │  Processing │  │   Search    │              │
│  └─────────────┘  └─────────────┘  └─────────────┘              │
│                                                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │   WHOOP     │  │   Daily     │  │   Data      │              │
│  │    Sync     │  │  Summary    │  │  Storage    │              │
│  └─────────────┘  └─────────────┘  └─────────────┘              │
└──────────────────────────┬──────────────────────────────────────┘
                           │
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
    ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
    │  FatSecret  │ │    WHOOP    │ │  PostgreSQL │
    │     API     │ │     API     │ │  Database   │
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

### 1. Telegram Web App

**Technologies:**
- React 18 + TypeScript
- Tailwind CSS
- Telegram Web App SDK

**Responsibilities:**
- UI rendering
- User interaction
- Sending commands to bot
- Real-time data display

### 2. n8n Workflows

**Main workflows:**

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| Voice Processing | Telegram voice message | Process voice messages |
| Text Processing | Telegram text message | Process text messages |
| WHOOP Sync | Schedule (15 min) | Sync WHOOP data |
| Daily Summary | Schedule (21:00) | Generate daily report |
| Weekly Report | Schedule (Sunday) | Generate weekly report |

### 3. PostgreSQL Database

**Characteristics:**
- PostgreSQL 15+
- UUID for primary keys
- JSONB for flexible data
- Indexes for query optimization

**Main tables:**
- `users` - user profiles
- `food_entries` - food records
- `whoop_activities` - workouts
- `mood_entries` - mood records
- `daily_summaries` - daily summaries

---

## Data Flow

### Voice Food Logging

```
┌──────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│ User │───▶│ Telegram │───▶│   n8n    │───▶│ OpenAI   │
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
                           ┌──────────┐    ┌──────────┐
                           │ PostgreSQL│◀──│   n8n    │
                           │          │    │          │
                           └──────────┘    └──────────┘
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

- **Telegram:** Uses `initData` for user verification
- **WHOOP:** OAuth 2.0 tokens stored encrypted
- **FatSecret:** Client credentials, no user tokens stored

### Secret Storage

All secrets stored in environment variables and n8n encrypted credentials storage.

### GDPR Compliance

- User can export all their data
- User can delete account and all data
- Minimal data collection
- Data not shared with third parties

---

## Deployment

### Docker Compose

```yaml
version: '3.8'

services:
  n8n:
    image: n8nio/n8n
    environment:
      - DB_TYPE=postgresdb
      - DB_POSTGRESDB_HOST=db
    ports:
      - "5678:5678"
    depends_on:
      - db

  db:
    image: postgres:15
    environment:
      - POSTGRES_DB=healthlog
      - POSTGRES_USER=user
      - POSTGRES_PASSWORD=pass
    volumes:
      - postgres_data:/var/lib/postgresql/data

  web:
    build: ./webapp
    ports:
      - "3000:3000"

volumes:
  postgres_data:
```

### Dokploy

The system can be deployed via Dokploy:

1. Create a new project
2. Add PostgreSQL service
3. Add n8n as Docker application
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

- n8n execution logs
- PostgreSQL query logs
- API error tracking

---

## Scaling

### Horizontal Scaling

Multiple n8n instances behind a load balancer with PostgreSQL primary/replica setup.

### Caching

- Redis for sessions and API cache
- CDN for Web App static files
