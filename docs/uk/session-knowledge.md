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

## 3. Схема БД - Реальність vs Документація

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

## 4. Сумісність версій нод n8n (v2.35.5)

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

### Активація Workflow через API

MCP інструменти не мають прямої функції активації. Використовуйте REST API:

```bash
curl -X POST "{N8N_HOST}/api/v1/workflows/{WORKFLOW_ID}/activate" \
  -H "X-N8N-API-KEY: {N8N_API_KEY}" \
  -H "Content-Type: application/json"
```

> `N8N_HOST` в `.env`, `N8N_API_KEY` в `.mcp.json`.

---

## 5. Типові помилки та виправлення

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

---

## 6. Архітектура Workflows

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

## 7. Довідник файлів

| Файл | Призначення |
|------|-------------|
| `database/init-db.sh` | Скрипт ініціалізації БД (fallback через Docker psql) |
| `database/migrations/001_initial_schema.sql` | UUID-схема (НЕ застосована до прод) |
| `database/migrations/002_health_tracker_schema.sql` | ALTER TABLE міграція (застосована до прод) |
| `n8n/workflows/whoop-oauth-callback.json` | Локальна копія OAuth workflow (може бути застарілою) |
| `.env` | Змінні оточення (БД, WHOOP, Telegram, OpenAI) |
| `.mcp.json` | Конфігурація MCP серверів (n8n, Dokploy, Chrome DevTools) |

---

## 8. TODO / Відомі проблеми

- [ ] **Оновити локальні JSON файли workflows** в `n8n/workflows/` щоб відповідали розгорнутим версіям
- [ ] **Виправити `docs/en/architecture.md`** - написано "UUID for primary keys", а БД використовує INTEGER
- [ ] **Виправити `docs/en/api-integration.md`** - прибрати `read:cycles` зі списку скоупів (невалідний)
- [ ] **WHOOP OAuth використовує webhook-test URL** - для продакшну змінити на `/webhook/whoop/callback`
- [ ] **Ризик SQL ін'єкції** в запиті Store Tokens - використовує інтерполяцію рядків для токенів
- [ ] **Перевірити WHOOP_CLIENT_ID** в n8n/Dokploy що не обрізаний
- [ ] **Гонка між інтервалом синхронізації та терміном токена** - обидва 1 година, розглянути превентивне оновлення
