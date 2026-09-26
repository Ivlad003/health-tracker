# 🍽 Food logging: history, barcodes, label photos, Web App API

[🇺🇦 Українська версія](../uk/food-logging.md)

Implementation of [the 2026-09-26 plan](plans/2026-09-26-food-history-photo-barcode.md) (P1 backend: phases 1–4 + Web App/admin API). The React frontend (`web/`) is **not** built yet; `/app/` shows an "Open in Telegram" page until a bundle exists in `web/dist`.

## 1. What the user gets

| Input | Behaviour |
|---|---|
| "гречка варена 180 г" (text/voice) | History-first: pinned default → previously confirmed choice → My Products → FatSecret search. A single unambiguous confirmed match with explicit grams is recorded immediately with **Undo**; otherwise up to 3 candidates are shown as buttons. |
| No grams | The bot asks; reply "135 г" or just "135" **as a reply** to that message (a bare number outside a reply never creates food). Gram preset buttons come from preferences. |
| Barcode photo (+ caption "135 г") | Local decoding (zxing-cpp) → GTIN validation → own/shared product → Open Food Facts → optional FatSecret barcode add-on. Restricted in-store/variable-weight codes are rejected. |
| Unknown barcode | The bot asks for a nutrition-label photo (reply to its message or same album). Extracted values are shown; **Save product and log** creates a reusable personal product linked to the barcode. |
| Packaging front | Name/brand → resolver candidates (never auto-committed). |
| Plate photo | Behind flag `food_plate_photos` (off by default). Several components with one total weight → the bot asks for per-component weights. |
| "видали останнє" | Voids the latest entry (revision), queues a FatSecret delete if it was synced. |

Replies show source (FatSecret / Open Food Facts / label / manual), FatSecret sync state and a daily total that is marked *(partial data)* when anything is unknown or ambiguous.

## 2. Architecture

| Module | Responsibility |
|---|---|
| `app/services/food_nutrition.py` | `Decimal` arithmetic, units (g/kg/mg/oz/lb; ml never becomes g), kJ→kcal (4.184), grams validation, text quantity parsing, FatSecret serving → basis/units |
| `app/services/barcode_reader.py` | EAN-13/EAN-8/UPC-A/UPC-E, check digits, UPC-E expansion, GTIN-13 for FatSecret, OFF normalization, restricted prefixes, threaded image decoding |
| `app/services/food_catalog.py` | Products (shared provider identities + personal), nutrition revisions, My Products membership, exclusions/archive, default rules, learned aliases, listing, provider-cache purge |
| `app/services/food_resolver.py` | Compatibility (barcode/brand/fat %/raw-cooked), ranking tiers, auto-select decision, per-user candidate retrieval, FatSecret search fallback |
| `app/services/food_logging.py` | `UserContext`, drafts (optimistic versions), `prepare_item`, atomic commit (entry + outbox + membership + alias), void/edit revisions, `merge_daily`/`daily_view` |
| `app/services/food_sync.py` | Outbox worker (SKIP LOCKED + lease), typed results, reconciliation of `unknown` creates |
| `app/services/catalog_import.py` | Resumable FatSecret diary → My Products jobs, selective mode, enrichment (favourites/most/recently eaten), daily refresh |
| `app/services/open_food_facts.py` | Pinned API v3.4 adapter, limiter, User-Agent, hit/miss cache |
| `app/services/food_vision.py` | Image bounds/normalization (EXIF stripped), OpenAI vision extraction, strict validation |
| `app/services/food_bot.py` | Telegram-independent orchestration returning `BotReply` (text + inline buttons + Web App link) |
| `app/services/preferences.py` | Typed/versioned preferences, date-effective goals |
| `app/services/feature_flags.py` | Runtime flags with capability checks |
| `app/services/webapp_auth.py` | initData HMAC validation, sessions, CSRF, roles, audit |
| `app/routers/webapp.py`, `app/routers/admin.py` | `/api/v1/webapp/*`, `/api/v1/admin/*` |

