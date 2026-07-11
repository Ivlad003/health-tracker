# 🔌 Інтеграція API

[🇬🇧 English version](../en/api-integration.md)

## Огляд

Система інтегрується з чотирма зовнішніми джерелами/API:
- **FatSecret** - база даних продуктів та калорійності
- **WHOOP** - дані про фізичну активність
- **Apple Health** - iOS health-метрики через native Shortcuts або підтриманий
  сторонній застосунок експорту
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

## Apple Health Sync

Apple Health не має backend Web API. Підтримані способи налаштування:

- **Native iOS Shortcuts, без встановлення додаткових застосунків** —
  рекомендований low-friction шлях для користувачів, які не хочуть сторонній
  застосунок.
- **Health Auto Export iOS app** — простіший інтерфейс регулярного експорту,
  але потрібно встановити сторонній застосунок *Health Auto Export — JSON+CSV*.

Справжнього backend-only або zero-device-setup способу для Apple Health немає,
бо дані Apple Health залишаються на iPhone користувача, доки iOS їх не надішле.

### Рекомендовано: готовий iOS Shortcut

Apple Health не має backend Web API. Користувач підключає його через
`/connect_apple_health`: бот генерує персональний токен і webhook URL для iOS
Shortcut.
Повторний запуск `/connect_apple_health` змінює token: новий URL починає
працювати одразу, а всі попередні URL відхиляються. Використовуй повторне
підключення як спосіб відкликати URL або token, якщо він потрапив не туди.

Рекомендований onboarding — готовий Shortcut з назвою
`Health Tracker Apple Health Sync`:

1. У Telegram виконай `/connect_apple_health`.
2. Відкрий Shortcut import link/file на iPhone користувача.
3. Встав згенерований webhook URL в import question Shortcut.
4. Запусти Shortcut один раз і дозволь доступ до Health та Network.
5. У **Shortcuts** -> **Automation** створи **Personal Automation**, наприклад
   **Time of Day**, і вибери імпортований Shortcut для регулярного запуску.

Готовий Shortcut надсилає лише Health-зразки, для яких **Start Date is today**
у локальному календарі iPhone. Тобто день починається з локальної опівночі, а
Shortcut не експортує всю історію Health або рухоме вікно за попередні 24 години.

Backend віддає підписаний artifact тут:

```text
GET /api/v1/health/apple-health/shortcut
```

`/connect_apple_health` напряму дає посилання на цей URL, тому окрема env-змінна
для Shortcut URL не потрібна. Editable source template лежить у
`docs/shortcuts/apple-health-sync.shortcut.plist`; підписаний artifact можна
згенерувати через Apple Shortcuts CLI:

```bash
cp docs/shortcuts/apple-health-sync.shortcut.plist \
  /tmp/apple-health-sync-source.shortcut
shortcuts sign --mode anyone \
  --input /tmp/apple-health-sync-source.shortcut \
  --output docs/shortcuts/apple-health-sync.shortcut
```

Вхідний файл має зберігати розширення `.shortcut`: Shortcuts використовує його,
щоб розпізнати джерело як Shortcut, який можна підписати.

Apple усе одно вимагає підтвердження на пристрої. Повністю backend-only або
zero-touch setup неможливий: імпорт Shortcut, Health permission, Network
permission і Personal Automation прив'язані до конкретного iPhone/iPad.

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

Згенерований URL вже містить Telegram user ID (`users.telegram_user_id`) і
персональний token, тому в Shortcut достатньо вказати URL, `Content-Type:
application/json` і payload з метриками. **Не додавай поля `userId` чи `token`
у Request Body** — вони вже у URL, і дублювання у тілі — типова помилка при
налаштуванні Shortcut. Webhook також підтримує старий варіант:
`X-Apple-Health-Token` header і поле `userId` у body (для зворотної сумісності).
Метрики старші за 30 днів відхиляються. Дані Apple Health зберігаються в
уніфікованій таблиці `health_data` із `source = 'apple_health'`.

