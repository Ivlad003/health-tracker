# CLAUDE.md - AI Assistant Instructions

## Project Overview

**Health & Wellness Tracker Bot** - Telegram bot (long polling) for tracking calories, physical activity, sleep, and mood with FatSecret, WHOOP, and Apple Health (iOS Shortcut webhook) integration. Food logging is history-first (FatSecret diary → "My Products", pinned defaults, learned choices) with barcode/label photos and a local ledger + FatSecret outbox. The Telegram Web App backend API (`/api/v1/webapp/*`, `/api/v1/admin/*`) and its Vite + React UI (`web/`, served from `web/dist` at `/app/`) are implemented. Screen layout follows `docs/design/`. See [`docs/en/food-logging.md`](docs/en/food-logging.md).

---

## 🌐 BILINGUAL DOCUMENTATION REQUIREMENTS

### ⚠️ CRITICAL: All documentation MUST be maintained in TWO languages

This project uses **bilingual documentation** (Ukrainian 🇺🇦 and English 🇬🇧).

### Rules for maintaining documentation:

1. **Every documentation file must exist in both languages:**
   - Ukrainian version: `docs/uk/filename.md`
   - English version: `docs/en/filename.md`

2. **When creating new documentation:**
   - ALWAYS create both language versions simultaneously
   - Use the same file structure in both `docs/uk/` and `docs/en/`
   - Keep content synchronized between versions

3. **When updating documentation:**
   - Update BOTH language versions
   - If you update `docs/uk/api.md`, you MUST also update `docs/en/api.md`
   - Mark sections as `[NEEDS_TRANSLATION]` if temporary async update is needed

4. **File naming convention:**
   - Use English file names for both versions
   - Example: `docs/uk/getting-started.md` and `docs/en/getting-started.md`

5. **README files:**
   - Root `README.md` - English (primary)
   - `README.uk.md` - Ukrainian version in root

6. **Code comments:**
   - Code comments should be in English
   - User-facing strings should support i18n

### Documentation structure:
```
docs/
├── uk/                    # 🇺🇦 Ukrainian documentation
│   ├── README.md
│   ├── getting-started.md
│   ├── api-integration.md
│   ├── architecture.md
│   ├── critical-issues.md
│   └── session-knowledge.md
├── en/                    # 🇬🇧 English documentation
│   ├── README.md
│   ├── getting-started.md
│   ├── api-integration.md
│   ├── architecture.md
│   ├── critical-issues.md
│   └── session-knowledge.md
└── design/               # Design specs (bilingual in single files)
    ├── README.md          # Design system & components
    └── pages/
        ├── 01-dashboard.md
        ├── 02-food-log.md
        ├── 03-activity.md
        ├── 04-history.md
        └── 05-profile.md
```

---

## 📁 Project Structure

```
health-tracker/
├── .github/
│   └── specs/            # GitHub Spec Kit specifications
├── docs/
│   ├── uk/               # Ukrainian docs
│   ├── en/               # English docs
│   └── design/           # Design specifications
│       └── pages/        # Page-by-page design specs
│   └── shortcuts/        # Apple Health Shortcut: signed .shortcut + editable .plist
├── database/
│   ├── init-db.sh        # DB initialization script (Docker psql fallback)
│   └── migrations/       # SQL migrations 001–017 (008 intentionally absent, 001 never applied)
├── app/                  # FastAPI Python application
│   ├── routers/          # whoop, fatsecret, apple_health, utils (admin-only), webapp, admin (Web App)
│   ├── services/         # WHOOP, FatSecret, Apple Health, AI, Telegram, briefings, gym, journal,
│   │                     #   food_* (nutrition/catalog/resolver/logging/sync/bot/vision), catalog_import,
│   │                     #   barcode_reader, open_food_facts, preferences, feature_flags, webapp_auth
│   ├── config.py         # Settings & environment variables
│   ├── database.py       # PostgreSQL connection pool
│   ├── security.py       # Signed OAuth state + require_admin
│   ├── timeutils.py      # resolve_timezone() — never hard-code Europe/Kyiv
│   ├── crypto.py         # hash_secret/verify_secret (Apple Health webhook secret)
│   ├── i18n.py           # t(key, lang) uk/en catalog
│   ├── db_preflight.py   # Migrations 007, 009–017 + verification (Docker CMD)
│   ├── backfill_apple_health.py  # Legacy raw → v3 aggregates CLI
│   ├── main.py           # FastAPI app entrypoint, secret-redacting logging
│   └── scheduler.py      # APScheduler periodic jobs
├── tests/                # pytest; test_apple_health_db.py / test_food_ledger_db.py need a throwaway PostgreSQL URL
├── spec/
│   └── main.cs.md        # Landing page CodeSpeak specification
├── CLAUDE.md             # This file
├── README.md             # English README
└── README.uk.md          # Ukrainian README
```

