# База знань сесії - 2026-02-25

[English version](../en/session-knowledge.md)

> Практичні знання, отримані під час створення Health Tracker бота.
> Цей файл є довідником для майбутніх сесій розробки.

---

## 1. Факти про інфраструктуру

| Ресурс | Значення |
|--------|----------|
| Додаток | FastAPI Python 3.12+ (Docker на Dokploy) |
| PostgreSQL | Див. `.env` -> `DATABASE_URL` |
| Dokploy панель | Див. `.mcp.json` -> `mcpServers.dokploy-mcp.env.DOKPLOY_URL` |
| WHOOP user ID | Зберігається в колонці `users.whoop_user_id` |
| Telegram user ID | Зберігається в колонці `users.telegram_user_id` |

### Сервіси додатку

| Сервіс | Файл | Призначення |
|--------|------|-------------|
| Telegram Bot | `app/services/telegram_bot.py` | Обробка повідомлень, команди (/start, /help, /sync, /connect_whoop, /connect_fatsecret) |
| AI Assistant | `app/services/ai_assistant.py` | GPT класифікація + відповідь, статистика калорій |
| WHOOP Sync | `app/services/whoop_sync.py` | OAuth 2.0, синхронізація даних, оновлення токенів |
| FatSecret API | `app/services/fatsecret_api.py` | OAuth 1.0, пошук їжі, синхронізація щоденника, перевірка токенів |
| FatSecret Auth | `app/services/fatsecret_auth.py` | OAuth 1.0 HMAC-SHA1 підписання |
| Briefings | `app/services/briefings.py` | Ранкові (08:00) / вечірні (21:00) повідомлення |
| Scheduler | `app/scheduler.py` | APScheduler періодичні задачі |

### Заплановані задачі

| Задача | Частота | Призначення |
|--------|---------|-------------|
| WHOOP Data Sync | Кожну 1г | Синхронізація тренувань, сну, відновлення |
| WHOOP Token Refresh | Кожні 30хв | Проактивне оновлення токенів |
| FatSecret Data Sync | Кожну 1г | Синхронізація щоденника їжі |
| FatSecret Token Check | Кожні 30хв | Перевірка токенів, сповіщення при закінченні |
| Morning Briefing | 08:00 Europe/Kyiv | Ранковий огляд здоров'я |
| Evening Summary | 21:00 Europe/Kyiv | Вечірній звіт |
| Conversation Cleanup | 03:00 UTC | Очищення старої історії розмов |

---

## 2. WHOOP API - Критичні відкриття

### Версія API: ТІЛЬКИ v2

**WHOOP API використовує v2, НЕ v1.** Всі v1 ендпоінти повертають 404.

| Ендпоінт | URL |
|----------|-----|
| Тренування | `GET /developer/v2/activity/workout` |
| Відновлення | `GET /developer/v2/recovery` |
| Сон | `GET /developer/v2/activity/sleep` |
| Денний цикл | `GET /developer/v2/cycle` |
| Обмін токенів | `POST /oauth/oauth2/token` |
| Авторизація | `GET /oauth/oauth2/auth` |

### Доступні скоупи (перевірені та підтверджені)

```
read:workout read:recovery read:sleep read:body_measurement
```

**Скоупи, які НЕ працюють:**
- `read:cycles` - повертає помилку `invalid_scope`
- `read:profile` - недоступний для цього додатку; v1 profile ендпоінт повертає 401

### Дані про кроки НЕ доступні через API

WHOOP відстежує кроки в додатку (додано 2025), але Developer API v2 **не надає** дані про кількість кроків. Немає ендпоінту або поля для кроків. Системний промпт бота направляє користувачів перевіряти кроки в додатку WHOOP.

### Життєвий цикл токена

- Access token діє **3600 секунд (1 година)**
- Refresh token довготривалий
- Оновлення через `POST /oauth/oauth2/token` з `grant_type=refresh_token`
- Потрібні лише `client_id` та `client_secret` (без `redirect_uri`)
- **Оновлення токена може повернути 400 Bad Request** якщо токен було відкликано. Обробка: очищення токенів + `TokenExpiredError`.

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

### Обробка помилок оновлення токенів

