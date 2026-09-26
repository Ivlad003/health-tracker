# Spec: Data Models
# Health & Wellness Tracker Bot

## Status: Draft
## Version: 0.3.0

> **Production reality (2026-09-26):** production uses **INTEGER** primary keys
> (`users.id SERIAL`, `users.telegram_user_id BIGINT`) from migration `002`,
> not the UUID layout described in sections 1–7 (migration `001`, never
> applied). WHOOP data is fetched live; `whoop_*`, `mood_entries`, and
> `daily_summaries` exist but are not written. The tables the app actually
> writes for Apple Health are documented in section 8.

---

## Entity Relationship Diagram

```
┌──────────────┐       ┌──────────────────┐       ┌──────────────────┐
│    users     │───┬──▶│   food_entries   │       │ whoop_activities │
└──────────────┘   │   └──────────────────┘       └──────────────────┘
                   │                                       ▲
                   │   ┌──────────────────┐               │
                   ├──▶│   mood_entries   │               │
                   │   └──────────────────┘               │
                   │                                       │
                   │   ┌──────────────────┐               │
                   ├──▶│  whoop_recovery  │───────────────┤
                   │   └──────────────────┘               │
                   │                                       │
                   │   ┌──────────────────┐               │
                   ├──▶│   whoop_sleep    │───────────────┘
                   │   └──────────────────┘
                   │
                   │   ┌──────────────────┐
                   └──▶│ daily_summaries  │
                       └──────────────────┘
```

---

## 1. Users

Stores user profiles and authentication data.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | UUID | PK, DEFAULT uuid_generate_v4() | Primary key |
| telegram_id | VARCHAR(50) | UNIQUE, NOT NULL | Telegram user ID |
| telegram_username | VARCHAR(100) | | Telegram username |
| whoop_user_id | VARCHAR(100) | | WHOOP user ID |
| whoop_access_token | TEXT | | OAuth access token |
| whoop_refresh_token | TEXT | | OAuth refresh token |
| whoop_token_expires_at | TIMESTAMP WITH TIME ZONE | | Token expiration |
| daily_calorie_goal | INTEGER | DEFAULT 2000 | Target calories/day |
| timezone | VARCHAR(50) | DEFAULT 'Europe/Kyiv' | User timezone |
| language | VARCHAR(10) | DEFAULT 'uk' | Preferred language |
| settings | JSONB | DEFAULT '{}' | User preferences |
| created_at | TIMESTAMP WITH TIME ZONE | DEFAULT NOW() | Registration date |
| updated_at | TIMESTAMP WITH TIME ZONE | DEFAULT NOW() | Last update |

---

## 2. Food Entries

Stores logged food items with nutritional data.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | UUID | PK | Primary key |
| user_id | UUID | FK → users.id, NOT NULL | Owner |
| food_name | VARCHAR(255) | NOT NULL | Food item name |
| fatsecret_food_id | VARCHAR(50) | | FatSecret ID |
| calories | DECIMAL(10,2) | DEFAULT 0 | Calories (kcal) |
| protein | DECIMAL(10,2) | DEFAULT 0 | Protein (g) |
| fat | DECIMAL(10,2) | DEFAULT 0 | Fat (g) |
| carbs | DECIMAL(10,2) | DEFAULT 0 | Carbohydrates (g) |
| fiber | DECIMAL(10,2) | DEFAULT 0 | Fiber (g) |
| serving_size | DECIMAL(10,2) | | Serving amount |
| serving_unit | VARCHAR(50) | | Unit (g, ml, pcs) |
| meal_type | ENUM | NOT NULL | breakfast/lunch/dinner/snack |
| logged_at | TIMESTAMP WITH TIME ZONE | DEFAULT NOW() | When eaten |
| source_text | TEXT | | Original voice/text input |
| source_audio_file_id | VARCHAR(255) | | Telegram audio file ID |
| created_at | TIMESTAMP WITH TIME ZONE | DEFAULT NOW() | Record creation |

### Indexes
- `idx_food_entries_user_id` ON (user_id)
- `idx_food_entries_logged_at` ON (logged_at)
- `idx_food_entries_user_date` ON (user_id, DATE(logged_at))

---

## 3. Mood Entries

