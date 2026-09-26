# Session Knowledge Base - updated 2026-09-26

[Ukrainian version](../uk/session-knowledge.md)

> Practical knowledge gained from building the Health Tracker bot.
> This file serves as a reference for future development sessions.

---

## 1. Infrastructure Facts

| Resource | Value |
|----------|-------|
| App | FastAPI Python 3.12+ (Docker on Dokploy) |
| PostgreSQL | See `.env` -> `DATABASE_URL` |
| Dokploy panel | See `.mcp.json` -> `mcpServers.dokploy-mcp.env.DOKPLOY_URL` |
| WHOOP user ID | Stored in `users.whoop_user_id` column |
| Telegram user ID | Stored in `users.telegram_user_id` column |

### Application Services

| Service | File | Purpose |
|---------|------|---------|
| Telegram Bot | `app/services/telegram_bot.py` | Long-polling bot; photo/document/callback handlers for food. Commands: /start, /help, /sync, /timezone, /profile, /language, /connect_whoop, /connect_fatsecret, /connect_apple_health, /apple_health_help, /gym_prompt, /journal*, /app |
| AI Assistant | `app/services/ai_assistant.py` | GPT intent classification + response, calorie stats |
| WHOOP Sync | `app/services/whoop_sync.py` | OAuth 2.0, data sync, token refresh |
| FatSecret API | `app/services/fatsecret_api.py` | OAuth 1.0, food search, diary sync, token check |
| FatSecret Auth | `app/services/fatsecret_auth.py` | OAuth 1.0 HMAC-SHA1 signing |
| Apple Health | `app/services/apple_health.py`, `app/routers/apple_health.py` | Shortcut/HAE webhook, schema-v3 per-family daily aggregates |
| Briefings | `app/services/briefings.py` | Morning (08:00) / evening (21:00) **user-local** messages, journal reminders |
| Scheduler | `app/scheduler.py` | APScheduler periodic jobs |
| Security | `app/security.py` | Signed OAuth `state`, `require_admin` dependency |
| Time | `app/timeutils.py` | `resolve_timezone()` (users.timezone → DEFAULT_TIMEZONE), local-day bounds |
| Crypto | `app/crypto.py` | `hash_secret`/`verify_secret` for the Apple Health secret |
| i18n | `app/i18n.py` | uk/en message catalog `t(key, lang)`, `normalize_language()` |
| Workouts | `app/services/health_workouts.py` | Apple Health workout validation, upsert, daily summary |
| BMR | `app/services/bmr.py` | Mifflin-St Jeor, `/profile` parsing, prorated basal burn |
| Food logging | `app/services/food_*.py`, `catalog_import.py`, `barcode_reader.py`, `open_food_facts.py` | Ledger, catalog, resolver, outbox, history import, barcode/label photos — see [food-logging.md](food-logging.md) |
| Web App | `app/routers/webapp.py`, `app/routers/admin.py`, `app/services/webapp_auth.py` | initData sessions, `/api/v1/webapp/*`, `/api/v1/admin/*` |

### Scheduled Jobs

| Job | Frequency | Purpose |
|-----|-----------|---------|
| WHOOP Token Refresh | Every 30min | Refresh tokens expiring within 10 min |
| FatSecret Token Check | Every 3h | Validate tokens, notify user + clear on revocation |
| Morning Briefing | Every 5 min, sends at the user's configured local time (default 08:00), once per local date (`notification_sends`) | Daily health summary |
| Evening Summary | Every 5 min, same rule (default 21:00) | End-of-day report |
| Food outbox | Every 1min | FatSecret create/edit/delete with leases |
| Food reconcile | Every 10min | Resolve `unknown` creates from the remote diary |
| FatSecret history import | Every 5min (+ daily refresh 04:00 UTC) | My Products population, resumable |
| Food cache purge | Hourly | FatSecret data ≤24 h, expired drafts, lookup cache, sessions |
| Journal Reminders | Every 10min | User-configured times (±5 min, wraps midnight), user-local |
| Conversation Cleanup | 03:00 UTC | Remove conversation history older than 7 days |

There is **no** periodic WHOOP/FatSecret *read* sync (only the food outbox
writes and the history import): WHOOP and FatSecret are
fetched live (WHOOP with a 120 s cache). Apple Health is push-only. Jobs use
`coalesce=True, max_instances=1`.

