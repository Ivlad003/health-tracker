# База знань сесії - 2026-02-24

[English version](../en/session-knowledge.md)

> Практичні знання, отримані під час створення та налагодження інтеграції n8n з WHOOP.
> Цей файл є довідником для майбутніх сесій розробки.

---

## 1. Факти про інфраструктуру

| Ресурс | Значення |
|--------|----------|
| n8n інстанс | Див. `.env` -> `N8N_HOST` (v2.35.5, Dokploy) |
| PostgreSQL | Див. `.env` -> `DATABASE_URL` |
| n8n PostgreSQL credential | назва: `pet_pg_db` (ID в n8n credentials UI) |
| n8n API ключ | `.mcp.json` -> `mcpServers.n8n-mcp.env.N8N_API_KEY` |
| Dokploy панель | Див. `.mcp.json` -> `mcpServers.dokploy-mcp.env.DOKPLOY_URL` |
| WHOOP user ID | Зберігається в колонці `users.whoop_user_id` |
| Telegram user ID | Зберігається в колонці `users.telegram_user_id` |

### Розгорнуті n8n Workflows

| Workflow | ID | Статус |
|----------|----|--------|
| WHOOP OAuth Callback | `sWOs9ycgABYKCQ8g` | Активний |
| WHOOP Data Sync | `nAjGDfKdddSDH2MD` | Активний (щогодини) |
| FatSecret Food Search | `qTHRcgiqFx9SqTNm` | Активний (вебхук: `/webhook/food/search?q=`) |
| FatSecret OAuth Connect | `5W40Z9r0cn5Z5Nyx` | Неактивний (crypto заблоковано) |
| FatSecret OAuth Callback | `2kyFWt88FfOt14mw` | Неактивний (crypto заблоковано) |
| FatSecret Food Diary | `5HazDbwcUsZPvXzS` | Неактивний (чекає OAuth) |
| IP Check | `g3IFs0z6mPYtwPI9` | Активний (утиліта) |

---

## 2. WHOOP API - Критичні відкриття

### Версія API: ТІЛЬКИ v2

**WHOOP API використовує v2, НЕ v1.** Всі v1 ендпоінти повертають 404.

| Ендпоінт | URL |
|----------|-----|
| Тренування | `GET /developer/v2/activity/workout` |
| Відновлення | `GET /developer/v2/recovery` |
| Сон | `GET /developer/v2/activity/sleep` |
| Обмін токенів | `POST /oauth/oauth2/token` |
| Авторизація | `GET /oauth/oauth2/auth` |

### Доступні скоупи (перевірені та підтверджені)

```
read:workout read:recovery read:sleep read:body_measurement
```

**Скоупи, які НЕ працюють:**
- `read:cycles` - повертає помилку `invalid_scope`
- `read:profile` - недоступний для цього додатку; v1 profile ендпоінт повертає 401

### Життєвий цикл токена

- Access token діє **3600 секунд (1 година)**
- Refresh token довготривалий
- Оновлення через `POST /oauth/oauth2/token` з `grant_type=refresh_token`
- Для оновлення потрібні лише `client_id` та `client_secret` (без `redirect_uri`)

### OAuth Flow - Робоча URL авторизації

```
https://api.prod.whoop.com/oauth/oauth2/auth?client_id={WHOOP_CLIENT_ID}&redirect_uri={WHOOP_REDIRECT_URI}&response_type=code&scope=read:workout%20read:recovery%20read:sleep%20read:body_measurement&state={TELEGRAM_USER_ID}
```

> Значення `WHOOP_CLIENT_ID` та `WHOOP_REDIRECT_URI` знаходяться в `.env`.

### Отримання User ID без `read:profile`

Оскільки profile ендпоінт недоступний, user_id витягується з відповіді recovery:
```
GET /developer/v2/recovery?limit=1 -> response.records[0].user_id
```

---

## 3. FatSecret API - Критичні відкриття

### Два різних набори credentials

FatSecret використовує **різні credentials** для OAuth 1.0 та OAuth 2.0:

| | OAuth 2.0 | OAuth 1.0 |
|---|---|---|
| Назва ключа | Client ID | Consumer Key |
| Назва секрету | Client Secret | Shared Secret |
| Значення | Однаковий ключ, **різні секрети** | Однаковий ключ, **різні секрети** |
| Призначення | Публічна база продуктів (пошук) | Персональний щоденник харчування |

### OAuth 2.0 (Server-to-Server) - ПРАЦЮЄ

- Token: `POST https://oauth.fatsecret.com/connect/token`
- API: `POST https://platform.fatsecret.com/rest/server.api`
- Скоупи: `basic`, `premier`, `barcode`, `localization`, `nlp`, `image-recognition`
- **Потребує IP whitelist** на `platform.fatsecret.com`

### OAuth 1.0 Three-Legged (дані користувача) - В ПРОЦЕСІ

