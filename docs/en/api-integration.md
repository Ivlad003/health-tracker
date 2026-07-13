# 🔌 API Integration

[🇺🇦 Українська версія](../uk/api-integration.md)

## Overview

The system integrates with four external data sources/APIs:
- **FatSecret** - food database and calorie information
- **WHOOP** - physical activity data
- **Apple Health** - iOS health metrics via native Shortcuts or a supported
  third-party export app
- **OpenAI** - speech recognition and text analysis

---

## FatSecret API

### Authentication

FatSecret uses OAuth 2.0 (Client Credentials) for public data.

```bash
POST https://oauth.fatsecret.com/connect/token
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials
&client_id={YOUR_CLIENT_ID}
&client_secret={YOUR_CLIENT_SECRET}
&scope=basic
```

**Response:**
```json
{
  "access_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6...",
  "token_type": "Bearer",
  "expires_in": 86400
}
```

### Food Search

```bash
GET https://platform.fatsecret.com/rest/food/search/v1
Authorization: Bearer {access_token}
Content-Type: application/json

?search_expression=oatmeal&format=json&max_results=10
```

**Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| search_expression | string | Search query |
| format | string | json or xml |
| max_results | int | Maximum results (1-50) |
| page_number | int | Page number |

### Get Food Details

```bash
GET https://platform.fatsecret.com/rest/food/v5
Authorization: Bearer {access_token}

?food_id=33691&format=json
```

**Response:**
```json
{
  "food": {
    "food_id": "33691",
    "food_name": "Oatmeal",
    "food_type": "Generic",
    "servings": {
      "serving": [
        {
          "serving_id": "34324",
          "serving_description": "1 cup cooked",
          "metric_serving_amount": "234.000",
          "metric_serving_unit": "g",
          "calories": "158",
          "protein": "6.00",
          "fat": "3.20",
          "carbohydrate": "27.40"
        }
      ]
    }
  }
}
```

---

## WHOOP API

### Authentication

WHOOP uses OAuth 2.0 Authorization Code Flow.

**Step 1: Authorization**
```
GET https://api.prod.whoop.com/oauth/oauth2/auth
?client_id={CLIENT_ID}
&redirect_uri={REDIRECT_URI}
&response_type=code
&scope=read:workout read:recovery read:sleep read:cycles
&state={RANDOM_STATE}
```

**Step 2: Exchange Code for Token**
```bash
POST https://api.prod.whoop.com/oauth/oauth2/token
Content-Type: application/x-www-form-urlencoded

grant_type=authorization_code
&code={AUTH_CODE}
&redirect_uri={REDIRECT_URI}
&client_id={CLIENT_ID}
&client_secret={CLIENT_SECRET}
```

**Step 3: Refresh Token**
```bash
POST https://api.prod.whoop.com/oauth/oauth2/token
Content-Type: application/x-www-form-urlencoded

grant_type=refresh_token
&refresh_token={REFRESH_TOKEN}
&client_id={CLIENT_ID}
&client_secret={CLIENT_SECRET}
```

### Scopes

| Scope | Description |
|-------|-------------|
| read:workout | Workout data |
| read:recovery | Recovery metrics |
| read:sleep | Sleep data |
| read:cycles | Physiological cycles |
| read:body_measurement | Body measurements |

### Get Workouts

```bash
GET https://api.prod.whoop.com/developer/v2/activity/workout
Authorization: Bearer {access_token}

?limit=10&start=2026-01-01T00:00:00Z
```

**Response:**
```json
{
  "records": [
    {
      "id": "ecfc6a15-4661-442f-a9a4-f160dd7afae8",
      "user_id": 9012,
      "sport_name": "running",
      "start": "2026-01-28T10:00:00Z",
      "end": "2026-01-28T10:45:00Z",
      "score_state": "SCORED",
      "score": {
        "strain": 8.5,
        "kilojoule": 1340.5,
        "average_heart_rate": 145,
        "max_heart_rate": 172,
        "zone_durations": {
          "zone_one_milli": 300000,
          "zone_two_milli": 600000,
          "zone_three_milli": 1200000,
          "zone_four_milli": 900000,
          "zone_five_milli": 180000
        }
      }
    }
  ],
  "next_token": "MTIzOjEyMzEyMw"
}
```

### Get Recovery

```bash
GET https://api.prod.whoop.com/developer/v2/recovery
Authorization: Bearer {access_token}
```

### Conversion Formula

```
Calories (kcal) = Kilojoules / 4.184
```

---

## Apple Health Sync

Apple Health does not provide a backend Web API. The supported direct setup path is:

- **Native iOS Shortcuts, no extra app install** — recommended low-friction path
  for users who do not want a third-party app.

HAE-shaped JSON is accepted only from an advanced client-side wrapper that can
mint a causal export timestamp before network dispatch. Stock *Health Auto
Export — JSON+CSV* REST automation is not a supported direct client because its
custom headers are static and do not provide an export-created timestamp.

There is no true backend-only or zero-device-setup Apple Health sync path,
because Apple Health data stays on the user's iPhone unless iOS sends it out.

### Recommended: ready-to-import iOS Shortcut

Apple Health does not provide a backend Web API. Users connect it through
`/connect_apple_health`, which generates a per-user token and webhook URL for an
iOS Shortcut.
Running `/connect_apple_health` again rotates that token: the new URL starts
working immediately and any previously generated URL is rejected. Use reconnect
as the revocation path if a URL or token is leaked.

The recommended onboarding path is a ready Shortcut named
`Health Tracker Apple Health Sync`:

1. In Telegram, run `/connect_apple_health`.
2. Open the Shortcut import link/file on the user's iPhone or iPad.
3. Paste the generated webhook URL into the Shortcut's import question.
4. Run the Shortcut once and approve the Health and Network permissions.
5. In **Shortcuts** -> **Automation**, create a **Personal Automation** such as
   **Time of Day** and choose the imported Shortcut for recurring sync.

Open and run the Shortcut on an iPhone or iPad. macOS does not support the
**Find Health Samples** action. When a Mac opens the download endpoint, the
server shows a device-handoff page instead of installing a Shortcut that cannot
run there. iPad browsers that use a desktop-style `Macintosh` user agent still
receive the signed Shortcut file.

The supplied Shortcut runs four **Find Health Samples** queries and merges their
results into a single POST:

| Health type (picker label) | Sent as `type` | Unit | Date filter |
|---|---|---|---|
| Steps | `step_count` | `count` | Start Date is today |
| Active Calories | `active_energy` | `kcal` | Start Date is today |
| Sleep | `sleep_analysis` | `s` | End Date is today |
| Heart Rate Variability SDNN | `heart_rate_variability` | `ms` | Start Date is today |

Point-in-time metrics send only samples whose **Start Date is today** in the
iPhone's local calendar, so the sync does not export the user's complete Health
history or a rolling previous-24-hour window.

Sleep is handled differently in three ways:

- Sleep samples are category samples whose Value/Duration render as localized
  text in Shortcuts, so each sleep metric is sent with `value: 0` plus `end`
  (ISO 8601 end date) and `stage` fields; the server derives the duration from
  `end` and keeps `stage` as diagnostic data.
- The sleep query uses **End Date is today**. This includes a night that began
  before midnight while keeping the declared snapshot to one complete-so-far
  calendar day; the server attributes each sleep sample to the day its interval
  *ends*.
- Overlapping sleep samples (an In Bed envelope plus Core/REM/Deep stages, or
  iPhone plus Watch sources) are merged as time intervals server-side instead
  of summed, so a night is not double-counted.

HRV (SDNN) is surfaced as a **stress proxy** in briefings and the assistant:
Apple Health has no native stress metric, and lower-than-usual HRV correlates
with higher stress. WHOOP recovery remains the primary recovery signal for
users who have WHOOP connected. If the imported Shortcut shows an empty Type in
the HRV query, reselect *Heart Rate Variability SDNN* manually in the Shortcuts
editor — the picker label can vary by iOS version.

After the Shortcut template changes, existing users must delete the previously
imported Shortcut, import it again from the same link, and approve Health
access for the data types (Health permissions are granted per type). An old
imported copy without the schema-v3 snapshot envelope is rejected by the server.

The backend serves the signed artifact at:

```text
GET /api/v1/health/apple-health/shortcut
```

`/connect_apple_health` links to that URL directly, so no separate Shortcut URL
environment variable is required. The editable source template lives at
`docs/shortcuts/apple-health-sync.shortcut.plist`; regenerate the signed
artifact with Apple's Shortcuts CLI:

```bash
cp docs/shortcuts/apple-health-sync.shortcut.plist \
  /tmp/apple-health-sync-source.shortcut
shortcuts sign --mode anyone \
  --input /tmp/apple-health-sync-source.shortcut \
  --output docs/shortcuts/apple-health-sync.shortcut
```

The input file must retain the `.shortcut` extension: Shortcuts uses it to
recognize the source as a signable Shortcut workflow.

Apple still requires device-side confirmation. A fully backend-only or
zero-touch setup is not possible because the Shortcut import, Health permission,
Network permission, and Personal Automation belong to a specific iPhone/iPad.