---

## 2. WHOOP API - Critical Discoveries

### API Version: v2 ONLY

**The WHOOP API is v2, NOT v1.** All v1 endpoints return 404.

| Endpoint | URL |
|----------|-----|
| Workouts | `GET /developer/v2/activity/workout` |
| Recovery | `GET /developer/v2/recovery` |
| Sleep | `GET /developer/v2/activity/sleep` |
| Daily Cycle | `GET /developer/v2/cycle` |
| Token exchange | `POST /oauth/oauth2/token` |
| Authorization | `GET /oauth/oauth2/auth` |

### Available Scopes (tested and confirmed)

```
read:workout read:recovery read:sleep read:body_measurement
```

**Scopes that DO NOT work:**
- `read:cycles` - returns `invalid_scope` error
- `read:profile` - not available for this app; v1 profile endpoint returns 401

### Steps Data NOT Available via API

WHOOP tracks steps in the app (added 2025), but the Developer API v2 does **not** expose step count data. There is no steps endpoint or field in any API response. The bot's system prompt guides users to check the WHOOP app directly.

### Token Lifecycle

- Access token expires in **3600 seconds (1 hour)**
- Refresh token is long-lived
- Refresh via `POST /oauth/oauth2/token` with `grant_type=refresh_token`
- Only `client_id` and `client_secret` needed for refresh (no `redirect_uri`)
- **Token refresh can return 400 Bad Request** if token was revoked (e.g., user re-authorized). Handle by clearing tokens and raising `TokenExpiredError`.

### OAuth Flow - Working Authorization URL

```
https://api.prod.whoop.com/oauth/oauth2/auth?client_id={WHOOP_CLIENT_ID}&redirect_uri={WHOOP_REDIRECT_URI}&response_type=code&scope=offline%20read:workout%20read:recovery%20read:sleep%20read:body_measurement&state={SIGNED_STATE}
```

`state` is **signed** (`app/security.py`, HMAC-SHA256, purpose `whoop`, 1 h
TTL). A bare Telegram id used to allow account-linking CSRF. `offline` is
needed for a refresh token.

> Values for `WHOOP_CLIENT_ID` and `WHOOP_REDIRECT_URI` are in `.env`.

### Getting User ID Without `read:profile`

Since the profile endpoint is unavailable, the user_id is taken from the first
record of recovery → sleep → workouts (`limit=1`). A new member with no records
is still connected with `whoop_user_id = NULL` (this used to crash with
`IndexError`).

### Token Refresh Error Handling

`refresh_token_if_needed()` in `whoop_sync.py`:
- Has `force` parameter for proactive refresh
- Runs under a per-user `asyncio.Lock`; a waiting caller reuses the token that
  was just issued instead of spending the refresh token twice
- On 400/401/403 from token endpoint: `clear_whoop_tokens()` + `TokenExpiredError`
- `get_whoop_context_for_user()` is the only live-data entry point (401 →
  force-refresh → retry once → clear). Do not re-implement this dance.

---

## 3. FatSecret API - Critical Discoveries

### Two Separate Credential Sets

FatSecret uses **different credentials** for OAuth 1.0 vs OAuth 2.0:

| | OAuth 2.0 | OAuth 1.0 |
|---|---|---|
| Key name | Client ID | Consumer Key |
| Secret name | Client Secret | Shared Secret |
| Values | Same key, **different secrets** | Same key, **different secrets** |
| Use case | Public food database (search) | User's personal food diary |

### OAuth 2.0 (Server-to-Server) - WORKING

- Token endpoint: `POST https://oauth.fatsecret.com/connect/token`
- API endpoint: `POST https://platform.fatsecret.com/rest/server.api`
- Used for: food search, food details (public database)
- **Requires IP whitelisting** on `platform.fatsecret.com`

### OAuth 1.0 Three-Legged (User Data) - WORKING

Used for accessing user's personal food diary.

**Endpoints:**
- Request Token: `POST https://authentication.fatsecret.com/oauth/request_token`
- User Authorization: `GET https://authentication.fatsecret.com/oauth/authorize?oauth_token={token}`
- Access Token: `POST https://authentication.fatsecret.com/oauth/access_token`

**Signing:** HMAC-SHA1 via `app/services/fatsecret_auth.py`

