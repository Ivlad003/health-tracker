# CLAUDE.md - AI Assistant Instructions

## Project Overview

**Health & Wellness Tracker Bot** - Telegram Web App for tracking calories, physical activity, and mood with FatSecret and WHOOP API integration.

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
├── database/
│   ├── init-db.sh        # DB initialization script (Docker psql fallback)
│   └── migrations/       # SQL migrations
├── app/                  # FastAPI Python application
│   ├── routers/          # API route handlers
│   ├── services/         # Business logic (WHOOP, FatSecret, AI, Telegram)
│   ├── config.py         # Settings & environment variables
│   ├── database.py       # PostgreSQL connection pool
│   ├── main.py           # FastAPI app entrypoint
│   └── scheduler.py      # APScheduler periodic jobs
├── spec/
│   └── main.cs.md        # Landing page CodeSpeak specification
├── CLAUDE.md             # This file
├── README.md             # English README
└── README.uk.md          # Ukrainian README
```

---

## 🛠 Tech Stack

- **Bot Platform:** Telegram Web App
- **Backend:** FastAPI (Python 3.12+)
- **Database:** PostgreSQL 15+ (asyncpg)
- **APIs:**
  - FatSecret API (food calories, OAuth 1.0)
  - WHOOP API v2 (activity tracking, OAuth 2.0)
  - OpenAI GPT + Whisper (AI assistant, speech-to-text)
- **Scheduler:** APScheduler (token refresh, data sync, briefings)
- **Hosting:** Dokploy (Docker-based)

---

## 🔑 Key Commands

```bash
# Database initialization
bash database/init-db.sh

# Database migrations (002 is the production-applied migration)
psql -d healthlog -f database/migrations/001_initial_schema.sql   # UUID-based (NOT applied to prod)
psql -d healthlog -f database/migrations/002_health_tracker_schema.sql  # INTEGER-based (production)

# Run the app locally
uvicorn app.main:app --reload

# Run tests
pytest
```

---

## 📋 GitHub Spec Kit

Specifications are stored in `.github/specs/` directory following the GitHub Spec Kit format:

- [`spec-overview.md`](.github/specs/spec-overview.md) - PRD: goals, user stories, tech overview, milestones
- [`spec-data-models.md`](.github/specs/spec-data-models.md) - Database entities, ERD, column definitions, enums
- [`spec-critical-issues.md`](.github/specs/spec-critical-issues.md) - Risk mitigation, action plan, existing solutions

---

## 🎨 Design Specifications

Design specs for Telegram Web App are in [`docs/design/`](docs/design/README.md):
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
| DB Schema (production) | [`database/migrations/002_health_tracker_schema.sql`](database/migrations/002_health_tracker_schema.sql) |
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
6. **WHOOP API is v2 only** - All v1 endpoints return 404; confirmed working scopes: `read:workout read:recovery read:sleep read:body_measurement`
7. **Read [`session-knowledge.md`](docs/en/session-knowledge.md) before any dev session** - Contains critical infrastructure facts, API discoveries, and common pitfalls
