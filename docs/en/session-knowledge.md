# Session Knowledge Base - 2026-02-25

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
| Telegram Bot | `app/services/telegram_bot.py` | Message handling, commands (/start, /help, /sync, /connect_whoop, /connect_fatsecret) |
| AI Assistant | `app/services/ai_assistant.py` | GPT intent classification + response, calorie stats |
| WHOOP Sync | `app/services/whoop_sync.py` | OAuth 2.0, data sync, token refresh |
| FatSecret API | `app/services/fatsecret_api.py` | OAuth 1.0, food search, diary sync, token check |
| FatSecret Auth | `app/services/fatsecret_auth.py` | OAuth 1.0 HMAC-SHA1 signing |
| Briefings | `app/services/briefings.py` | Morning (08:00) / evening (21:00) scheduled messages |
| Scheduler | `app/scheduler.py` | APScheduler periodic jobs |

### Scheduled Jobs

| Job | Frequency | Purpose |
|-----|-----------|---------|
| WHOOP Data Sync | Every 1h | Sync workouts, sleep, recovery for all users |
| WHOOP Token Refresh | Every 30min | Proactive token refresh (prevents expiry) |
| FatSecret Data Sync | Every 1h | Sync food diary for all connected users |
| FatSecret Token Check | Every 30min | Validate tokens, notify user + clear on expiry |
| Morning Briefing | 08:00 Europe/Kyiv | Daily health summary |
| Evening Summary | 21:00 Europe/Kyiv | End-of-day report |
| Conversation Cleanup | 03:00 UTC | Remove old conversation history |

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
https://api.prod.whoop.com/oauth/oauth2/auth?client_id={WHOOP_CLIENT_ID}&redirect_uri={WHOOP_REDIRECT_URI}&response_type=code&scope=read:workout%20read:recovery%20read:sleep%20read:body_measurement&state={TELEGRAM_USER_ID}
```

> Values for `WHOOP_CLIENT_ID` and `WHOOP_REDIRECT_URI` are in `.env`.

### Getting User ID Without `read:profile`

Since the profile endpoint is unavailable, the user_id is extracted from the recovery endpoint response:
```
GET /developer/v2/recovery?limit=1 -> response.records[0].user_id
```

### Token Refresh Error Handling

`refresh_token_if_needed()` in `whoop_sync.py`:
- Has `force` parameter for proactive refresh
- On 400/401/403 from token endpoint: clears tokens from DB, raises `TokenExpiredError`
- All WHOOP API callers have 401 retry logic (force-refresh + retry once)

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

**Token behavior:** OAuth 1.0 tokens are **permanent** — they don't expire unless revoked. There is no refresh mechanism. The 30-min health check validates tokens by calling the API.

### FatSecret Returns HTTP 200 for Auth Errors

**CRITICAL:** FatSecret returns `HTTP 200 OK` with `{"error": {"code": X, "message": "..."}}` in the response body for auth failures — NOT HTTP 401/403. Standard `httpx.HTTPStatusError` catches won't detect this.

**Solution:** Custom `FatSecretAuthError` exception + `_FS_AUTH_ERROR_CODES = {2, 4, 8, 13, 14}` in `fatsecret_api.py`. All API responses are checked for error body.

### Calorie Source Priority

When FatSecret is connected and working, it's the **source of truth** for calories eaten (bot entries are synced there). Only use bot-logged calories as fallback when FatSecret is unavailable. See `get_today_stats()` in `ai_assistant.py`.

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

`users`, `diary_entries`, `food_entries`, `mood_entries`, `whoop_activities`, `whoop_recovery`, `whoop_sleep`, `daily_summaries`, `sync_logs`, `conversation_messages`

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

**WHOOP (OAuth 2.0):** Token has known expiry time. `refresh_token_if_needed()` checks `whoop_token_expires_at`. On any 401 from API: force-refresh + retry once. On refresh failure (400/401/403): clear tokens, raise `TokenExpiredError`.

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
| `app/routers/whoop.py` | `/whoop/callback` OAuth flow |
| `app/routers/fatsecret.py` | `/fatsecret/connect`, `/fatsecret/callback` |
| `app/routers/utils.py` | `/ip` health check |
| `database/init-db.sh` | DB initialization script |
| `database/migrations/002_health_tracker_schema.sql` | Production schema migration |
| `.env` | Environment variables (DB, WHOOP, FatSecret, Telegram, OpenAI) |

---

## 8. TODO / Known Issues

### BACKLOG

- [ ] **Token encryption** — WHOOP/FatSecret tokens stored as plain text in DB
- [ ] **BMR in calorie balance** — Add Mifflin-St Jeor formula for basal metabolic rate
- [ ] **Fix `docs/en/api-integration.md`** — remove `read:cycles` from scopes, add FatSecret OAuth 1.0 vs 2.0 distinction
- [ ] **WHOOP steps via API** — Monitor WHOOP Developer API for steps endpoint (not available as of 2026-02-25)
- [ ] **Local Ukrainian food database** — Fallback for when FatSecret doesn't have Ukrainian foods

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
