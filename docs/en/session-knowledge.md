# Session Knowledge Base - 2026-02-24

[Ukrainian version](../uk/session-knowledge.md)

> Practical knowledge gained from building and debugging the n8n WHOOP integration.
> This file serves as a reference for future development sessions.

---

## 1. Infrastructure Facts

| Resource | Value |
|----------|-------|
| n8n instance | See `.env` -> `N8N_HOST` (v2.35.5, Dokploy) |
| PostgreSQL | See `.env` -> `DATABASE_URL` |
| n8n PostgreSQL credential | name: `pet_pg_db` (ID in n8n credentials UI) |
| n8n API key location | `.mcp.json` -> `mcpServers.n8n-mcp.env.N8N_API_KEY` |
| Dokploy panel | See `.mcp.json` -> `mcpServers.dokploy-mcp.env.DOKPLOY_URL` |
| WHOOP user ID | Stored in `users.whoop_user_id` column |
| Telegram user ID | Stored in `users.telegram_user_id` column |

### n8n Workflows Deployed

| Workflow | ID | Status |
|----------|----|--------|
| WHOOP OAuth Callback | `sWOs9ycgABYKCQ8g` | Active |
| WHOOP Data Sync | `nAjGDfKdddSDH2MD` | Active (hourly) |

---

## 2. WHOOP API - Critical Discoveries

### API Version: v2 ONLY

**The WHOOP API is v2, NOT v1.** All v1 endpoints return 404.

| Endpoint | URL |
|----------|-----|
| Workouts | `GET /developer/v2/activity/workout` |
| Recovery | `GET /developer/v2/recovery` |
| Sleep | `GET /developer/v2/activity/sleep` |
| Token exchange | `POST /oauth/oauth2/token` |
| Authorization | `GET /oauth/oauth2/auth` |

> The existing `docs/en/api-integration.md` already has v2 URLs, which is correct.

### Available Scopes (tested and confirmed)

```
read:workout read:recovery read:sleep read:body_measurement
```

**Scopes that DO NOT work:**
- `read:cycles` - returns `invalid_scope` error
- `read:profile` - not available for this app; v1 profile endpoint returns 401

### Token Lifecycle

- Access token expires in **3600 seconds (1 hour)**
- Refresh token is long-lived
- Refresh via `POST /oauth/oauth2/token` with `grant_type=refresh_token`
- Only `client_id` and `client_secret` needed for refresh (no `redirect_uri`)

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

---

## 3. Database Schema - Reality vs Docs

### CRITICAL: Existing DB uses INTEGER, not UUID

The `docs/en/architecture.md` says "UUID for primary keys" but the **actual production database** uses:

```sql
-- Actual schema (pre-existing table):
users.id          -> INTEGER (SERIAL), NOT UUID
users.telegram_user_id -> BIGINT, NOT telegram_id
```

Migration `001_initial_schema.sql` has UUID-based schema but was **never applied** to the existing database. Migration `002_health_tracker_schema.sql` was created to work with the existing INTEGER-based schema using `ALTER TABLE` and `IF NOT EXISTS`.

### Column Name Differences

| Doc/Migration 001 says | Actual DB column |
|------------------------|------------------|
| `telegram_id` | `telegram_user_id` |
| `id UUID` | `id INTEGER` |
| `username` | `telegram_username` (added by migration 002) |

### Tables in Production (9 total)

`users`, `diary_entries` (pre-existing), `food_entries`, `mood_entries`, `whoop_activities`, `whoop_recovery`, `whoop_sleep`, `daily_summaries`, `sync_logs`

### Confirmed User Record

```sql
-- User with WHOOP tokens:
-- id: (integer), telegram_user_id: {TG_USER_ID}, whoop_user_id: '{WHOOP_USER_ID}'
-- Tokens stored and working
```

---

## 4. n8n Node Version Compatibility (v2.35.5)

### CRITICAL: Use these exact typeVersions

The n8n instance (v2.35.5) does **NOT support** newer node versions. Using unsupported versions causes activation to fail silently with:
```
"Cannot read properties of undefined (reading 'execute')"
```

| Node Type | Working Version | Broken Versions |
|-----------|----------------|-----------------|
| `scheduleTrigger` | **1.2** | 1.3 |
| `httpRequest` | **4.2** | 4.4 |
| `postgres` | **2.5** | 2.6 |
| `code` | **1** | 2 |
| `webhook` | **2** | - |
| `if` | **2.3** | - |
| `respondToWebhook` | **1.5** | - |

### Code Node v1 Syntax

In Code node v1:
- "Run Once for All Items": use `items` array (not `$input.all()`)
- "Run Once for Each Item": use `item` (not `$input.item`)
- `$('Node Name').item.json` works in both versions
- `$json` shorthand works in both versions
- `$now`, `$env` work in both versions

### n8n Workflow Activation via API