```bash
POST /api/v1/health/apple-health/sync?userId={telegram_user_id}&token={per_user_token}
Content-Type: application/json
```

```json
{
  "sourceType": "apple_health",
  "schemaVersion": 3,
  "snapshot": {
    "collector": "shortcut",
    "timezone": "+03:00",
    "coveredDates": ["2026-07-11"],
    "coveredMetricFamilies": ["steps", "active_energy", "sleep", "hrv"],
    "generatedAt": "2026-07-11T10:05:00+03:00"
  },
  "metrics": [
    {
      "type": "step_count",
      "value": 5000,
      "unit": "count",
      "timestamp": "2026-07-11T10:00:00+03:00"
    },
    {
      "type": "sleep_analysis",
      "value": 0,
      "unit": "s",
      "timestamp": "2026-07-10T23:04:00+03:00",
      "end": "2026-07-11T06:34:00+03:00",
      "stage": "Core"
    }
  ]
}
```

`snapshot.timezone` is an IANA name (e.g. `"Europe/Kyiv"`) or a fixed UTC
offset (e.g. `"+03:00"`). The ready Shortcut formats the current device offset
as `XXXXX`. `snapshot.collector` is the stable sender identity, and
`snapshot.generatedAt` is the offset-aware freshness timestamp used to order
snapshots from that collector. Its single `snapshot.coveredDates` entry is the
current local date and `coveredMetricFamilies` declares which families are
complete-so-far for that date. Point-in-time samples use **Start Date is
today**, while sleep uses **End Date is today**.
Only the live collectors `shortcut` and `health_auto_export` are accepted;
native webhook payloads must use `shortcut`. Each family may cover at most 31
dates, all within 30 days before ingestion or one day after it, and a payload
must use exactly one of the two coverage encodings.

The generated URL already contains the Telegram user ID (`users.telegram_user_id`)
and per-user token, so the Shortcut only needs the URL, `Content-Type:
application/json`, and the metrics payload. **Do not add `userId` or `token`
fields to the Request Body** — they are already in the URL, and duplicating them
in the body is a common Shortcut-setup mistake. The webhook still accepts the
legacy `X-Apple-Health-Token` header and `userId` body field for backward
compatibility. Metrics older than 30 days are rejected. Apple Health records are
no longer written to the unified `health_data` table. The server parses and
aggregates the snapshot in memory and stores one processed row per (`user_id`,
`source`, `collector`, `metric_date`, `metric_family`) in
`health_daily_metric_aggregates` (migration
`010_health_daily_metric_aggregates.sql`). The schema-v2
`health_daily_aggregates` table remains readable during the rollout. Raw sample
retention for new Apple Health imports is **0** — no rows are written to
`health_data`, and the raw request body is never forwarded to Telegram. Telegram
receives only a sanitized count summary. The webhook streams at most 5 MiB,
returns HTTP 413 before parsing larger bodies, and maps malformed or excessively
nested JSON to HTTP 400.

#### Per-family aggregate model (raw retention 0)

- **Snapshot envelope (required).** Each POST must carry `schemaVersion: 3` plus
  `snapshot.collector`, an offset-aware `snapshot.generatedAt`,
  `snapshot.timezone`, and either `coveredDates` plus
  `coveredMetricFamilies`, or `coveredDatesByFamily`. The ready Shortcut declares
  the current local date and the four families it actually queried.
- **Per-family completeness.** Only declared families are replaced. An empty
  covered family writes an authoritative zero without erasing unrelated metrics
  collected by another query or integration.
- **Freshness and idempotency.** A newer snapshot from the same collector
  replaces that collector's family row. An explicit newer observation advances
  freshness even when its processed total is unchanged. The same timestamp and
  content is a replay, an older snapshot is ignored as stale, and the same
  freshness timestamp with different processed data returns HTTP 409. For HAE,
  freshness is tracked separately per family/date so one family's newer sample
  cannot make stale data in another family look newer.
- **Attribution day must be covered.** Every sample's attribution day (the
  offset-aware local date of its timestamp; for sleep, the local date of its end
  timestamp) must be declared for that family. The timestamp's encoded offset
  is preserved across DST changes instead of applying one fixed offset to the
  whole export. Undeclared days are rejected as partial or ambiguous.
- **Legacy payloads are rejected, not merged.** Payloads missing
  schema-v3 completeness and freshness metadata are
  rejected with an actionable "Re-import the latest Shortcut…" error. This is
  deliberate: the old rolling-window payload could not guarantee complete days.
