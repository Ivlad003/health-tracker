# 🏗 Architecture

[🇺🇦 Українська версія](../uk/architecture.md)

## System Overview

Health & Wellness Tracker is built as a FastAPI Python application serving as a Telegram bot backend (long polling), an API server for OAuth callbacks and the Apple Health webhook, and the JSON backend of the Telegram Web App (`/api/v1/webapp/*`, `/api/v1/admin/*`). The Web App frontend itself is not built yet. Food logging is described in detail in [food-logging.md](food-logging.md).

```
┌─────────────────────────────────────────────────────────────────┐
│                      TELEGRAM BOT                                │
│               (python-telegram-bot v21)                          │
└──────────────────────────┬──────────────────────────────────────┘
                           │ Long polling
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
- `app/main.py` — App entrypoint, lifespan management, `/app/` placeholder or `web/dist`
- `app/config.py` — Settings from environment variables
- `app/database.py` — PostgreSQL connection pool
- `app/scheduler.py` — Periodic job scheduling
- `app/security.py` — Signed OAuth `state`, `require_admin` dependency
- `app/timeutils.py` — Per-user timezone resolution
- `app/db_preflight.py` — migrations 007, 009–017 + schema verification
- `app/crypto.py` — hashing of the Apple Health webhook secret
- `app/i18n.py` — uk/en message catalog

### 2. Services

| Service | File | Purpose |
|---------|------|---------|
| Telegram Bot | `app/services/telegram_bot.py` | Message handling, commands |
| AI Assistant | `app/services/ai_assistant.py` | GPT intent classification + response |
| WHOOP Sync | `app/services/whoop_sync.py` | OAuth 2.0, live context (120 s cache), locked token refresh |
| FatSecret API | `app/services/fatsecret_api.py` | OAuth 2.0 search (cached token), OAuth 1.0 diary |
| FatSecret Auth | `app/services/fatsecret_auth.py` | OAuth 1.0 HMAC-SHA1 signing |
| Apple Health | `app/services/apple_health.py` | Schema-v3 validation, per-family aggregation, read overlay |
| Briefings | `app/services/briefings.py` | Morning/evening user-local messages, journal reminders |
| Gym / Journal | `app/services/gym_service.py`, `journal_service.py` | Exercise log, journal entries |
| Workouts | `app/services/health_workouts.py` | Apple Health workout events |
| BMR | `app/services/bmr.py` | Mifflin-St Jeor from `/profile` + latest weight |
| Food nutrition | `app/services/food_nutrition.py` | Decimal portion math, units, gram parsing |
| Food catalog / resolver | `app/services/food_catalog.py`, `food_resolver.py` | Products, My Products, default rules, history-first matching |
| Food ledger / sync | `app/services/food_logging.py`, `food_sync.py` | Drafts, idempotent commits, daily union, FatSecret outbox + reconciliation |
| History import | `app/services/catalog_import.py` | FatSecret diary → My Products (resumable) |
| Barcode / OFF / vision | `app/services/barcode_reader.py`, `open_food_facts.py`, `food_vision.py` | Local decoding, Open Food Facts v3.4, label/plate extraction |
| Food bot flows | `app/services/food_bot.py` | Text/voice/photo/callback orchestration |
| Web App auth / prefs / flags | `app/services/webapp_auth.py`, `preferences.py`, `feature_flags.py` | initData sessions, roles, typed preferences, runtime flags |

### 3. API Routers

| Router | Path | Purpose |
|--------|------|---------|
| WHOOP | `app/routers/whoop.py` | `/whoop/callback` OAuth flow (signed state) |
| FatSecret | `app/routers/fatsecret.py` | `/fatsecret/connect`, `/fatsecret/callback` (signed state); admin `/fatsecret/diary`, `/food/search` |
| Apple Health | `app/routers/apple_health.py` | `/api/v1/health/apple-health/shortcut`, `/api/v1/health/apple-health/sync` |
| Utils | `app/routers/utils.py` | Admin-only `/ip-check`, `/debug/*` |
| Web App | `app/routers/webapp.py` | `/api/v1/webapp/*` (Telegram initData session) |
| Admin | `app/routers/admin.py` | `/api/v1/admin/*` (server-side `admin` role) |

### 4. Scheduled Jobs

| Job | Frequency | Purpose |
|-----|-----------|---------|
| WHOOP Token Refresh | Every 30min | Refresh tokens expiring within 10 min |
| FatSecret Token Check | Every 3h | Validate tokens, notify on revocation |
| Morning Briefing | Every 5 min → user's configured local time (default 08:00), once per local date | Daily health summary |
| Evening Summary | Every 5 min → user's configured local time (default 21:00), once per local date | End-of-day report |
| Food outbox / reconcile | 1 min / 10 min | FatSecret diary writes; ambiguous writes reconciled |
| FatSecret history import / refresh | 5 min / 04:00 UTC | My Products population |
| Food cache purge | Hourly | ≤24 h FatSecret cache, expired drafts, sessions |
| Journal Reminders | Every 10min | User-configured times, user-local |
| Conversation Cleanup | 03:00 UTC | Remove old conversation history |

WHOOP and FatSecret data are fetched live; Apple Health is pushed by the iPhone.

### 5. PostgreSQL Database

**Characteristics:**
- PostgreSQL 15+
- INTEGER primary keys
- asyncpg for async operations

**Main tables (written by the app):**
- `users` — user profiles, OAuth tokens, `timezone`
- `food_entries` — the local food ledger (every confirmed entry; remote ids + sync status)
- `food_products`, `food_nutrition_versions`, `user_product_memberships`, `food_default_rules`,
  `food_log_drafts`, `food_sync_outbox`, `catalog_import_jobs`/`_candidates`,
  `external_lookup_cache` (migration 016)
- `user_preferences`, `user_goal_history`, `webapp_sessions`, `user_roles`, `feature_flags`,
  `admin_audit_log`, `notification_sends` (migration 017)
- `conversation_messages` — chat history for GPT context
- `gym_exercises`, `journal_entries`
- `apple_health_sync`, `apple_health_import_logs`
- `health_daily_metric_aggregates` — Apple Health per-family daily values
  (steps, active_energy, heart_rate, hrv, sleep, resting_heart_rate,
  body_mass, distance, exercise_time)
- `health_workouts` — Apple Health workouts (one row per workout)

Never-written tables from 002/007 (`whoop_*`, `daily_summaries`, `sync_logs`,
`mood_entries`, `health_conflicts`) are dropped by migration 015 when empty.

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

- **Telegram:** identity is `update.effective_user.id` from long polling
- **WHOOP / FatSecret linking:** HMAC-signed, purpose-bound, 1-hour OAuth
  `state` (`app/security.py`) — prevents account-linking CSRF
- **WHOOP:** OAuth 2.0 tokens with locked auto-refresh
- **FatSecret:** OAuth 1.0 HMAC-SHA1 signed requests
- **Apple Health webhook:** per-user random token, constant-time comparison,
  rotated by `/connect_apple_health`
- **Operator endpoints:** `ADMIN_API_TOKEN` bearer; hidden (404) when unset
- **Telegram Web App:** `initData` HMAC-validated with the bot token (≤5 min old),
  revocable 1-hour server sessions (hashed), Bearer or cookie + CSRF + Origin,
  admin role re-checked from `user_roles` on every admin request
- **Logs:** `SecretRedactingFilter` masks `token`, `code`, `state`,
  `oauth_token`, `oauth_verifier` query values (incl. uvicorn access logs)

### Secret Storage

All secrets stored in environment variables (`.env` file). In PostgreSQL,
WHOOP/FatSecret OAuth tokens are stored in plain text, and the Apple Health
webhook secret is stored only as a SHA-256 hash.

### Localization

Fixed bot texts come from `app/i18n.py` (Ukrainian and English). The language
is `users.language`, initialized from Telegram `language_code` and changed with
`/language`. Bot command menus are registered for `uk` and as the default (en).

### GDPR Compliance

- User can export all their data
- User can delete account and all data
- Minimal data collection
- Data not shared with third parties

---

## Deployment

### Docker

See the repository `Dockerfile`: it installs requirements, copies `app/`,
`database/`, and `docs/shortcuts/`, and its `CMD` runs the database preflight
before `newrelic-admin run-program uvicorn app.main:app`.

### Dokploy

The system is deployed via Dokploy:

1. Create a new project
2. Add PostgreSQL service
3. Add the app as Docker application
4. Configure environment variables
5. Set up domain and SSL

The Docker image copies `database/` into the container and uses one production migration path:

```bash
python -m app.db_preflight --apply-apple-health-migration
```

This prestart command applies migrations `007` and `009`–`015`,
then verifies `apple_health_sync`, `health_data`, `apple_health_import_logs`,
`health_daily_aggregates`, `health_daily_metric_aggregates`, and the required
indexes before Uvicorn starts. The FastAPI lifespan runs the same verifier
again; if the schema is incomplete, startup exits with a sanitized error before
the app serves traffic. A PostgreSQL session advisory lock serializes the whole
apply-and-verify sequence across replicas. The general `database/init-db.sh`
runner applies forward `*.sql` files only, skips `*_rollback.sql`, and makes
`psql` stop on the first migration error.

Apple Health schema v3 writes processed daily values at the
`collector + metric_date + metric_family` boundary. One transaction commits all
family rows, sync counters, and the sanitized import log together. Readers
filter rows by their timezone-adjusted query window, select the newest in-window
live collector independently per family, and fill only missing values from
schema-v2 aggregates, backfill rows, and legacy raw data during the
expand/migrate/contract rollout.
The ingress boundary accepts only the native `shortcut` and converted
`health_auto_export` collectors, bounds each family to 31 recent covered dates,
and rejects non-finite/out-of-domain values. Receipt time and mutable HealthKit
sample timestamps do not define HAE ordering. Converted HAE-shaped requests
must carry a client-minted, offset-aware export timestamp created before network
dispatch, plus one complete, unbatched, unaggregated metric, an attested period,
and explicit timezone. Stock direct HAE REST automations fail closed because
they do not supply that causal marker.
Destructive backfill holds a writer-blocking table lock until the residual
raw-row check commits.

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

- Python `logging` module (JSON logs to stdout, optional New Relic Log API)
- Secrets redacted by `SecretRedactingFilter`
- PostgreSQL query logs
- API error tracking

---

## Scaling

### Horizontal Scaling

Multiple FastAPI instances behind a load balancer with PostgreSQL primary/replica setup.

### Caching

- In-process caches: WHOOP live context (120 s), FatSecret OAuth 2.0 token
- Per-user WHOOP refresh locks are in-process; with several replicas, move
  them to a PostgreSQL advisory lock or Redis