The MCP tools don't have a direct activate function. Use the REST API:

```bash
curl -X POST "{N8N_HOST}/api/v1/workflows/{WORKFLOW_ID}/activate" \
  -H "X-N8N-API-KEY: {N8N_API_KEY}" \
  -H "Content-Type: application/json"
```

> `N8N_HOST` is in `.env`, `N8N_API_KEY` is in `.mcp.json`.

Deactivate: same URL but `/deactivate`.

---

## 5. Common Pitfalls & Fixes

### .env Parsing in Bash

`source <(grep ...)` and `export $(cat ... | xargs)` **fail** when password contains special characters (like the DB password). Use:

```bash
while IFS= read -r line || [ -n "$line" ]; do
    [[ "$line" =~ ^#.*$ ]] && continue
    [[ -z "$line" ]] && continue
    if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
        key="${BASH_REMATCH[1]}"
        value="${BASH_REMATCH[2]}"
        value="${value%\"}" && value="${value#\"}"
        export "$key=$value"
    fi
done < "$ENV_FILE"
```

### PostgreSQL DATE() on TIMESTAMPTZ is NOT immutable

```sql
-- FAILS: DATE() depends on timezone setting
CREATE INDEX idx ON food_entries(user_id, DATE(logged_at));

-- WORKS: composite index, filter in queries
CREATE INDEX idx ON food_entries(user_id, logged_at);
```

### n8n httpRequest Node - Authentication Conflict

When using manual `Authorization: Bearer ...` header, set `authentication: "none"`. If `genericCredentialType` or `httpHeaderAuth` is set, it can **override** your Bearer token with stale/wrong credentials.

### WHOOP_CLIENT_ID Truncation

The WHOOP client ID in n8n environment variables was truncated (missing last 2 chars). Always verify the full `WHOOP_CLIENT_ID` value from `.env` matches what's configured in Dokploy/n8n environment settings.

### n8n IF Node for DateTime Comparison

- Use `"operation": "before"` (not `"beforeOrEqual"` - it doesn't exist)
- Do NOT use `"singleValue": true` on binary operators like "exists"
- Add `"version": 2` in conditions options for v2.3 IF nodes

---

## 6. Workflow Architecture Notes

### WHOOP OAuth Callback Flow

```
Webhook (GET /whoop/callback)
  -> Validate Params (code exists?)
    -> YES: Exchange Code for Token (POST /oauth/oauth2/token)
      -> Fetch Recovery for User ID (GET /developer/v2/recovery?limit=1)
        -> Store Tokens in DB (UPDATE users SET ... WHERE telegram_user_id = state)
          -> Respond Success (HTML page)
    -> NO: Respond Error (HTML page)
```

Key: The `state` parameter in OAuth URL carries the Telegram user ID, used to match the DB record.

### WHOOP Data Sync Flow

```
Schedule Trigger (every 1 hour)
  -> Get WHOOP Users (SELECT users with tokens)
    -> Token Expired? (whoop_token_expires_at < now)
      -> YES: Refresh Token -> Update Tokens in DB -> Merge Token Data
      -> NO: Merge Token Data
        -> Parallel: Fetch Workouts / Fetch Recovery / Fetch Sleep
          -> Process (Code nodes) -> Store (PostgreSQL UPSERT with ON CONFLICT)
```

---

## 7. Files Reference

| File | Purpose |
|------|---------|
| `database/init-db.sh` | DB initialization script (Docker psql fallback) |
| `database/migrations/001_initial_schema.sql` | UUID-based schema (NOT applied to prod) |
| `database/migrations/002_health_tracker_schema.sql` | ALTER TABLE migration (applied to prod) |
| `n8n/workflows/whoop-oauth-callback.json` | Local copy of OAuth workflow (may be outdated) |
| `.env` | Environment variables (DB, WHOOP, Telegram, OpenAI) |
| `.mcp.json` | MCP server configs (n8n, Dokploy, Chrome DevTools) |

---

## 8. TODO / Known Issues

- [ ] **Update local JSON workflow files** in `n8n/workflows/` to match deployed n8n versions
- [ ] **Fix `docs/en/architecture.md`** - says "UUID for primary keys" but actual DB uses INTEGER
- [ ] **Fix `docs/en/api-integration.md`** - remove `read:cycles` from scopes list (invalid)
- [ ] **WHOOP OAuth uses webhook-test URL** (`/webhook-test/whoop/callback`) - this only works when workflow is active in test mode. For production, change to `/webhook/whoop/callback`
- [ ] **SQL injection risk** in n8n Store Tokens query - uses string interpolation for tokens. Consider parameterized queries
- [ ] **Verify WHOOP_CLIENT_ID** in n8n/Dokploy environment is not truncated
- [ ] **WHOOP Data Sync hourly interval** vs 1-hour token expiry creates a race condition - consider refreshing tokens proactively or using a shorter sync interval