Потрібен для доступу до щоденника харчування (`food_entries.get`).

**Ендпоінти:**
- Request Token: `POST https://authentication.fatsecret.com/oauth/request_token`
- Авторизація: `GET https://authentication.fatsecret.com/oauth/authorize?oauth_token={token}`
- Access Token: `POST https://authentication.fatsecret.com/oauth/access_token`

**Підхід в n8n:** Вбудований credential типу `oAuth1Api` (автоматично підписує запити).
- Credential: назва `FatSecret OAuth1`, ID `VCx6xRbjDM47owI0`
- Користувач натискає "Connect" в n8n Credentials UI

**БЛОКЕР:** n8n Code ноди **не можуть використовувати `require('crypto')`** - модуль заблокований.

**БЛОКЕР:** FatSecret OAuth1 "Connect" повертає **помилку 400** - ймовірно IP whitelist.
- Вихідний IP n8n сервера: `84.54.23.99` (підтверджено через ipify.org)
- IP додано 2026-02-24 - повторити після 2026-02-25 (до 24 год на активацію)

### Колонки БД додані для FatSecret

```sql
ALTER TABLE users ADD COLUMN IF NOT EXISTS fatsecret_access_token TEXT, fatsecret_access_secret TEXT;
```

---

## 4. Схема БД - Реальність vs Документація

### КРИТИЧНО: Існуюча БД використовує INTEGER, а не UUID

`docs/en/architecture.md` каже "UUID for primary keys", але **реальна продакшн БД** використовує:

```sql
-- Фактична схема (існуюча таблиця):
users.id          -> INTEGER (SERIAL), НЕ UUID
users.telegram_user_id -> BIGINT, НЕ telegram_id
```

Міграція `001_initial_schema.sql` має UUID-схему, але **ніколи не застосовувалась** до існуючої БД. Міграція `002_health_tracker_schema.sql` створена для роботи з існуючою INTEGER-схемою через `ALTER TABLE` та `IF NOT EXISTS`.

### Різниця в назвах колонок

| Документація/Міграція 001 | Фактична колонка в БД |
|---------------------------|----------------------|
| `telegram_id` | `telegram_user_id` |
| `id UUID` | `id INTEGER` |
| `username` | `telegram_username` (додано міграцією 002) |

### Таблиці в продакшні (9 штук)

`users`, `diary_entries` (вже існували), `food_entries`, `mood_entries`, `whoop_activities`, `whoop_recovery`, `whoop_sleep`, `daily_summaries`, `sync_logs`

---

## 5. Сумісність версій нод n8n (v2.35.5)

### КРИТИЧНО: Використовуйте саме ці typeVersions

Інстанс n8n (v2.35.5) **НЕ підтримує** новіші версії нод. Використання непідтримуваних версій призводить до помилки активації:
```
"Cannot read properties of undefined (reading 'execute')"
```

| Тип ноди | Робоча версія | Зламані версії |
|----------|--------------|----------------|
| `scheduleTrigger` | **1.2** | 1.3 |
| `httpRequest` | **4.2** | 4.4 |
| `postgres` | **2.5** | 2.6 |
| `code` | **1** | 2 |
| `webhook` | **2** | - |
| `if` | **2.3** | - |
| `respondToWebhook` | **1.5** | - |

### Синтаксис Code Node v1

В Code node v1:
- "Run Once for All Items": використовуйте масив `items` (не `$input.all()`)
- "Run Once for Each Item": використовуйте `item` (не `$input.item`)
- `$('Node Name').item.json` працює в обох версіях
- `$json` скорочення працює в обох версіях
- **Повертайте `[]` щоб відкинути елементи** - повернення `item` при порожніх даних передає сиру відповідь до наступних нод, що спричиняє SQL помилки. Використовуйте `return [];` щоб зупинити передачу

### Активація Workflow через API

MCP інструменти не мають прямої функції активації. Використовуйте REST API:

```bash
curl -X POST "{N8N_HOST}/api/v1/workflows/{WORKFLOW_ID}/activate" \
  -H "X-N8N-API-KEY: {N8N_API_KEY}" \
  -H "Content-Type: application/json"
```

> `N8N_HOST` в `.env`, `N8N_API_KEY` в `.mcp.json`.

---

## 6. Типові помилки та виправлення

### Парсинг .env у Bash

`source <(grep ...)` та `export $(cat ... | xargs)` **падають**, коли пароль містить спецсимволи. Використовуйте цикл `while IFS= read -r line`.

### PostgreSQL DATE() на TIMESTAMPTZ НЕ є immutable

```sql
-- ПАДАЄ: DATE() залежить від налаштувань timezone
CREATE INDEX idx ON food_entries(user_id, DATE(logged_at));

-- ПРАЦЮЄ: композитний індекс, фільтр у запитах
CREATE INDEX idx ON food_entries(user_id, logged_at);
```

### n8n httpRequest - конфлікт автентифікації