Endpoint очікує JSON, але якщо тіло не є валідним JSON, він пробує розпарсити
його як Apple property list (бінарний `bplist00` або XML plist). Це покриває
налаштування Shortcuts, де словник проходить через кроки **Get Type** / plist і
в тіло запиту потрапляють байти plist замість JSON. Дати з plist конвертуються
в ISO 8601 рядки, а data-блоби — в UTF-8 (або Base64) рядки перед інжестом.
Тіла, які не є ані JSON, ані plist-словником, як і раніше відхиляються з
`400 Invalid JSON payload`.

#### Ручний fallback для Shortcut

Використовуй це тільки якщо готовий Shortcut не імпортується або його потрібно
налагодити.

1. У Telegram виконай `/connect_apple_health` і скопіюй згенерований URL. Запусти команду
   повторно пізніше, якщо потрібно відкликати старий URL і створити новий.
2. На iPhone відкрий **Shortcuts** -> **Automation** -> **New Automation**.
3. Обери **Time of Day**, задай час синхронізації та повторення щодня. Для
   частішої синхронізації створи кілька automation, наприклад ранок, день і
   вечір.
4. Додай дію **Find Health Samples**.
   - Type: обери метрику, наприклад **Steps**.
   - Start Date: обери **is today**. Не використовуй рухомий діапазон
     `Current Date - 1 day`; **is today** використовує локальний календарний
     день iPhone, що починається з опівночі.
   - Group By: **Hour** або **Day**.
5. Додай дію **Repeat with Each** для результату Health Samples.
6. Усередині repeat-блоку додай дію **Dictionary** для одного metric object:
   - `type`: `step_count`
   - `value`: властивість **Value** поточного repeat item («Quantity» не є
     властивістю health sample і рендериться порожнім рядком). Сервер також
     приймає текстові значення на кшталт `"434 count"` чи `"68,5"` і
     видобуває числову частину.
   - `unit`: `count`
   - `timestamp`: start date поточного repeat item через **Format Date** у
     форматі **ISO 8601**
   - `duration`: `3600` для hourly samples або пропусти, якщо duration невідомий
7. Додавай кожен metric dictionary до list variable з назвою `metrics`.
8. Після repeat-блоку додай фінальну дію **Dictionary** для request body:
   - `sourceType`: `apple_health`
   - `dataType`: `activity`
   - `metrics`: list variable `metrics`
9. Додай **Get Contents of URL**:
   - URL: встав URL з Telegram із кроку 1.
   - Method: **POST**.
   - Headers: `Content-Type` = `application/json`.
   - Request Body: **JSON** або **Dictionary**, використай request body
     dictionary з кроку 8.
10. Запусти Shortcut один раз вручну. Успішний перший sync повертає JSON на
    кшталт `{"records_received": 1, "records_processed": 1,
    "records_failed": 0}`.

Для додаткових Health-метрик повтори той самий шаблон і зміни `type`, `unit` та
`dataType`. Тримай timestamps у форматі ISO 8601, а кожну метрику — новішою за
30 днів.

### Health Auto Export iOS app (сторонній застосунок)

Той самий endpoint `/api/v1/health/apple-health/sync` також приймає JSON у
форматі застосунку *Health Auto Export — JSON+CSV*. Коли тіло запиту має
форму (`{"data": {"metrics": [{"name", "units", "data": [...]}]}}`), сервер
розгортає кожну точку `data[]` в окрему внутрішню метрику й пропускає її через
ту саму пайплайн-обробку.

Налаштування:

1. У Telegram виконай `/connect_apple_health` і скопіюй URL. Запусти команду
   повторно пізніше, якщо потрібно відкликати старий URL і створити новий.
2. Встанови **Health Auto Export — JSON+CSV** з App Store.
3. Додай нову автоматизацію в застосунку:
   - Output: **JSON (REST API)**
   - URL: встав URL з кроку 1
   - Aggregation: наприклад, hourly або daily
   - Metrics: обери будь-які (або **All**)
4. Запусти один раз для перевірки. Сервер поверне `{"records_received": N,
   "records_processed": M, ...}`.

Цей шлях не потребує створення iOS Shortcut, але потребує встановлення й
налаштування стороннього застосунку Health Auto Export.

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