Stores mood and wellness logs.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | UUID | PK | Primary key |
| user_id | UUID | FK → users.id, NOT NULL | Owner |
| mood_score | INTEGER | CHECK (1-10) | Mood rating 1-10 |
| mood_description | VARCHAR(255) | | Mood keywords |
| energy_level | INTEGER | CHECK (1-10) | Energy rating 1-10 |
| sleep_quality | VARCHAR(50) | | Sleep description |
| sleep_hours | DECIMAL(4,2) | | Hours slept |
| stress_level | INTEGER | CHECK (1-10) | Stress rating 1-10 |
| notes | TEXT | | Additional notes |
| logged_at | TIMESTAMP WITH TIME ZONE | DEFAULT NOW() | Log timestamp |
| source_text | TEXT | | Original input |
| created_at | TIMESTAMP WITH TIME ZONE | DEFAULT NOW() | Record creation |

---

## 4. WHOOP Activities

Stores synced workout data from WHOOP.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | UUID | PK | Primary key |
| user_id | UUID | FK → users.id, NOT NULL | Owner |
| whoop_workout_id | VARCHAR(100) | UNIQUE, NOT NULL | WHOOP workout UUID |
| sport_id | INTEGER | | WHOOP sport ID |
| sport_name | VARCHAR(100) | NOT NULL | Activity name |
| score_state | ENUM | DEFAULT 'PENDING_SCORE' | SCORED/PENDING/UNSCORABLE |
| kilojoules | DECIMAL(10,2) | | Energy in kJ |
| calories | DECIMAL(10,2) | | Calculated kcal |
| strain | DECIMAL(5,2) | | Strain score |
| avg_heart_rate | INTEGER | | Average HR (bpm) |
| max_heart_rate | INTEGER | | Max HR (bpm) |
| percent_recorded | DECIMAL(5,2) | | HR data coverage % |
| distance_meter | DECIMAL(10,2) | | Distance (m) |
| altitude_gain_meter | DECIMAL(10,2) | | Elevation gain (m) |
| zone_zero_seconds | INTEGER | DEFAULT 0 | Time in zone 0 |
| zone_one_seconds | INTEGER | DEFAULT 0 | Time in zone 1 |
| zone_two_seconds | INTEGER | DEFAULT 0 | Time in zone 2 |
| zone_three_seconds | INTEGER | DEFAULT 0 | Time in zone 3 |
| zone_four_seconds | INTEGER | DEFAULT 0 | Time in zone 4 |
| zone_five_seconds | INTEGER | DEFAULT 0 | Time in zone 5 |
| started_at | TIMESTAMP WITH TIME ZONE | NOT NULL | Workout start |
| ended_at | TIMESTAMP WITH TIME ZONE | NOT NULL | Workout end |
| timezone_offset | VARCHAR(10) | | User's TZ offset |
| whoop_created_at | TIMESTAMP WITH TIME ZONE | | WHOOP record creation |
| whoop_updated_at | TIMESTAMP WITH TIME ZONE | | WHOOP record update |
| created_at | TIMESTAMP WITH TIME ZONE | DEFAULT NOW() | Local record creation |
| updated_at | TIMESTAMP WITH TIME ZONE | DEFAULT NOW() | Local record update |

---

## 5. WHOOP Recovery

Stores daily recovery scores from WHOOP.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | UUID | PK | Primary key |
| user_id | UUID | FK → users.id, NOT NULL | Owner |
| whoop_cycle_id | VARCHAR(100) | UNIQUE, NOT NULL | WHOOP cycle ID |
| recovery_score | DECIMAL(5,2) | | Recovery % (0-100) |
| resting_heart_rate | DECIMAL(5,2) | | RHR (bpm) |
| hrv_rmssd_milli | DECIMAL(10,2) | | HRV in ms |
| spo2_percentage | DECIMAL(5,2) | | Blood oxygen % |
| skin_temp_celsius | DECIMAL(5,2) | | Skin temperature |
| recorded_at | TIMESTAMP WITH TIME ZONE | NOT NULL | Measurement time |
| created_at | TIMESTAMP WITH TIME ZONE | DEFAULT NOW() | Record creation |

---

## 6. WHOOP Sleep