`refresh_token_if_needed()` в `whoop_sync.py`:
- Має параметр `force` для проактивного оновлення
- При 400/401/403 від token endpoint: очищує токени з БД, кидає `TokenExpiredError`
- Всі виклики WHOOP API мають логіку повторного запиту при 401 (force-refresh + retry)

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
- Призначення: пошук продуктів, деталі продуктів (публічна база)
- **Потребує IP whitelist** на `platform.fatsecret.com`

### OAuth 1.0 Three-Legged (дані користувача) - ПРАЦЮЄ

Для доступу до персонального щоденника харчування.

**Ендпоінти:**
- Request Token: `POST https://authentication.fatsecret.com/oauth/request_token`
- Авторизація: `GET https://authentication.fatsecret.com/oauth/authorize?oauth_token={token}`
- Access Token: `POST https://authentication.fatsecret.com/oauth/access_token`

**Підписання:** HMAC-SHA1 через `app/services/fatsecret_auth.py`

**Поведінка токенів:** OAuth 1.0 токени **постійні** — не закінчуються, якщо не відкликані. Немає механізму оновлення. Перевірка кожні 30 хв валідує токени через виклик API.

### FatSecret повертає HTTP 200 для помилок авторизації

**КРИТИЧНО:** FatSecret повертає `HTTP 200 OK` з `{"error": {"code": X, "message": "..."}}` в тілі відповіді для помилок авторизації — НЕ HTTP 401/403. Стандартний `httpx.HTTPStatusError` це не зловить.

**Рішення:** Кастомний `FatSecretAuthError` + `_FS_AUTH_ERROR_CODES = {2, 4, 8, 13, 14}` в `fatsecret_api.py`. Всі відповіді API перевіряються на error body.

### Пріоритет джерела калорій

Коли FatSecret підключений і працює, він є **джерелом істини** для з'їдених калорій (записи бота синхронізуються туди). Bot-logged калорії використовуються як fallback коли FatSecret недоступний. Див. `get_today_stats()` в `ai_assistant.py`.

---

## 4. Схема БД - Реальність vs Документація

### КРИТИЧНО: Існуюча БД використовує INTEGER, а не UUID

```sql
-- Фактична схема:
users.id          -> INTEGER (SERIAL), НЕ UUID
users.telegram_user_id -> BIGINT
```

Міграція `001_initial_schema.sql` має UUID-схему, але **ніколи не застосовувалась**. Міграція `002_health_tracker_schema.sql` працює з існуючою INTEGER-схемою.

### Таблиці в продакшні

`users`, `diary_entries`, `food_entries`, `mood_entries`, `whoop_activities`, `whoop_recovery`, `whoop_sleep`, `daily_summaries`, `sync_logs`, `conversation_messages`

---

## 5. GPT Context Engineering

### Уникайте складних розбивок для GPT

**Баг знайдено 2026-02-25:** Коли контекст GPT показував "total: 216 kcal (FatSecret: 216, bot: 40)", GPT додавав їх і отримував 256 замість 216.

**Виправлення:** Показувати тільки ОДНЕ число з явною інструкцією:
```
Today's calories eaten: {total} kcal.
IMPORTANT: Use ONLY these exact numbers when answering about calories.
Do NOT add or recalculate — these are already the correct totals.
```

### Структура системного промпту

`SYSTEM_PROMPT` в `ai_assistant.py` класифікує кожне повідомлення:
- `log_food` — витягує продукти з назвою, вагою, типом прийому їжі
- `query_data` — відповідає про дані здоров'я з контексту
- `delete_entry` — видаляє останній/конкретний запис їжі
- `general` — привітання, встановлення цілі калорій, допомога

Відповідь завжди JSON з полями `intent`, `food_items`, `calorie_goal`, `response`.

---

## 6. Типові помилки та виправлення

### Парсинг .env у Bash

`source <(grep ...)` та `export $(cat ... | xargs)` **падають** коли пароль містить спецсимволи. Використовуйте цикл `while IFS= read -r line` (див. `database/init-db.sh`).

### PostgreSQL DATE() на TIMESTAMPTZ НЕ є immutable

```sql
-- ПАДАЄ: DATE() залежить від timezone
CREATE INDEX idx ON food_entries(user_id, DATE(logged_at));

-- ПРАЦЮЄ: композитний індекс, фільтр у запитах
CREATE INDEX idx ON food_entries(user_id, logged_at);
```

### Патерни детекції протермінованих токенів

**WHOOP (OAuth 2.0):** Токен має відомий час закінчення. `refresh_token_if_needed()` перевіряє `whoop_token_expires_at`. При 401 від API: force-refresh + retry. При невдалому refresh (400/401/403): очищення токенів, `TokenExpiredError`.