**Token behavior:** OAuth 1.0 tokens are **permanent** — they don't expire unless revoked. There is no refresh mechanism. A 3-hour health check validates tokens; revocation is also detected on every chat message.

**Never log token responses** — `request_token`/`access_token` bodies contain secrets. The OAuth 2.0 client-credentials token is cached in memory.

**Diary date is user-local** (`fatsecret_today(tz)`), not UTC.

### FatSecret Returns HTTP 200 for Auth Errors

**CRITICAL:** FatSecret returns `HTTP 200 OK` with `{"error": {"code": X, "message": "..."}}` in the response body for auth failures — NOT HTTP 401/403. Standard `httpx.HTTPStatusError` catches won't detect this.

**Solution:** `_raise_on_error_body()` in `fatsecret_api.py`: codes `{2, 4, 8, 13, 14}` → `FatSecretAuthError`, others → `FatSecretAPIError`. Writes use `create_food_entry()` → `FoodEntryWriteResult` (`succeeded` needs an acknowledged `food_entry_id`; timeouts after dispatch / 5xx / malformed success → `unknown`, reconciled, never re-sent). Every confirmed entry is stored locally first (ledger + outbox); `create_food_diary_entry()` is only a boolean compatibility wrapper.

### Calorie Source Priority

Eaten calories = **local ledger ∪ live FatSecret diary**, linked by remote entry id so a synced entry counts once; remote-only and local-only entries are added; ambiguous/unknown parts make the total `partial` (`food_logging.merge_daily`, used by `get_today_stats()`). Never add "just logged" calories on top of a fresh FatSecret read (that double counted).

---

## 4. Database Schema - Reality vs Docs

### CRITICAL: Existing DB uses INTEGER, not UUID

```sql
-- Actual schema:
users.id          -> INTEGER (SERIAL), NOT UUID
users.telegram_user_id -> BIGINT
```

Migration `001_initial_schema.sql` has UUID-based schema but was **never applied**. Migration `002_health_tracker_schema.sql` works with the existing INTEGER-based schema.

### Tables in Production

Written by the app: `users`, `food_entries` (ledger), the food/catalog tables
from 016, the Web App/preferences tables from 017, `conversation_messages`,
`gym_exercises`, `journal_entries`, `apple_health_sync`,
`apple_health_import_logs`, `health_daily_metric_aggregates`,
`health_workouts`.

Read-only legacy (Apple Health rollout): `health_daily_aggregates` (v2),
`health_data` (raw).

Dropped by `015_drop_unused_tables.sql` (only if empty — a table with rows is
kept and reported with a NOTICE): `mood_entries`, `whoop_activities`,
`whoop_recovery`, `whoop_sleep`, `daily_summaries`, `sync_logs`,
`health_conflicts`, view `v_daily_calorie_balance`. `007` no longer creates
`health_conflicts`.

Migrations: `002` base → `003` FatSecret columns → `004` conversations → `005`
gym → `006` journal → `007` Apple Health connector → `009` v2 aggregates →
`010` v3 per-family aggregates → `011` extended families → `012` user profile
(birth_year, sex, height_cm) → `013` `health_workouts` → `014` hash Apple
Health secrets (irreversible) → `015` drop unused tables. `008` is intentionally
absent. `016` food ledger/catalog/outbox/imports, `017` Web App sessions, roles,
preferences, goals, flags, audit. The Docker preflight applies 007 and 009–017.

---

## 5. GPT Context Engineering

### Avoid Giving GPT Confusing Breakdowns

**Bug found 2026-02-25:** When GPT context showed "total: 216 kcal (FatSecret: 216, bot: 40)", GPT added them to get 256 instead of using 216.

**Fix:** Show only ONE total number with explicit instruction:
```
Today's calories eaten: {total} kcal.
IMPORTANT: Use ONLY these exact numbers when answering about calories.
Do NOT add or recalculate — these are already the correct totals.
```

### System Prompt Structure

The `SYSTEM_PROMPT` in `ai_assistant.py` classifies every message into one intent:
- `log_food` — extracts food items with name, weight, meal_type
- `query_data` — answers about health data using provided context
- `delete_entry` — removes last/specific food entry
- `general` — greetings, calorie goal setting, help

Response is always JSON with `intent`, `food_items`, `calorie_goal`, `response` fields.

---

## 6. Common Pitfalls & Fixes

### .env Parsing in Bash