---

## 🛠 Tech Stack

- **Bot Platform:** Telegram Bot API (python-telegram-bot v21, long polling)
- **Backend:** FastAPI (Python 3.12+)
- **Database:** PostgreSQL 15+ (asyncpg)
- **APIs:**
  - FatSecret API (food calories, OAuth 1.0)
  - WHOOP API v2 (activity tracking, OAuth 2.0, fetched live)
  - Apple Health (no server API; iOS Shortcut POSTs schema-v3 snapshots)
  - OpenAI GPT + Whisper (AI assistant, speech-to-text)
- **Scheduler:** APScheduler (token refresh, token checks, user-local briefings, journal reminders)
- **Hosting:** Dokploy (Docker-based)

---

## 🔑 Key Commands

```bash
# Database initialization
bash database/init-db.sh

# Database migrations (002 is the production base; 001 is UUID-based and NOT applied)
psql -d healthlog -f database/migrations/002_health_tracker_schema.sql
# Migrations 007 + 009–017 — what the Docker CMD runs:
python -m app.db_preflight --apply-apple-health-migration

# Run the app locally
uvicorn app.main:app --reload

# Run tests
pytest
# Real-PostgreSQL tests (DROP the public schema — throwaway DB only)
APPLE_HEALTH_TEST_DATABASE_URL=postgresql://... pytest tests/test_apple_health_db.py
FOOD_TEST_DATABASE_URL=postgresql://... pytest tests/test_food_ledger_db.py
# After editing the Shortcut plist: re-sign, then
python -m unittest tests.test_apple_health_shortcut_artifact
```

---

## 📋 GitHub Spec Kit

Specifications are stored in `.github/specs/` directory following the GitHub Spec Kit format:

- [`spec-overview.md`](.github/specs/spec-overview.md) - PRD: goals, user stories, tech overview, milestones
- [`spec-data-models.md`](.github/specs/spec-data-models.md) - Database entities, ERD, column definitions, enums
- [`spec-critical-issues.md`](.github/specs/spec-critical-issues.md) - Risk mitigation, action plan, existing solutions

---

## 🎨 Design Specifications

Design specs for the (future) Telegram Web App are in [`docs/design/`](docs/design/README.md):
- [Design System](docs/design/README.md) - Colors, typography, spacing, common components
- [01 - Dashboard](docs/design/pages/01-dashboard.md) - Main overview page
- [02 - Food Log](docs/design/pages/02-food-log.md) - Food logging interface
- [03 - Activity](docs/design/pages/03-activity.md) - WHOOP activity data
- [04 - History](docs/design/pages/04-history.md) - Historical data view
- [05 - Profile](docs/design/pages/05-profile.md) - User settings

Landing page spec (GitHub Pages): [`spec/main.cs.md`](spec/main.cs.md)

---

## ⚡ Quick Reference

