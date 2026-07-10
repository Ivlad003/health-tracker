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

Apple Health does not provide a backend Web API. The supported setup paths are:

- **Native iOS Shortcuts, no extra app install** — recommended low-friction path
  for users who do not want a third-party app.
- **Health Auto Export iOS app** — simpler recurring export UI, but requires
  installing the third-party *Health Auto Export — JSON+CSV* app.

There is no true backend-only or zero-device-setup Apple Health sync path,
because Apple Health data stays on the user's iPhone unless iOS sends it out.

### Native iOS Shortcuts Setup

Apple Health does not provide a backend Web API. Users connect it through
`/connect_apple_health`, which generates a per-user token and webhook URL for an
iOS Shortcut.
Running `/connect_apple_health` again rotates that token: the new URL starts
working immediately and any previously generated URL is rejected. Use reconnect
as the revocation path if a URL or token is leaked.

```bash
POST /api/v1/health/apple-health/sync?userId={telegram_user_id}&token={per_user_token}
Content-Type: application/json
```

```json
{
  "sourceType": "apple_health",
  "dataType": "activity",
  "metrics": [
    {
      "type": "step_count",
      "value": 5000,
      "unit": "count",
      "timestamp": "2026-05-20T10:00:00Z",
      "duration": 3600
    }
  ]
}
```

The generated URL already contains the Telegram user ID (`users.telegram_user_id`)
and per-user token, so the Shortcut only needs the URL, `Content-Type:
application/json`, and the metrics payload. **Do not add `userId` or `token`
fields to the Request Body** — they are already in the URL, and duplicating them
in the body is a common Shortcut-setup mistake. The webhook still accepts the
legacy `X-Apple-Health-Token` header and `userId` body field for backward
compatibility. Metrics older than 30 days are rejected. Apple Health records are
stored in the unified `health_data` table with `source = 'apple_health'`.

#### Tap-by-tap Shortcut instructions

1. In Telegram, run `/connect_apple_health` and copy the generated URL. Re-run
   the command later if you need to revoke the old URL and generate a replacement.
2. On the iPhone, open **Shortcuts** -> **Automation** -> **New Automation**.
3. Choose **Time of Day**, set the desired sync time, and set it to repeat
   daily. For more frequent syncs, create several automations, for example
   morning, afternoon, and evening.
4. Add the **Find Health Samples** action.
   - Type: choose the metric to sync, for example **Steps**.
   - Start Date: `Current Date - 1 day`.
   - End Date: `Current Date`.
   - Group By: **Hour** or **Day**.
5. Add a **Repeat with Each** action for the Health Samples result.
6. Inside the repeat block, add a **Dictionary** action for one metric object:
   - `type`: `step_count`
   - `value`: the repeat item's quantity
   - `unit`: `count`
   - `timestamp`: the repeat item's start date, formatted with **Format Date**
     using **ISO 8601**
   - `duration`: `3600` for hourly grouped samples, or omit it when unknown
7. Append each metric dictionary to a list variable named `metrics`.
8. After the repeat block, add a final **Dictionary** action for the request
   body:
   - `sourceType`: `apple_health`
   - `dataType`: `activity`
   - `metrics`: the `metrics` list variable
9. Add **Get Contents of URL**:
   - URL: paste the Telegram URL from step 1.
   - Method: **POST**.
   - Headers: `Content-Type` = `application/json`.
   - Request Body: **JSON** or **Dictionary**, using the request body dictionary
     from step 8.
10. Run the Shortcut once manually. A successful first sync returns JSON like
    `{"records_received": 1, "records_processed": 1, "records_failed": 0}`.

For additional Health metrics, repeat the same pattern and change `type`, `unit`,
and `dataType` as needed. Keep timestamps in ISO 8601 format and keep each metric
newer than 30 days.

### Health Auto Export iOS app (third-party app path)

The same `/api/v1/health/apple-health/sync` endpoint also accepts the JSON
schema produced by the *Health Auto Export — JSON+CSV* iOS app. When the body
matches that shape (`{"data": {"metrics": [{"name", "units", "data": [...]}]}}`),
the server flattens each `data[]` point into one internal metric and ingests it
through the same pipeline.

Setup:

1. In Telegram, run `/connect_apple_health` and copy the URL. Re-run the
   command later if you need to revoke the old URL and generate a replacement.
2. Install **Health Auto Export — JSON+CSV** from the App Store.
3. Add a new automation in the app:
   - Output: **JSON (REST API)**
   - URL: paste the URL from step 1
   - Aggregation: e.g. hourly or daily
   - Metrics: select whichever you want (or **All**)
4. Run once to verify. The server will return `{"records_received": N,
   "records_processed": M, ...}`.

This path does not require building an iOS Shortcut, but it does require
installing and configuring the third-party Health Auto Export app.

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