Stores sleep data from WHOOP.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | UUID | PK | Primary key |
| user_id | UUID | FK → users.id, NOT NULL | Owner |
| whoop_sleep_id | VARCHAR(100) | UNIQUE, NOT NULL | WHOOP sleep ID |
| score_state | ENUM | DEFAULT 'PENDING_SCORE' | Score state |
| sleep_performance_percentage | DECIMAL(5,2) | | Performance % |
| sleep_consistency_percentage | DECIMAL(5,2) | | Consistency % |
| sleep_efficiency_percentage | DECIMAL(5,2) | | Efficiency % |
| total_sleep_time_milli | BIGINT | | Total sleep (ms) |
| total_slow_wave_sleep_milli | BIGINT | | Deep sleep (ms) |
| total_rem_sleep_milli | BIGINT | | REM sleep (ms) |
| total_light_sleep_milli | BIGINT | | Light sleep (ms) |
| total_awake_milli | BIGINT | | Awake time (ms) |
| sleep_cycle_count | INTEGER | | Number of cycles |
| disturbance_count | INTEGER | | Disturbances |
| respiratory_rate | DECIMAL(5,2) | | Breaths/min |
| started_at | TIMESTAMP WITH TIME ZONE | NOT NULL | Sleep start |
| ended_at | TIMESTAMP WITH TIME ZONE | NOT NULL | Sleep end |
| created_at | TIMESTAMP WITH TIME ZONE | DEFAULT NOW() | Record creation |

---

## 7. Daily Summaries

Aggregated daily statistics.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | UUID | PK | Primary key |
| user_id | UUID | FK → users.id, NOT NULL | Owner |
| summary_date | DATE | NOT NULL | Summary date |
| total_calories_in | DECIMAL(10,2) | DEFAULT 0 | Food calories |
| total_protein | DECIMAL(10,2) | DEFAULT 0 | Total protein |
| total_fat | DECIMAL(10,2) | DEFAULT 0 | Total fat |
| total_carbs | DECIMAL(10,2) | DEFAULT 0 | Total carbs |
| total_calories_out | DECIMAL(10,2) | DEFAULT 0 | Burned calories |
| calorie_balance | DECIMAL(10,2) | DEFAULT 0 | In - Out |
| workout_count | INTEGER | DEFAULT 0 | Number of workouts |
| total_workout_minutes | INTEGER | DEFAULT 0 | Total workout time |
| total_strain | DECIMAL(5,2) | DEFAULT 0 | Combined strain |
| avg_mood | DECIMAL(3,1) | | Average mood |
| avg_energy | DECIMAL(3,1) | | Average energy |
| recovery_score | DECIMAL(5,2) | | WHOOP recovery |
| sleep_hours | DECIMAL(4,2) | | Hours slept |
| sleep_performance | DECIMAL(5,2) | | Sleep performance % |
| notes | TEXT | | Daily notes |
| created_at | TIMESTAMP WITH TIME ZONE | DEFAULT NOW() | Record creation |
| updated_at | TIMESTAMP WITH TIME ZONE | DEFAULT NOW() | Record update |

### Constraints
- `UNIQUE(user_id, summary_date)`

---

## Enums

### meal_type
- `breakfast`
- `lunch`
- `dinner`
- `snack`

### workout_score_state
- `SCORED`
- `PENDING_SCORE`
- `UNSCORABLE`

### sync_type
- `whoop_workout`
- `whoop_recovery`
- `whoop_sleep`
- `fatsecret`
- `apple_health` (migration 007)

### data_source (migration 007)
- `whoop`
- `apple_health`
- `fatsecret`
- `manual`

### sync_status
- `started`
- `completed`
- `failed`

---

## 8. Apple Health (migrations 007, 009, 010, 011, 013, 014)

### apple_health_sync

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | SERIAL | PK | |
| user_id | INTEGER | FK → users.id, UNIQUE | Owner |
| is_active | BOOLEAN | DEFAULT TRUE | Webhook enabled |
| secret_key | VARCHAR(255) | UNIQUE | `sha256:<hex>` of the per-user webhook token (014); rotated by `/connect_apple_health` |
| last_sync_at | TIMESTAMPTZ | | Last successful snapshot |
| next_sync_at | TIMESTAMPTZ | | Informational only |
| sync_frequency_hours | INTEGER | CHECK 1..24 | From `APPLE_HEALTH_SYNC_HOURS` |
| success_count / error_count | INTEGER | | Counters |
| last_error_message | TEXT | | Bounded to 256 chars |