| Resource | Location |
|----------|----------|
| PRD & User Stories | [`.github/specs/spec-overview.md`](.github/specs/spec-overview.md) |
| Data Models & ERD | [`.github/specs/spec-data-models.md`](.github/specs/spec-data-models.md) |
| Critical Issues & Risks | [`.github/specs/spec-critical-issues.md`](.github/specs/spec-critical-issues.md) |
| API Integration (EN) | [`docs/en/api-integration.md`](docs/en/api-integration.md) |
| API Integration (UK) | [`docs/uk/api-integration.md`](docs/uk/api-integration.md) |
| Architecture (EN) | [`docs/en/architecture.md`](docs/en/architecture.md) |
| Session Knowledge | [`docs/en/session-knowledge.md`](docs/en/session-knowledge.md) |
| Critical Issues (EN) | [`docs/en/critical-issues.md`](docs/en/critical-issues.md) |
| DB Schema (production) | [`database/migrations/002_health_tracker_schema.sql`](database/migrations/002_health_tracker_schema.sql) + `003`–`017` |
| Food logging (EN) | [`docs/en/food-logging.md`](docs/en/food-logging.md) |
| Food logging (UK) | [`docs/uk/food-logging.md`](docs/uk/food-logging.md) |
| Apple Health Shortcut | [`docs/shortcuts/apple-health-sync.shortcut.plist`](docs/shortcuts/apple-health-sync.shortcut.plist) |
| DB Init Script | [`database/init-db.sh`](database/init-db.sh) |
| Design System | [`docs/design/README.md`](docs/design/README.md) |
| Design Pages | [`docs/design/pages/`](docs/design/pages/) |
| Landing Page Spec | [`spec/main.cs.md`](spec/main.cs.md) |
---

## 🚨 Important Notes

1. **Always maintain bilingual docs** - This is mandatory
2. **Use GitHub Spec Kit format** for specifications
3. **Follow Telegram Web App guidelines** for UI/UX
4. **Keep sensitive data in .env** - Never commit secrets
5. **DB uses INTEGER PKs, not UUID** - Production schema differs from `001_initial_schema.sql`; see [`session-knowledge.md`](docs/en/session-knowledge.md) for details
6. **WHOOP API is v2 only** - All v1 endpoints return 404; confirmed working scopes: `offline read:workout read:recovery read:sleep read:body_measurement` (`read:cycles` → invalid_scope)
7. **Read [`session-knowledge.md`](docs/en/session-knowledge.md) before any dev session** - Contains critical infrastructure facts, API discoveries, and common pitfalls
8. **OAuth `state` must be signed** - use `sign_oauth_state` / `verify_oauth_state` from `app/security.py`; never pass a bare Telegram id
9. **Operator/debug endpoints** must use `Depends(require_admin)` (`ADMIN_API_TOKEN`)
10. **Timezones** - use `resolve_timezone(users.timezone)`; never hard-code `Europe/Kyiv`
11. **Outbound HTTP** - always pass `timeout=settings.http_timeout_seconds`
12. **Never log tokens/secrets** - FatSecret token responses, access tokens, Apple Health tokens
13. **WHOOP live data** - go through `get_whoop_context_for_user()`; don't re-implement the refresh/401 dance
14. **New Apple Health family** = add to `SUPPORTED_METRIC_FAMILIES` + sum/average set + unit aliases + a migration widening the CHECK + preflight constraint + Shortcut query (re-sign)
15. **Apple Health secret is only a hash** - `hash_secret`/`verify_secret` (`app/crypto.py`); OAuth tokens are stored in plain text (encryption was deliberately removed)
16. **User-facing text** - use `t(key, lang)` from `app/i18n.py`; add every key to both `_UK` and `_EN`
17. **New migration the app depends on** - append it to `APPLE_HEALTH_MIGRATIONS` in `app/db_preflight.py` (the Docker CMD is the only prod migration path) and keep it idempotent
18. **Food entries go through `app/services/food_logging.py`** - commit = ledger row + outbox in one transaction; never write `food_entries` or call FatSecret create directly from handlers; never add "just logged" calories to a FatSecret read (use `daily_view`)
19. **Nutrition math is deterministic** - `food_nutrition.py` (`Decimal`, unknown = `None`, ml ≠ g); GPT must not invent grams (`quantity_g: null` → ask)
20. **FatSecret storable data** - IDs are permanent; names/nutrition only in cache columns (≤ 24 h, purged hourly). Never store them durably elsewhere
21. **FatSecret writes** - `create_food_entry()` result `unknown` is never retried blindly; `food_sync.reconcile_unknown` decides
22. **Web App auth** - identity only from validated `initData` sessions (`webapp_auth`); admin role from `user_roles` per request; `ADMIN_API_TOKEN` never reaches the Mini App
23. **Open Food Facts** - pinned to API 3.4 with a recorded fixture; add fixtures before changing the version