- **Health Auto Export-shaped JSON uses a strict raw-snapshot contract.** Each
  request exports exactly one unaggregated metric. The server derives that
  family's complete coverage from the attested date period and explicit
  `X-Health-Tracker-Timezone`, so a covered day with no point is stored as zero
  rather than silently preserving a stale value. Every request must also carry
  `X-Health-Tracker-Generated-At`, minted on the client when that export is
  created and before network dispatch. Receipt time and mutable HealthKit
  sample timestamps are never used as snapshot order.
- **Sleep is stage-aware.** Core, REM, Deep, and other asleep stages are unioned;
  Awake and In Bed do not inflate sleep. If no asleep stages exist, the fallback
  is the union of In Bed minus Awake. An Awake-only covered day stores zero.
  Known Ukrainian Apple stage labels are normalized to the same canonical
  stages; an unknown localized label is ignored and counted diagnostically
  instead of being assumed asleep.
- **Read transition.** Stats first filter candidate rows against the requested
  window in each row's timezone, then select the newest in-window live collector
  independently for each date and family. They fill missing values from schema-v2
  aggregates, migration/backfill rows, and finally legacy raw samples. Different
  live collectors are never summed for the same family.
- **Sync response / Telegram summary.** The response now reports: samples
  received, samples aggregated, family rows updated/replayed/stale, failures,
  and `raw stored: 0`.

#### Legacy raw-data backfill

Migration `010` is additive. Backfill legacy Apple Health rows without deleting
them first:

```bash
python -m app.backfill_apple_health
```

The default timezone is `Europe/Kyiv`, and generated rows use collector
`legacy_backfill`. After application read-back and row-count verification, an
operator may run the explicit contract phase:

```bash
python -m app.backfill_apple_health --delete-raw
```

Delete mode removes only the exact raw row IDs locked and verified in that run,
checks the returned ID set, and exits non-zero on any backfill or residual-purge
failure. It refuses unsupported raw metric types and holds a writer-blocking
table lock through the final zero-row check. Keep the default non-destructive
phase available for rollback.
The `009` and `010` rollback scripts also fail closed when their aggregate table
contains rows and take an exclusive lock before checking; export the data or
deploy a forward fix instead of silently dropping processed Health history.

The endpoint expects JSON, but if the body is not valid JSON it falls back to
parsing it as an Apple property list (binary `bplist00` or XML plist). This
covers Shortcuts setups where the dictionary is coerced through **Get Type** /
plist steps and the request body ends up as plist bytes instead of JSON. Plist
dates are converted to ISO 8601 strings and plist data blobs to UTF-8 (or
Base64) strings before ingestion. Bodies that are neither JSON nor a plist
dictionary are still rejected with `400 Invalid JSON payload`.

#### Manual Shortcut fallback

Use this only if the ready Shortcut cannot be imported or needs debugging.

1. In Telegram, run `/connect_apple_health` and copy the generated URL. Re-run
   the command later if you need to revoke the old URL and generate a replacement.
2. On the iPhone, open **Shortcuts** -> **Automation** -> **New Automation**.
3. Choose **Time of Day**, set the desired sync time, and set it to repeat
   daily. For more frequent syncs, create several automations, for example
   morning, afternoon, and evening.
4. Add the **Find Health Samples** action.
   - Type: choose the metric to sync, for example **Steps**.
   - Start Date: choose **is today**. Do not use a rolling `Current Date - 1 day`
     range; **is today** uses the iPhone's local calendar day beginning at
     midnight.
   - Group By: **Hour** or **Day**.
5. Add a **Repeat with Each** action for the Health Samples result.
6. Inside the repeat block, add a **Dictionary** action for one metric object:
   - `type`: `step_count`
   - `value`: the repeat item's **Value** property ("Quantity" is not a
     health sample property and renders as an empty string). The server
     also accepts text renders such as `"434 count"` or `"68,5"` and
     extracts the numeric part.
   - `unit`: `count`
   - `timestamp`: the repeat item's start date, formatted with **Format Date**
     using **ISO 8601**
   - `duration`: `3600` for hourly grouped samples, or omit it when unknown
7. Append each metric dictionary to a list variable named `metrics`.
8. After the repeat block, format **Current Date** three ways: ISO 8601 without
   time for the covered date, custom format `XXXXX` for the UTC offset, and ISO
   8601 with time for `generatedAt`. Put the covered date in a one-item **List**,
   then create a `snapshot` Dictionary with `timezone`, `coveredDates`, and
   `generatedAt`. Also set `collector` to `shortcut` and add a
   `coveredMetricFamilies` List containing only the families queried by this
   Shortcut. Add a final **Dictionary** action for the request body:
   - `sourceType`: `apple_health`
   - `schemaVersion`: `3`
   - `dataType`: `activity`
   - `snapshot`: the snapshot Dictionary
   - `metrics`: the `metrics` list variable