### health_daily_metric_aggregates (schema v3 — current write target)

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | SERIAL | PK | |
| user_id | INTEGER | FK → users.id | Owner |
| source | data_source | | Always `apple_health` |
| collector | VARCHAR(32) | | `shortcut`, `health_auto_export`, `legacy_daily`, `legacy_backfill` |
| metric_date | DATE | | Local calendar day |
| metric_family | VARCHAR(32) | CHECK `family_check_v2` | See families below |
| timezone | VARCHAR(64) | | IANA name or UTC offset of the snapshot |
| total_value | NUMERIC(15,4) | ≥ 0, not NaN | Sum families + sleep seconds |
| average_value | NUMERIC(15,4) | NULL or ≥ 0, not NaN | Average families |
| sample_count | INTEGER | ≥ 0 | Samples behind the average |
| samples_received / samples_aggregated | INTEGER | ≥ 0 | Diagnostics |
| metrics | JSONB | | `records_by_type`, sleep stage seconds |
| snapshot_generated_at | TIMESTAMPTZ | NOT NULL | Freshness marker |
| payload_hash | VARCHAR(64) | NOT NULL | Replay/conflict detection |

Natural key: `UNIQUE(user_id, source, collector, metric_date, metric_family)`.
Indexes: `(user_id, metric_date)`, `(user_id, metric_family, metric_date,
snapshot_generated_at DESC)`, `(collector)`, and (011)
`(user_id, metric_family, metric_date DESC)`.

| metric_family | Stored in | Canonical unit | Added |
|---|---|---|---|
| steps | total_value (sum) | count | 010 |
| active_energy | total_value (sum) | kcal | 010 |
| heart_rate | average_value | count/min | 010 |
| hrv | average_value | ms (SDNN) | 010 |
| sleep | total_value | seconds (union of asleep stages) | 010 |
| resting_heart_rate | average_value | count/min | 011 |
| body_mass | average_value | kg | 011 |
| distance | total_value (sum) | m | 011 |
| exercise_time | total_value (sum) | min | 011 |

### Legacy (read-only during rollout)

- `health_daily_aggregates` (009, schema v2): one row per user/day with fixed
  columns for the first five families.
- `health_data` (007): raw samples; no longer written (raw retention 0).
- `apple_health_import_logs` (007): sanitized per-request accounting.
- `health_conflicts` (007): never used; no longer created, dropped by 015.

### health_workouts (013)

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | SERIAL | PK | |
| user_id | INTEGER | FK → users.id | Owner |
| source | data_source | | `apple_health` |
| collector | VARCHAR(32) | | `shortcut` / `health_auto_export` |
| external_id | VARCHAR(128) | UNIQUE with user_id, source | HealthKit/HAE UUID or `derived:<hash>` |
| workout_type | VARCHAR(64) | | e.g. Running |
| started_at / ended_at | TIMESTAMPTZ | ended_at ≥ started_at | |
| duration_seconds | INTEGER | 0..86400 | |
| active_energy_kcal | NUMERIC(10,2) | ≥ 0, not NaN | |
| distance_m | NUMERIC(12,2) | ≥ 0, not NaN | |
| avg_heart_rate / max_heart_rate | NUMERIC(6,2) | 0..300 | |
| timezone | VARCHAR(64) | | Sender timezone if known |

Index: `(user_id, started_at DESC)`.

## 9. Users — production additions

| Column | Migration | Description |
|--------|-----------|-------------|
| whoop_access_token, whoop_refresh_token, fatsecret_access_token, fatsecret_access_secret | 002/003 | Plain text |
| timezone | 002 | IANA name; `/timezone` |
| language | 002 | `uk` / `en`; `/language` |
| birth_year, sex (`male`/`female`), height_cm | 012 | BMR inputs; `/profile`; CHECK `users_profile_check` |

## 10. Dropped (015, only when empty)

