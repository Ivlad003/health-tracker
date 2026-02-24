# 🔌 Інтеграція API

[🇬🇧 English version](../en/api-integration.md)

## Огляд

Система інтегрується з трьома зовнішніми API:
- **FatSecret** - база даних продуктів та калорійності
- **WHOOP** - дані про фізичну активність
- **OpenAI** - розпізнавання мови та аналіз тексту

---

## FatSecret API

### Автентифікація

FatSecret використовує OAuth 2.0 (Client Credentials) для публічних даних.

```bash
POST https://oauth.fatsecret.com/connect/token
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials
&client_id={YOUR_CLIENT_ID}
&client_secret={YOUR_CLIENT_SECRET}
&scope=basic
```

**Відповідь:**
```json
{
  "access_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6...",
  "token_type": "Bearer",
  "expires_in": 86400
}
```

### Пошук продуктів

```bash
GET https://platform.fatsecret.com/rest/food/search/v1
Authorization: Bearer {access_token}
Content-Type: application/json

?search_expression=oatmeal&format=json&max_results=10
```

**Параметри:**
| Параметр | Тип | Опис |
|----------|-----|------|
| search_expression | string | Пошуковий запит |
| format | string | json або xml |
| max_results | int | Максимум результатів (1-50) |
| page_number | int | Номер сторінки |

### Отримання деталей продукту

```bash
GET https://platform.fatsecret.com/rest/food/v5
Authorization: Bearer {access_token}

?food_id=33691&format=json
```

**Відповідь:**
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

### Автентифікація

WHOOP використовує OAuth 2.0 Authorization Code Flow.

**Крок 1: Авторизація**
```
GET https://api.prod.whoop.com/oauth/oauth2/auth
?client_id={CLIENT_ID}
&redirect_uri={REDIRECT_URI}
&response_type=code
&scope=read:workout read:recovery read:sleep read:cycles
&state={RANDOM_STATE}
```

**Крок 2: Обмін коду на токен**
```bash
POST https://api.prod.whoop.com/oauth/oauth2/token
Content-Type: application/x-www-form-urlencoded

grant_type=authorization_code
&code={AUTH_CODE}
&redirect_uri={REDIRECT_URI}
&client_id={CLIENT_ID}
&client_secret={CLIENT_SECRET}
```

**Крок 3: Оновлення токена**
```bash
POST https://api.prod.whoop.com/oauth/oauth2/token
Content-Type: application/x-www-form-urlencoded

grant_type=refresh_token
&refresh_token={REFRESH_TOKEN}
&client_id={CLIENT_ID}
&client_secret={CLIENT_SECRET}
```

### Scopes (Дозволи)

| Scope | Опис |
|-------|------|
| read:workout | Дані тренувань |
| read:recovery | Показники відновлення |
| read:sleep | Дані сну |
| read:cycles | Фізіологічні цикли |
| read:body_measurement | Виміри тіла |

### Отримання тренувань

```bash
GET https://api.prod.whoop.com/developer/v2/activity/workout
Authorization: Bearer {access_token}

?limit=10&start=2026-01-01T00:00:00Z
```

**Відповідь:**
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

### Отримання відновлення

```bash
GET https://api.prod.whoop.com/developer/v2/recovery
Authorization: Bearer {access_token}
```

**Відповідь:**
```json
{
  "records": [
    {
      "cycle_id": "12345",
      "score_state": "SCORED",
      "score": {
        "recovery_score": 78,
        "resting_heart_rate": 52,
        "hrv_rmssd_milli": 45.5,
        "spo2_percentage": 98.2,
        "skin_temp_celsius": 36.5
      }
    }
  ]
}
```

### Формула конвертації

```
Калорії (kcal) = Кілоджоулі / 4.184
```

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

**Відповідь:**
```json
{
  "text": "На сніданок їв вівсянку з бананом, приблизно двісті грам каші"
}
```

### GPT (Аналіз тексту)

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
      "content": "На сніданок їв вівсянку з бананом, приблизно двісті грам каші"
    }
  ],
  "response_format": { "type": "json_object" }
}
```

**Відповідь:**
```json
{
  "foods": [
    {
      "name": "oatmeal",
      "name_uk": "вівсянка",
      "amount": 200,
      "unit": "g"
    },
    {
      "name": "banana",
      "name_uk": "банан",
      "amount": 1,
      "unit": "piece"
    }
  ],
  "meal_type": "breakfast",
  "confidence": 0.95
}
```

---

## Rate Limits

| API | Ліміт |
|-----|-------|
| FatSecret | 5,000 запитів/день |
| WHOOP | 100 запитів/хвилина |
| OpenAI | Залежить від плану |

---

## Обробка помилок

### HTTP коди відповідей

| Код | Значення | Дія |
|-----|----------|-----|
| 200 | Успіх | - |
| 400 | Невірний запит | Перевірити параметри |
| 401 | Не авторизовано | Оновити токен |
| 429 | Перевищено ліміт | Зачекати та повторити |
| 500 | Помилка сервера | Повторити пізніше |

### Retry стратегія

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