9. Add **Get Contents of URL**:
   - URL: paste the Telegram URL from step 1.
   - Method: **POST**.
   - Headers: `Content-Type` = `application/json`.
   - Request Body: **JSON** or **Dictionary**, using the request body dictionary
     from step 8.
10. Run the Shortcut once manually. A successful first sync returns JSON like
    `{"schema_version": 3, "records_received": 1, "records_aggregated": 1,
    "aggregate_rows_updated": 1, "aggregate_rows_replayed": 0,
    "aggregate_rows_stale": 0, "raw_stored": 0, "records_failed": 0}`.

For another supported family, repeat the same pattern and declare it in
`coveredMetricFamilies`. Supported families are steps, active energy, heart
rate, HRV, and sleep. Keep timestamps in ISO 8601 format and every metric newer
than 30 days. Unsupported types are reported for diagnostics but are not stored.

### Health Auto Export iOS app (third-party app path)

The same `/api/v1/health/apple-health/sync` endpoint also accepts the JSON
schema produced by the *Health Auto Export — JSON+CSV* iOS app. When the body
matches that shape (`{"data": {"metrics": [{"name", "units", "data": [...]}]}}`),
the server flattens each `data[]` point into one internal metric and ingests it
through the same pipeline.

The stock Health Auto Export REST automation is **not a supported direct sync
client** for schema v3: HAE documents static custom headers but does not provide
a causal export-created timestamp. Setting `X-Health-Tracker-Generated-At` to a
fixed value or adding `now()` at an ingress proxy is unsafe and rejected as a
design pattern; the latter merely renames receipt order. Use the native Apple
Shortcut above for direct phone sync.

An advanced client-side wrapper may relay HAE-shaped JSON only if it mints the
timestamp before dispatch and sends all of the following:

- `automation-period`: `default`, `none` (treated as Default), `today`,
  `yesterday`, or `previous7days`; incremental/realtime periods are rejected.
- `automation-aggregation: none`, Batch Requests off, and exactly one supported
  metric. HAE aggregates when multiple metrics are selected, so multi-metric
  requests are rejected; sleep must be unaggregated segments.
- `X-Health-Tracker-HAE-Mode: complete-unbatched-unaggregated-single-metric-v1`.
- `X-Health-Tracker-Timezone`: an IANA timezone such as `Europe/Kyiv`, or a
  fixed offset such as `+03:00`.
- `X-Health-Tracker-Generated-At`: an offset-aware timestamp created with this
  export before the HTTP request is sent.

The request must stay below 5 MiB. Malformed points and aggregated sleep fail
closed. A newer valid marker can authoritatively clear a period with `data: []`;
an older marker is stale, and different content under the same marker returns
`409`. Accepted sleep segments preserve `startDate`, `endDate`, and `value`;
`Awake` and `In Bed` are never mislabeled as asleep.

---

## OpenAI API

### Whisper (Speech-to-Text)

```bash
POST https://api.openai.com/v1/audio/transcriptions
Authorization: Bearer {OPENAI_API_KEY}
Content-Type: multipart/form-data

file: {audio_file}
model: whisper-1
language: uk
```

### GPT (Text Analysis)

```bash
POST https://api.openai.com/v1/chat/completions
Authorization: Bearer {OPENAI_API_KEY}
Content-Type: application/json

{
  "model": "gpt-4",
  "messages": [
    {
      "role": "system",
      "content": "Extract food items from text. Return JSON array with name, amount, unit."
    },
    {
      "role": "user",
      "content": "I had oatmeal with banana for breakfast, about 200 grams"
    }
  ],
  "response_format": { "type": "json_object" }
}
```

---

## Rate Limits

| API | Limit |
|-----|-------|
| FatSecret | 5,000 requests/day |
| WHOOP | 100 requests/minute |
| OpenAI | Depends on plan |

---

## Error Handling

### HTTP Response Codes

| Code | Meaning | Action |
|------|---------|--------|
| 200 | Success | - |
| 400 | Bad Request | Check parameters |
| 401 | Unauthorized | Refresh token |
| 429 | Rate Limit | Wait and retry |
| 500 | Server Error | Retry later |

### Retry Strategy

```javascript
const retry = async (fn, maxRetries = 3, delay = 1000) => {
  for (let i = 0; i < maxRetries; i++) {
    try {
      return await fn();
    } catch (error) {
      if (i === maxRetries - 1) throw error;
      if (error.status === 429) {
        await sleep(delay * Math.pow(2, i));
      }
    }
  }
};
```