## 3. Data model (migrations 016, 017)

`016_food_ledger_catalog.sql`: `food_products`, `food_nutrition_versions`, `user_product_memberships`, `food_default_rules`, `food_log_drafts`, `food_sync_outbox`, `catalog_import_jobs`, `catalog_import_candidates`, `external_lookup_cache`, plus new `food_entries` columns (product/revision, grams, local date, origin, status, idempotency key, remote ids, sync status, revision/version). `calories` became nullable (unknown ≠ 0); legacy rows get `local_date` backfilled.

`017_webapp_admin_preferences.sql`: `user_preferences`, `user_goal_history`, `webapp_sessions`, `user_roles`, `feature_flags`, `admin_audit_log`, `notification_sends`.

Both are idempotent and registered in `APPLE_HEALTH_MIGRATIONS` (Docker CMD preflight) with verified tables, unique indexes and constraints.

### Storage policy

- FatSecret IDs (`food_id`, `serving_id`, `food_entry_id`) are permanent.
- FatSecret names and nutrition are cached ≤ `FATSECRET_CACHE_HOURS` (max 24): `food_products.provider_*`, `food_nutrition_versions` (`provider_cache`), `food_entries.nutrition_expires_at`, draft snapshots. The hourly purge deletes/nulls them; UI labels then fall back to the user's own names (diary label, bot text).
- Open Food Facts, label and manual data are durable revisions with provenance. Changing a product creates a new revision; past meals keep theirs.

## 4. Reliable writes and daily totals

1. Commit = one transaction: `food_entries` row + `food_sync_outbox` create + membership upsert + learned alias.
2. Worker sends `food_entry.create.v2` with the real gram serving; success requires an acknowledged `food_entry_id`.
3. Timeout after dispatch / 5xx / malformed success → `unknown`: never re-sent. `reconcile_unknown` reads the diary: exactly one matching unlinked entry → link; none after 15 min → safe retry; several → stays ambiguous (visible in admin jobs).
4. Non-FatSecret products (OFF/label/manual) stay local with `not_supported`.
5. `merge_daily` counts linked entries once, remote-only and local-only entries once each, suppresses one matching remote entry for an ambiguous local one, excludes remote entries of voided local entries, and reports `partial` + reasons.
6. Dates are the user's local calendar date (`users.timezone`), converted to FatSecret's day integer.

## 5. FatSecret history → My Products

On `/fatsecret/callback` a job for `history_import_days` (default 30) starts in the background. The scheduler processes up to 31 days per run (newest first), checkpointing each day; errors leave the job `partial` with backoff. One card per `food_id` (servings merged), user diary label as display name, no meals created. Removal = durable exclusion (default rules disabled); refreshes and bot auto-add never resurrect excluded/archived cards. Selective mode stores candidates; explicit skips become exclusions, unreviewed stay pending. Daily refresh (04:00 UTC) re-reads the last 3 days.

## 6. Web App API

Authentication: `POST /api/v1/webapp/auth/telegram {init_data}` → `session_token`, `csrf_token`. Use `Authorization: Bearer <session_token>` (recommended in Telegram WebViews) or the Secure/HttpOnly cookie + `X-CSRF-Token` + same-origin `Origin` for mutations. Sessions last `WEBAPP_SESSION_TTL_SECONDS`; initData older than `WEBAPP_AUTH_MAX_AGE_SECONDS` is rejected.