`mood_entries`, `whoop_activities`, `whoop_recovery`, `whoop_sleep`,
`daily_summaries`, `sync_logs`, `health_conflicts`, view
`v_daily_calorie_balance`. Sections 3–7 above describe the original design
and are kept for history.

## 11. Food ledger & catalog (migration 016)

Full behaviour: [`docs/en/food-logging.md`](../../docs/en/food-logging.md). INTEGER keys throughout.

### food_entries — production additions (the local ledger)

| Column | Description |
|--------|-------------|
| product_id → food_products, nutrition_version_id → food_nutrition_versions | Product and the exact nutrition revision used |
| grams, quantity_source (`explicit`/`reused`/`label`) | User-provided weight (0 < g ≤ 5000) |
| local_date, timezone | User-local calendar date (backfilled for legacy rows) |
| origin | `legacy`, `bot_text`, `bot_voice`, `bot_photo`, `web_manual`, `web_upload` |
| entry_status (`committed`/`voided`), voided_at, revision, version | Revisions instead of deletes; optimistic version |
| idempotency_key | UNIQUE per user (`uq_food_entries_idempotency`) |
| remote_provider, remote_food_id, remote_serving_id, remote_units, remote_entry_id | FatSecret identity; UNIQUE (user, provider, remote_entry_id) |
| sync_status | `local_only`, `pending`, `synced`, `failed`, `unknown`, `not_supported`, `delete_pending`, `deleted`, `delete_failed` |
| nutrition_source, nutrition_expires_at | FatSecret-derived values expire (≤ 24 h) and are nulled by the purge |
| calories | Now nullable: unknown ≠ 0 |

### New tables

| Table | Key points |
|-------|-----------|
| food_products | provider `fatsecret`/`off`/`label`/`manual`/`recipe`; shared identity UNIQUE (provider, external_id) where owner IS NULL; personal UNIQUE (owner, provider, external_id); barcode as string; `provider_name/brand` + `provider_cached_until` (FatSecret cache); `is_starter` for the admin starter catalog |
| food_nutrition_versions | basis quantity/unit (`g`/`ml`/`serving`), `grams_per_basis`, nutrients (NULL = unknown), `source`, `source_revision`, `storage_policy` (`durable`/`provider_cache` + `expires_at`), `is_current`; FatSecret servings carry `serving_id` + `fatsecret_units_per_basis` |
| user_product_memberships | My Products: origin (`fatsecret_history`, `bot`, `manual`, `web`, `starter`, `label`, `barcode`), state `active`/`excluded`/`archived` (+ `excluded_at`), display_name/preparation/usual portion overrides, known serving ids, confirmed/history counts, provider rank, version |
| food_default_rules | alias → product (+ serving, preparation, suggested portion), origin `manual` (one active per alias) / `learned` (per alias + product), enabled, priority, version |
| food_log_drafts | chat/message/reply/media-group linkage, origin, state (`received` … `committed`/`cancelled`/`expired`/`failed`), items JSONB, `commit_key` UNIQUE, committed entry ids, expiry (24 h) |
| food_sync_outbox | (entry, revision, operation) UNIQUE; `create`/`edit`/`delete`; status `pending`/`sending`/`succeeded`/`failed`/`unknown`/`not_supported`/`cancelled`; lease, attempts, next attempt, dispatched_at |
| catalog_import_jobs / catalog_import_candidates | Resumable FatSecret history scans (range, mode `auto`/`selective`, checkpoint, counts, status, one active per user) and selective candidates |
| external_lookup_cache | Open Food Facts hit (7 d) / miss (1 h) cache |

## 12. Web App, preferences, admin (migration 017)

| Table | Key points |
|-------|-----------|
| user_preferences | Typed JSON validated by `app/services/preferences.py`, version |
| user_goal_history | Date-effective kcal/macro goals, UNIQUE (user, effective_date) |
| webapp_sessions | SHA-256 token + CSRF hashes, expiry, revocation |
| user_roles | (user, role=`admin`), granted_by, revoked_at |
| feature_flags | key, enabled, version, updated_by |
| admin_audit_log | actor, action, target, revision, result, compact details |
| notification_sends | (user, kind, local_date) — once-per-day briefing claims |