**FatSecret (OAuth 1.0):** Токени постійні, але можуть бути відкликані. API повертає HTTP 200 з error body. Перевірка `_FS_AUTH_ERROR_CODES` у відповіді. При auth помилці: очищення токенів, `FatSecretAuthError`, сповіщення через Telegram.

### Сповіщення про протерміновані токени

`handle_message` та `handle_sync` в `telegram_bot.py` перевіряють `expired_services` з `get_today_stats()` і додають підказки:
```
🔑 Сесія закінчилась, потрібно перепідключити:
  ⌚ WHOOP → /connect_whoop
  🥗 FatSecret → /connect_fatsecret
```

---

## 7. Довідник файлів

| Файл | Призначення |
|------|-------------|
| `app/main.py` | Точка входу FastAPI, управління життєвим циклом |
| `app/config.py` | Налаштування зі змінних оточення |
| `app/database.py` | asyncpg пул з'єднань PostgreSQL |
| `app/scheduler.py` | Конфігурація періодичних задач APScheduler |
| `app/services/telegram_bot.py` | Всі обробники бота та повідомлення |
| `app/services/ai_assistant.py` | GPT інтеграція, статистика калорій, контекст розмови |
| `app/services/whoop_sync.py` | WHOOP OAuth 2.0, синхронізація, управління токенами |
| `app/services/fatsecret_api.py` | FatSecret API, синхронізація щоденника, перевірка токенів |
| `app/services/fatsecret_auth.py` | OAuth 1.0 HMAC-SHA1 підписання запитів |
| `app/services/briefings.py` | Ранкові/вечірні заплановані повідомлення |
| `app/routers/whoop.py` | `/whoop/callback` OAuth flow |
| `app/routers/fatsecret.py` | `/fatsecret/connect`, `/fatsecret/callback` |
| `app/routers/utils.py` | `/ip` health check |
| `database/init-db.sh` | Скрипт ініціалізації БД |
| `database/migrations/002_health_tracker_schema.sql` | Продакшн схема міграції |
| `.env` | Змінні оточення (БД, WHOOP, FatSecret, Telegram, OpenAI) |

---

## 8. TODO / Відомі проблеми

### БЭКЛОГ

- [ ] **Шифрування токенів** — WHOOP/FatSecret токени зберігаються як plain text в БД
- [ ] **BMR в calorie balance** — Додати формулу Mifflin-St Jeor для базового метаболізму
- [ ] **Виправити `docs/en/api-integration.md`** — прибрати `read:cycles`, додати FatSecret OAuth 1.0 vs 2.0
- [ ] **WHOOP кроки через API** — Моніторити WHOOP Developer API на появу ендпоінту кроків (недоступний станом на 2026-02-25)
- [ ] **Локальна база українських продуктів** — Fallback коли FatSecret не має українських продуктів

---

## 9. Історія міграції (2026-02-24)

Всі 7 n8n workflows мігровано в єдиний FastAPI Python додаток, workflows видалено з сервера.

### Маппінг Workflows на Python

| Колишній n8n Workflow | Python еквівалент |
|---|---|
| WHOOP Data Sync | `app/services/whoop_sync.py` (APScheduler щогодини) |
| WHOOP OAuth Callback | `app/routers/whoop.py` → `GET /whoop/callback` |
| FatSecret Food Search | `app/services/fatsecret_api.py` → `search_food()` |
| FatSecret OAuth Connect | `app/routers/fatsecret.py` → `GET /fatsecret/connect` |
| FatSecret OAuth Callback | `app/routers/fatsecret.py` → `GET /fatsecret/callback` |
| FatSecret Food Diary | `app/services/fatsecret_api.py` → `fetch_food_diary()` |
| IP Check | `app/routers/utils.py` → `GET /ip` |

### Розгортання

Docker образ: `health-tracker`, розгорнутий на Dokploy.

```bash
docker build -t health-tracker .
docker run --env-file .env -p 8000:8000 health-tracker
```

Production startup використовує команду Dockerfile як єдиний авторитетний шлях міграції для Apple Health. Контейнер запускає `python -m app.db_preflight --apply-apple-health-migration` перед Uvicorn, а FastAPI lifespan повторно перевіряє потрібні таблиці та індекси Apple Health до обслуговування трафіку.