`source <(grep ...)` and `export $(cat ... | xargs)` **fail** when password contains special characters. Use `while IFS= read -r line` loop (see `database/init-db.sh`).

### PostgreSQL DATE() on TIMESTAMPTZ is NOT immutable

```sql
-- FAILS: DATE() depends on timezone setting
CREATE INDEX idx ON food_entries(user_id, DATE(logged_at));

-- WORKS: composite index, filter in queries
CREATE INDEX idx ON food_entries(user_id, logged_at);
```

### Token Expiry Detection Patterns

**WHOOP (OAuth 2.0):** Token has known expiry time. `refresh_token_if_needed()` checks `whoop_token_expires_at`. On any 401 from API: force-refresh + retry once (`get_whoop_context_for_user`). On refresh failure (400/401/403): clear tokens, raise `TokenExpiredError`.

### Timezones

Never hard-code `Europe/Kyiv`. Use `resolve_timezone(users.timezone)` from
`app/timeutils.py`; it falls back to `DEFAULT_TIMEZONE` for empty/invalid values.
Users can change it with `/timezone Europe/Warsaw`.

### Food logging pitfalls

- **Grams come from the user.** The GPT prompt returns `quantity_g: null` unless a
  weight was stated; the bot asks. A bare number is accepted only as a reply to
  the draft message.
- **Never trust a FatSecret "success" without `food_entry_id`** and never retry an
  `unknown` create blindly — `food_sync.reconcile_unknown` decides.
- **`updated_at` triggers overwrite manual values** — do not backdate
  `updated_at` for scheduling; the outbox uses `dispatched_at`/`next_attempt_at`.
- **asyncpg `AmbiguousParameterError`** ("text versus character varying") when the
  same `$n` is compared as text and assigned to a VARCHAR column: cast (`$n::varchar`)
  or pass a precomputed value.
- **FatSecret data ≤ 24 h**: store IDs + the user's own labels durably, everything
  else via the cache columns that `purge_expired_provider_data` clears.
- **Open Food Facts API is pinned to 3.4**; do not switch to 3.5+ without new
  fixtures (the nutrition structure changed).

### Operator endpoints

`/debug/*`, `/ip-check`, `/fatsecret/diary`, `/food/search` require
`ADMIN_API_TOKEN` (`Authorization: Bearer …`); they return 404 when the token is
not configured. Every outbound HTTP call uses `HTTP_TIMEOUT_SECONDS`.

**FatSecret (OAuth 1.0):** Tokens are permanent but can be revoked. API returns HTTP 200 with error body. Check `_FS_AUTH_ERROR_CODES` in response. On auth error: clear tokens, raise `FatSecretAuthError`, notify user via Telegram.

### Expired Token User Notification

Both `handle_message` and `handle_sync` in `telegram_bot.py` check `expired_services` list from `get_today_stats()` and append reconnect hints:
```
🔑 Сесія закінчилась, потрібно перепідключити:
  ⌚ WHOOP → /connect_whoop
  🥗 FatSecret → /connect_fatsecret
```

---

## 7. Files Reference

| File | Purpose |
|------|---------|
| `app/main.py` | FastAPI app entrypoint, lifespan management |
| `app/config.py` | Settings from environment variables |
| `app/database.py` | asyncpg PostgreSQL connection pool |
| `app/scheduler.py` | APScheduler periodic job configuration |
| `app/services/telegram_bot.py` | All bot handlers and user-facing messages |
| `app/services/ai_assistant.py` | GPT integration, calorie stats, conversation context |
| `app/services/whoop_sync.py` | WHOOP OAuth 2.0, data sync, token management |
| `app/services/fatsecret_api.py` | FatSecret API, diary sync, token health check |
| `app/services/fatsecret_auth.py` | OAuth 1.0 HMAC-SHA1 request signing |
| `app/services/briefings.py` | Morning/evening scheduled messages |
| `app/services/apple_health.py` | Apple Health validation, aggregation, persistence, read overlay |
| `app/security.py` | Signed OAuth state, admin guard |
| `app/timeutils.py` | Per-user timezone helpers |
| `app/db_preflight.py` | Applies/verifies migrations 007, 009–017 under an advisory lock |
| `app/services/food_bot.py` | Telegram food flows (text/voice/photo/callbacks) without PTB types |
| `app/routers/webapp.py`, `app/routers/admin.py` | Web App JSON API, owner/admin API |
| `app/routers/whoop.py` | `/whoop/callback` OAuth flow |
| `app/routers/fatsecret.py` | `/fatsecret/connect`, `/fatsecret/callback`, admin `/fatsecret/diary`, `/food/search` |
| `app/routers/apple_health.py` | `/api/v1/health/apple-health/shortcut`, `/sync` |
| `app/routers/utils.py` | Admin `/ip-check`, `/debug/*` |
| `database/init-db.sh` | DB initialization script |
| `database/migrations/002_health_tracker_schema.sql` | Production schema migration |
| `.env` | Environment variables (DB, WHOOP, FatSecret, Telegram, OpenAI) |

