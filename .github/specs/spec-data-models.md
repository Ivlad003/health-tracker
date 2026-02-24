# Spec: Data Models
# Health & Wellness Tracker Bot

## Status: Draft
## Version: 0.1.0

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

### sync_status
- `started`
- `completed`
- `failed`