| Group | Endpoints |
|---|---|
| Profile | `GET/PATCH /me` (`profile_version`), `GET/PUT /preferences` (`version`), `GET/PUT /goals` |
| Products | `GET /products`, `POST /products`, `GET /products/search`, `POST /products/import`, `GET/PATCH /products/{id}`, `POST /products/{id}/membership`, `POST /products/bulk-membership`, `POST /products/{id}/refresh` |
| Defaults | `GET/POST /default-rules`, `DELETE /default-rules/{id}?version=`, `POST /default-rules/preview` ("Try phrase", records nothing) |
| History import | `POST/GET /catalog-imports`, `GET /catalog-imports/{id}`, `POST /catalog-imports/{id}/selection`, `POST /catalog-imports/{id}/cancel` |
| Diary | `GET /today` (eaten calories, WHOOP, Apple Health, BMR — the same assembly as the bot), `GET/PATCH /food-drafts[/{id}]`, `POST /food-drafts/{id}/commit`, `POST /food-drafts/{id}/cancel`, `GET /food-entries?date=`, `POST /food-entries` (`idempotency_key`), `PATCH /food-entries/{id}`, `DELETE /food-entries/{id}?version=`, `POST /food-entries/{id}/copy` |
| Uploads | `POST /uploads?idempotency_key=&caption=` raw `image/jpeg|png|webp` body ≤ 10 MB → shared draft |
| Integrations | `GET /integrations`, `POST /integrations/{fatsecret|whoop}/connect-link`, `POST /integrations/fatsecret/disconnect` |
| Admin | `GET/POST /api/v1/admin/catalog`, `PATCH /catalog/{id}`, `GET /features`, `PUT /features/{key}`, `GET /jobs`, `POST /jobs/outbox/{id}/retry`, `POST /jobs/imports/{id}/retry`, `GET /audit`, `POST /roles` |

Errors: `{"detail": {"error": "<code>", ...}}`; stale versions → **409**, validation → 400/422, foreign ids → 404. Admin role is re-read on every request; `ADMIN_API_TOKEN` is never used by the Mini App.

## 7. Settings

| Scope | Where |
|---|---|
| Deployment | `.env`: `FOOD_*`, `FATSECRET_BARCODE_ENABLED`, `FATSECRET_HISTORY_IMPORT_DAYS`, `FATSECRET_CACHE_HOURS`, `OPENAI_VISION_MODEL`, `MEDIA_*`, `OFF_*`, `WEBAPP_*` |
| Runtime flags (admin) | `food_history`, `food_barcode`, `fatsecret_barcode`, `food_vision`, `food_plate_photos` — enabling an unavailable capability returns 409 |
| User preferences | `recording_policy` (`auto_confirmed`/`review_all`), `catalog_auto_add`, `fatsecret_export`, `gram_presets`, `history_import_days`, `history_import_mode`, `history_daily_refresh`, `briefing_morning_enabled/time`, `briefing_evening_enabled/time`, `sync_error_notices` |

Briefings now run every 5 minutes and fire at each user's configured local time once per local date (`notification_sends`).

## 8. Scheduler

| Job | Interval |
|---|---|
| `food_outbox` | 1 min |
| `food_reconcile` | 10 min |
| `catalog_import` | 5 min |
| `catalog_refresh` | 04:00 UTC |
| `food_cache_purge` (provider cache, drafts, lookup cache, sessions) | 1 h |

## 9. Tests

`tests/test_food_*.py`, `test_barcode_reader.py`, `test_fatsecret_writes.py`, `test_webapp_auth.py`, `test_open_food_facts.py` (fixture recorded from OFF v3.4), `test_preferences.py`. Real-PostgreSQL scenarios (drop the public schema!):

```bash
FOOD_TEST_DATABASE_URL=postgresql://... pytest tests/test_food_ledger_db.py
```

## 10. Still requiring live evidence (plan §11)

Not verifiable from the repository: FatSecret account entitlements (barcode, image recognition, `food.create`), actual history quotas, whether `food_entry.create.v2` always returns an id, Ukrainian OFF coverage, vision quality/cost. The code defaults to safe behaviour (barcode add-on off, plate photos off, unknown writes reconciled), and the evaluation dataset from plan §10 still has to be collected before enabling plate recognition.