---

## 8. TODO / Known Issues

### BACKLOG

- [ ] **Verify extended Shortcut on a device** — picker labels Resting Heart Rate / Weight / Walking + Running Distance / Exercise Minutes and the sample `Unit` property (needs an iPhone)
- [ ] **Workouts in the signed Shortcut** — server + HAE path done; the Shortcuts "Find Workouts" action must be added and verified on a device
- [ ] **Multi-replica WHOOP refresh lock** — the per-user lock is in-process; move to a PostgreSQL advisory lock before running >1 replica
- [x] ~~Apple Health secret hashing~~ — SHA-256 (2026-09-26). OAuth token encryption was implemented and then deliberately removed; tokens stay plain text
- [x] ~~BMR in calorie balance~~ — `/profile` + Mifflin-St Jeor, prorated basal burn added to Apple Health active energy (2026-09-26)
- [x] ~~Apple Health workouts~~ — `health_workouts`, native `workouts` array + HAE `data.workouts` (2026-09-26)
- [x] ~~Drop never-written tables~~ — guarded migration 015 (2026-09-26)
- [x] ~~i18n~~ — uk/en catalog, `/language`, language from Telegram `language_code` (2026-09-26)
- [ ] **WHOOP steps via API** — Monitor WHOOP Developer API for steps endpoint (not available as of 2026-02-25)
- [ ] **Local Ukrainian food database** — partly covered by Open Food Facts barcodes + label photos → personal products; coverage not measured
- [ ] **Web App frontend** (`web/`, React + Vite) — backend API is ready (docs/en/food-logging.md §6)
- [ ] **Food evaluation dataset** (plan §10) and live FatSecret checks (plan §11) before enabling plate photos / the FatSecret barcode add-on
- [x] ~~History-first food logging, barcode + label photos, FatSecret outbox, Web App/admin API~~ (2026-09-26)
- [x] ~~Fix `docs/en/api-integration.md` scopes / FatSecret OAuth 1.0 vs 2.0~~ (2026-09-26)
- [x] ~~Unauthenticated debug endpoints, unsigned OAuth state, missing HTTP timeouts, UTC diary date, lost food entries~~ (2026-09-26)

---

## 9. Migration History (2026-02-24)

All 7 n8n workflows were migrated to a single FastAPI Python application and the n8n workflows were deleted from the server.

### Workflow to Python Mapping

| Former n8n Workflow | Python Equivalent |
|---|---|
| WHOOP Data Sync | `app/services/whoop_sync.py` (APScheduler hourly) |
| WHOOP OAuth Callback | `app/routers/whoop.py` → `GET /whoop/callback` |
| FatSecret Food Search | `app/services/fatsecret_api.py` → `search_food()` |
| FatSecret OAuth Connect | `app/routers/fatsecret.py` → `GET /fatsecret/connect` |
| FatSecret OAuth Callback | `app/routers/fatsecret.py` → `GET /fatsecret/callback` |
| FatSecret Food Diary | `app/services/fatsecret_api.py` → `fetch_food_diary()` |
| IP Check | `app/routers/utils.py` → `GET /ip` |

### Deployment

Docker image: `health-tracker`, deployed on Dokploy.

```bash
docker build -t health-tracker .
docker run --env-file .env -p 8000:8000 health-tracker
```

Production startup uses the Dockerfile command as the single authoritative
migration path for Apple Health. The container runs
`python -m app.db_preflight --apply-apple-health-migration` before Uvicorn,
applies migrations `007`, `009`, `010`, and `011`, then the FastAPI lifespan verifies
the required Apple Health tables and indexes again before serving traffic. The
preflight holds one PostgreSQL advisory lock across migration and verification,
so parallel replica starts cannot race DDL. `database/init-db.sh` skips every
`*_rollback.sql` file and enables `ON_ERROR_STOP` for migration files.