При використанні ручного заголовку `Authorization: Bearer ...`, встановлюйте `authentication: "none"`. Інакше credential може **перезаписати** ваш Bearer token.

### Обрізаний WHOOP_CLIENT_ID

WHOOP client ID в змінних n8n був обрізаний (відсутні останні 2 символи). Завжди перевіряйте повне значення `WHOOP_CLIENT_ID` з `.env` відповідає тому, що налаштовано в Dokploy/n8n.

### n8n IF Node для порівняння DateTime

- Використовуйте `"operation": "before"` (не `"beforeOrEqual"` - його не існує)
- НЕ використовуйте `"singleValue": true` на бінарних операторах типу "exists"

### n8n Code Node - Порожні записи спричиняють SQL помилки

**Баг знайдено 2026-02-24:** Коли WHOOP API повертає порожній масив (`records: []`), ноди Process (Workouts/Recovery/Sleep) виконували `return item;`, що передавало сиру відповідь API до нод Store. SQL шаблон перетворював `{{ $json.user_id }}` в пусте значення, генеруючи `VALUES (, '...'` — синтаксична помилка біля `,`.

**Виправлення:** Замінити `return item;` на `return [];` коли `!records.length`. Повернення порожнього масиву означає, що жодний елемент не передається до ноди Store, і SQL не виконується.

```javascript
// НЕПРАВИЛЬНО - передає сиру відповідь API до наступної ноди
if (!records.length) return item;

// ПРАВИЛЬНО - відкидає елемент, Store нода не виконується
if (!records.length) return [];
```

**Застосовано до:** `Process Workouts`, `Process Recovery`, `Process Sleep` у workflow WHOOP Data Sync (`nAjGDfKdddSDH2MD`).

---

## 7. Архітектура Workflows

### WHOOP OAuth Callback

```
Webhook (GET /whoop/callback)
  -> Перевірка параметрів (code існує?)
    -> ТАК: Обмін коду на токен -> Отримання Recovery (user_id) -> Збереження токенів в БД -> Відповідь (успіх)
    -> НІ: Відповідь (помилка)
```

Ключове: Параметр `state` в OAuth URL несе Telegram user ID для пошуку в БД.

### WHOOP Data Sync

```
Schedule Trigger (кожну годину)
  -> Отримання юзерів з WHOOP токенами
    -> Токен протермінований?
      -> ТАК: Оновити токен -> Зберегти -> Merge
      -> НІ: Merge
        -> Паралельно: Тренування / Відновлення / Сон
          -> Обробка (Code) -> Збереження (PostgreSQL UPSERT)
```

---

## 8. Довідник файлів

| Файл | Призначення |
|------|-------------|
| `database/init-db.sh` | Скрипт ініціалізації БД (fallback через Docker psql) |
| `database/migrations/001_initial_schema.sql` | UUID-схема (НЕ застосована до прод) |
| `database/migrations/002_health_tracker_schema.sql` | ALTER TABLE міграція (застосована до прод) |
| `n8n/workflows/whoop-oauth-callback.json` | Локальна копія OAuth workflow (може бути застарілою) |
| `.env` | Змінні оточення (БД, WHOOP, Telegram, OpenAI) |
| `.mcp.json` | Конфігурація MCP серверів (n8n, Dokploy, Chrome DevTools) |

---

## 9. TODO / Відомі проблеми

### НЕГАЙНО - Наступна сесія (після 24 год на активацію IP whitelist)

- [ ] **Повторити FatSecret OAuth1 Connect** - n8n Credentials -> "FatSecret OAuth1" -> "Connect". IP `84.54.23.99` має бути активним
- [ ] **Тест отримання щоденника** - Після OAuth, активувати `FatSecret Food Diary` (`5HazDbwcUsZPvXzS`)
- [ ] **Побудувати синхронізацію щоденника в БД** - Запланований workflow для синхронізації в `food_entries`
- [ ] **Очистити невикористані workflows** - Видалити `FatSecret OAuth Connect/Callback` (використовують заблокований `crypto`)

### БЭКЛОГ

- [ ] **Оновити локальні JSON файли workflows** в `n8n/workflows/`
- [ ] **Виправити `docs/en/architecture.md`** - UUID vs INTEGER
- [ ] **Виправити `docs/en/api-integration.md`** - прибрати `read:cycles`, додати FatSecret OAuth 1.0 vs 2.0
- [ ] **WHOOP OAuth webhook-test URL** - змінити на `/webhook/whoop/callback` для продакшну
- [ ] **Ризик SQL ін'єкції** в запитах Store Tokens
- [ ] **Перевірити WHOOP_CLIENT_ID** в n8n/Dokploy
- [ ] **Гонка токенів WHOOP** - інтервал синхронізації = термін токена (1 год)
- [ ] **n8n Code ноди не можуть `require('crypto')`** - підписання тільки через вбудовані credential типи