### Apple Health schema-v3 operational rules

- The native Shortcut must send collector `shortcut`, an offset-aware
  `generatedAt`, timezone, dates, and covered metric families. Re-import old
  schema-v2 Shortcuts.
- Native payloads cannot claim the `health_auto_export` collector. Coverage uses
  exactly one encoding, at most 31 dates per family, and a 30-day/+1-day window.
- Metric values and durations are finite, non-negative where applicable, and
  magnitude-bounded before aggregation; database constraints also reject NaN.
- Sleep counts unioned asleep stages. It excludes Awake and In Bed unless the
  only usable fallback is In Bed minus Awake; Awake-only coverage is zero.
- New imports persist only processed per-family rows. Raw request bodies are not
  stored in `health_data` or forwarded to Telegram.
- Run `python -m app.backfill_apple_health` first without deletion. Use
  `--delete-raw` only after read-back and row-count verification; any residual
  purge failure exits non-zero. Destructive mode refuses unsupported metrics
  and blocks concurrent writers through the final residual check.
- During rollout, readers timezone-filter candidates first, prefer the newest
  in-window live collector per date/family, and then fill gaps from schema-v2,
  backfill, and legacy raw sources.
- HAE accepts one supported metric per automation, only for cumulative
  Default/Today/Yesterday/Previous 7 Days periods, with Summarize Data and Batch
  Requests disabled. Required custom headers are
  `X-Health-Tracker-HAE-Mode: complete-unbatched-unaggregated-single-metric-v1`
  and `X-Health-Tracker-Timezone`, plus a client-minted
  `X-Health-Tracker-Generated-At` created before dispatch. Stock direct HAE REST
  automations cannot supply this causal marker and fail closed; ingress-proxy
  receipt timestamps are forbidden. Incremental, grouped, multi-metric, and
  malformed snapshots also fail closed.
- Schema-v3 families: `steps`, `active_energy`, `heart_rate`, `hrv`, `sleep`,
  and (migration 011) `resting_heart_rate`, `body_mass` (kg, average),
  `distance` (m, sum), `exercise_time` (min, sum). Sum vs average is decided by
  `SUM_METRIC_FAMILIES` / `AVERAGE_METRIC_FAMILIES` in `apple_health.py`.
- The ready Shortcut runs 8 queries. Weight and distance send the sample
  `Unit` property (locale-dependent); the server also parses a unit suffix from
  the Value text when Unit is empty.
- The Apple Health `token` is in the URL: `SecretRedactingFilter`
  (`app/main.py`) masks it in all logs, including uvicorn access logs.
- After editing `docs/shortcuts/apple-health-sync.shortcut.plist`, re-sign with
  `shortcuts sign --mode anyone` (input must end in `.shortcut`) and run
  `python -m unittest tests.test_apple_health_shortcut_artifact`.
- Workouts go to `health_workouts` (events, upsert by external id); the
  native payload's optional `workouts` array and HAE `data.workouts` are both
  accepted, workouts-only requests too. The signed Shortcut does not send them yet.
- The Apple Health secret is stored hashed (`sha256:`); the bot shows the URL
  only once.

## 10. Credentials, language, and BMR (2026-09-26)

- **Tokens at rest:** WHOOP/FatSecret OAuth tokens are plain text by decision
  (no `TOKEN_ENCRYPTION_KEY`). Only the Apple Health secret is hashed.
- **Language:** every fixed user-facing text goes through `t(key, lang)`
  (`app/i18n.py`); add keys to **both** `_UK` and `_EN` (a test checks key and
  placeholder parity). New users get `users.language` from Telegram
  `language_code`; `/language uk|en` changes it. GPT answers follow the
  language the user writes in.
- **BMR:** `/profile 1990 m 180` stores birth year, sex, height. Weight comes
  from Apple Health (latest weigh-in ≤30 days) or WHOOP body measurement.
  WHOOP cycle calories already include basal burn and are used as-is; Apple
  Health active energy does not, so `calories_out = active + BMR × elapsed
  share of the local day` (`calories_burned_source = "apple_health_bmr"`).

