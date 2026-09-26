# База знань сесії - оновлено 2026-09-26

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
| Telegram Bot | `app/services/telegram_bot.py` | Бот на long polling; обробники фото/документів/кнопок для їжі. Команди: /start, /help, /sync, /timezone, /profile, /language, /connect_whoop, /connect_fatsecret, /connect_apple_health, /apple_health_help, /gym_prompt, /journal*, /app |
| AI Assistant | `app/services/ai_assistant.py` | GPT класифікація + відповідь, статистика калорій |
| WHOOP Sync | `app/services/whoop_sync.py` | OAuth 2.0, синхронізація даних, оновлення токенів |
| FatSecret API | `app/services/fatsecret_api.py` | OAuth 1.0, пошук їжі, синхронізація щоденника, перевірка токенів |
| FatSecret Auth | `app/services/fatsecret_auth.py` | OAuth 1.0 HMAC-SHA1 підписання |
| Apple Health | `app/services/apple_health.py`, `app/routers/apple_health.py` | Webhook для Shortcut/HAE, щоденні агрегати schema v3 за сімействами |
| Briefings | `app/services/briefings.py` | Ранкові (08:00) / вечірні (21:00) повідомлення за **локальним часом користувача**, нагадування щоденника |
| Scheduler | `app/scheduler.py` | APScheduler періодичні задачі |
| Security | `app/security.py` | Підписаний OAuth `state`, залежність `require_admin` |
| Time | `app/timeutils.py` | `resolve_timezone()` (users.timezone → DEFAULT_TIMEZONE), межі локального дня |
| Crypto | `app/crypto.py` | `hash_secret`/`verify_secret` для секрету Apple Health |
| i18n | `app/i18n.py` | Каталог повідомлень uk/en `t(key, lang)`, `normalize_language()` |
| Workouts | `app/services/health_workouts.py` | Валідація, upsert і денний підсумок тренувань Apple Health |
| BMR | `app/services/bmr.py` | Mifflin-St Jeor, парсинг `/profile`, пропорційний базальний витрат |
| Облік їжі | `app/services/food_*.py`, `catalog_import.py`, `barcode_reader.py`, `open_food_facts.py` | Журнал, каталог, резолвер, outbox, імпорт історії, фото штрихкодів/етикеток — див. [food-logging.md](food-logging.md) |
| Web App | `app/routers/webapp.py`, `app/routers/admin.py`, `app/services/webapp_auth.py` | Сесії з initData, `/api/v1/webapp/*`, `/api/v1/admin/*` |

### Заплановані задачі

| Задача | Частота | Призначення |
|--------|---------|-------------|
| WHOOP Token Refresh | Кожні 30хв | Оновлення токенів, що спливають протягом 10 хв |
| FatSecret Token Check | Кожні 3г | Перевірка токенів, сповіщення + очищення при відкликанні |
| Morning Briefing | Кожні 5 хв, надсилає в налаштований місцевий час (типово 08:00) раз на локальну дату (`notification_sends`) | Ранковий огляд здоров'я |
| Evening Summary | Кожні 5 хв, те саме правило (типово 21:00) | Вечірній звіт |
| Food outbox | Кожну 1хв | Створення/редагування/видалення у FatSecret з lease |
| Food reconcile | Кожні 10хв | Звірка `unknown` створень із віддаленим щоденником |
| Імпорт історії FatSecret | Кожні 5хв (+ щоденне оновлення 04:00 UTC) | Наповнення «Моїх продуктів», відновлюване |
| Очищення кешу їжі | Щогодини | Дані FatSecret ≤24 год, прострочені чернетки, кеш пошуку, сесії |
| Journal Reminders | Кожні 10хв | Час користувача (±5 хв, з переходом через північ), локальний |
| Conversation Cleanup | 03:00 UTC | Видалення історії розмов старшої за 7 днів |

Періодичного *читання* WHOOP/FatSecret **немає** (є лише запис через outbox
і імпорт історії): дані WHOOP і FatSecret
беруться наживо (WHOOP з кешем 120 с). Apple Health — лише push. Задачі
працюють з `coalesce=True, max_instances=1`.

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
https://api.prod.whoop.com/oauth/oauth2/auth?client_id={WHOOP_CLIENT_ID}&redirect_uri={WHOOP_REDIRECT_URI}&response_type=code&scope=offline%20read:workout%20read:recovery%20read:sleep%20read:body_measurement&state={SIGNED_STATE}
```

`state` **підписаний** (`app/security.py`, HMAC-SHA256, призначення `whoop`,
TTL 1 год). Голий Telegram id дозволяв account-linking CSRF. `offline` потрібен
для отримання refresh token.

> Значення `WHOOP_CLIENT_ID` та `WHOOP_REDIRECT_URI` знаходяться в `.env`.

### Отримання User ID без `read:profile`

Оскільки profile ендпоінт недоступний, user_id береться з першого запису
recovery → sleep → workouts (`limit=1`). Новий користувач без записів усе одно
підключається з `whoop_user_id = NULL` (раніше це падало з `IndexError`).

### Обробка помилок оновлення токенів

`refresh_token_if_needed()` в `whoop_sync.py`:
- Має параметр `force` для проактивного оновлення
- Працює під per-user `asyncio.Lock`; виклик, що чекав, перевикористовує щойно
  виданий токен, а не витрачає refresh token двічі
- При 400/401/403 від token endpoint: `clear_whoop_tokens()` + `TokenExpiredError`
- `get_whoop_context_for_user()` — єдина точка входу для live-даних (401 →
  force-refresh → один retry → очищення). Не дублюйте цю логіку.

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

**Поведінка токенів:** OAuth 1.0 токени **постійні** — не закінчуються, якщо не відкликані. Немає механізму оновлення. Перевірка кожні 3 години валідує токени; відкликання також виявляється на кожному повідомленні в чаті.

**Ніколи не логуйте відповіді з токенами** — тіла `request_token`/`access_token` містять секрети. OAuth 2.0 client-credentials токен кешується в пам'яті.

**Дата щоденника — локальна для користувача** (`fatsecret_today(tz)`), а не UTC.

### FatSecret повертає HTTP 200 для помилок авторизації

**КРИТИЧНО:** FatSecret повертає `HTTP 200 OK` з `{"error": {"code": X, "message": "..."}}` в тілі відповіді для помилок авторизації — НЕ HTTP 401/403. Стандартний `httpx.HTTPStatusError` це не зловить.

**Рішення:** `_raise_on_error_body()` в `fatsecret_api.py`: коди `{2, 4, 8, 13, 14}` → `FatSecretAuthError`, інші → `FatSecretAPIError`. Запис іде через `create_food_entry()` → `FoodEntryWriteResult` (`succeeded` вимагає підтвердженого `food_entry_id`; тайм-аут після відправки / 5xx / некоректний «успіх» → `unknown`, звіряється, повторно не надсилається). Кожен підтверджений запис спершу зберігається локально (журнал + outbox); `create_food_diary_entry()` — лише булева обгортка для сумісності.

### Пріоритет джерела калорій

З'їдені калорії = **локальний журнал ∪ живий щоденник FatSecret**, пов'язані за віддаленим id, тож синхронізований запис рахується один раз; лише-віддалені та лише-локальні записи додаються; неоднозначні/невідомі частини роблять підсумок `partial` (`food_logging.merge_daily`, використовується в `get_today_stats()`). Ніколи не додавайте «щойно записані» калорії до свіжого читання FatSecret (це рахувало двічі).

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

Пише застосунок: `users`, `food_entries` (журнал), таблиці їжі/каталогу
з 016, таблиці Web App/налаштувань з 017, `conversation_messages`,
`gym_exercises`, `journal_entries`, `apple_health_sync`,
`apple_health_import_logs`, `health_daily_metric_aggregates`,
`health_workouts`.

Лише читання (rollout Apple Health): `health_daily_aggregates` (v2),
`health_data` (сирі дані).

Видаляються міграцією `015_drop_unused_tables.sql` (лише порожні — таблиця з
рядками лишається, про неї пишеться NOTICE): `mood_entries`, `whoop_activities`,
`whoop_recovery`, `whoop_sleep`, `daily_summaries`, `sync_logs`,
`health_conflicts`, view `v_daily_calorie_balance`. `007` більше не створює
`health_conflicts`.

Міграції: `002` база → `003` колонки FatSecret → `004` розмови → `005` gym →
`006` щоденник → `007` конектор Apple Health → `009` агрегати v2 → `010`
агрегати v3 за сімействами → `011` розширені сімейства → `012` профіль
користувача (birth_year, sex, height_cm) → `013` `health_workouts` → `014` хеш
секретів Apple Health (незворотно) → `015` видалення невикористаних таблиць.
`008` навмисно відсутня. `016` журнал їжі/каталог/outbox/імпорт, `017` сесії Web App,
ролі, налаштування, цілі, прапори, аудит; `018` облік повторного використання initData +
індекси для запитів за користувачем. Docker preflight застосовує 007 і 009–018.

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

**WHOOP (OAuth 2.0):** Токен має відомий час закінчення. `refresh_token_if_needed()` перевіряє `whoop_token_expires_at`. При 401 від API: force-refresh + один retry (`get_whoop_context_for_user`). При невдалому refresh (400/401/403): очищення токенів, `TokenExpiredError`.

### Часові пояси

Ніколи не хардкодьте `Europe/Kyiv`. Використовуйте `resolve_timezone(users.timezone)`
з `app/timeutils.py`; для порожніх/невалідних значень він повертає
`DEFAULT_TIMEZONE`. Користувач змінює пояс командою `/timezone Europe/Warsaw`.

### Пастки обліку їжі

- **Грами дає користувач.** Промпт GPT повертає `quantity_g: null`, якщо вагу не
  назвали; бот перепитує. Голе число приймається лише як відповідь (reply) на
  повідомлення чернетки.
- **Не довіряйте «успіху» FatSecret без `food_entry_id`** і ніколи не повторюйте
  `unknown` створення наосліп — вирішує `food_sync.reconcile_unknown`.
- **Тригери `updated_at` перезаписують ручні значення** — не «зсувайте» `updated_at`
  для планування; outbox використовує `dispatched_at`/`next_attempt_at`.
- **asyncpg `AmbiguousParameterError`** («text versus character varying»), коли той
  самий `$n` порівнюється як text і записується у VARCHAR: приводьте тип (`$n::varchar`)
  або передавайте готове значення.
- **Дані FatSecret ≤ 24 год**: тривко зберігаються лише ID і власні назви
  користувача, решта — в колонках кешу, які очищає `purge_expired_provider_data`.
- **API Open Food Facts зафіксоване на 3.4**; не переходьте на 3.5+ без нових
  фікстур (змінилась структура харчової цінності).

### Службові endpoints

`/debug/*`, `/ip-check`, `/fatsecret/diary`, `/food/search` вимагають
`ADMIN_API_TOKEN` (`Authorization: Bearer …`); без налаштованого токена вони
повертають 404. Кожен вихідний HTTP-запит використовує `HTTP_TIMEOUT_SECONDS`.

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
| `app/services/apple_health.py` | Валідація, агрегація, збереження й читання Apple Health |
| `app/security.py` | Підписаний OAuth state, admin guard |
| `app/timeutils.py` | Хелпери часового поясу користувача |
| `app/db_preflight.py` | Застосовує/перевіряє міграції 007, 009–018 під advisory lock |
| `app/services/food_bot.py` | Потоки їжі в Telegram (текст/голос/фото/кнопки) без типів PTB |
| `app/routers/webapp.py`, `app/routers/admin.py` | JSON API Web App, API власника/адміна |
| `app/routers/whoop.py` | `/whoop/callback` OAuth flow |
| `app/routers/fatsecret.py` | `/fatsecret/connect`, `/fatsecret/callback`, admin `/fatsecret/diary`, `/food/search` |
| `app/routers/apple_health.py` | `/api/v1/health/apple-health/shortcut`, `/sync` |
| `app/routers/utils.py` | Admin `/ip-check`, `/debug/*` |
| `database/init-db.sh` | Скрипт ініціалізації БД |
| `database/migrations/002_health_tracker_schema.sql` | Продакшн схема міграції |
| `.env` | Змінні оточення (БД, WHOOP, FatSecret, Telegram, OpenAI) |

---

## 8. TODO / Відомі проблеми

### БЭКЛОГ

- [ ] **Перевірити розширений Shortcut на пристрої** — назви у picker Resting Heart Rate / Weight / Walking + Running Distance / Exercise Minutes і властивість семпла `Unit` (потрібен iPhone)
- [ ] **Тренування в підписаному Shortcut** — сервер і шлях HAE готові; дію Shortcuts "Find Workouts" треба додати й перевірити на пристрої
- [ ] **Константи сесій Web App → Settings** — `SESSION_MAX_LIFETIME` (12 год) і `INIT_DATA_MAX_USES` (5) захардкоджені в `app/services/webapp_auth.py`; перенести в `WEBAPP_SESSION_MAX_LIFETIME_SECONDS` / `WEBAPP_INIT_DATA_MAX_USES`, якщо розгортанню знадобляться інші значення
- [ ] **Lock оновлення WHOOP для кількох реплік** — per-user lock живе в процесі; перед запуском >1 репліки перенести в PostgreSQL advisory lock
- [x] ~~Хешування секрету Apple Health~~ — SHA-256 (2026-09-26). Шифрування OAuth-токенів було реалізовано й свідомо прибрано; токени лишаються у відкритому вигляді
- [x] ~~BMR в calorie balance~~ — `/profile` + Mifflin-St Jeor, пропорційний базальний витрат додається до активної енергії Apple Health (2026-09-26)
- [x] ~~Тренування з Apple Health~~ — `health_workouts`, native-масив `workouts` + HAE `data.workouts` (2026-09-26)
- [x] ~~Видалити таблиці, які ніколи не пишуться~~ — захищена міграція 015 (2026-09-26)
- [x] ~~i18n~~ — каталог uk/en, `/language`, мова з Telegram `language_code` (2026-09-26)
- [ ] **WHOOP кроки через API** — Моніторити WHOOP Developer API на появу ендпоінту кроків (недоступний станом на 2026-02-25)
- [ ] **Локальна база українських продуктів** — частково покрито штрихкодами Open Food Facts + фото етикеток → особисті продукти; покриття не виміряне
- [x] ~~**Фронтенд Web App** (`web/`, React + Vite), віддається за `/app/`~~ (2026-09-26)
- [ ] **Набір даних для оцінки їжі** (план §10) і живі перевірки FatSecret (план §11) перед увімкненням фото страв / штрихкоду FatSecret
- [x] ~~Облік їжі спершу з історії, фото штрихкодів і етикеток, outbox FatSecret, API Web App/адмінки~~ (2026-09-26)
- [x] ~~Виправити scopes у `docs/en/api-integration.md` / FatSecret OAuth 1.0 vs 2.0~~ (2026-09-26)
- [x] ~~Debug-ендпоінти без автентифікації, непідписаний OAuth state, відсутні HTTP timeouts, UTC-дата щоденника, загублені записи їжі~~ (2026-09-26)

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

Production startup використовує команду Dockerfile як єдиний авторитетний шлях
міграції для Apple Health. Контейнер запускає
`python -m app.db_preflight --apply-apple-health-migration` перед Uvicorn,
застосовує міграції `007`, `009`, `010` і `011`, а FastAPI lifespan повторно перевіряє
потрібні таблиці та індекси Apple Health до обслуговування трафіку. Preflight
тримає один PostgreSQL advisory lock протягом migration і verification, тому
паралельні старти replicas не змагаються за DDL. `database/init-db.sh` пропускає
всі файли `*_rollback.sql` і вмикає `ON_ERROR_STOP` для migration files.

### Операційні правила Apple Health schema v3

- Native Shortcut має надсилати collector `shortcut`, offset-aware
  `generatedAt`, timezone, дати й покриті сімейства метрик. Старі schema-v2
  Shortcuts потрібно імпортувати заново.
- Native payload не може видавати себе за collector `health_auto_export`.
  Coverage використовує рівно один формат, не більш як 31 дату на сімейство та
  вікно 30 днів у минуле / 1 день у майбутнє.
- Значення метрик і durations мають бути finite, не від'ємними там, де це
  потрібно, та обмеженими за magnitude до агрегації; DB constraints також
  відхиляють NaN.
- Сон рахується як об'єднання asleep-стадій. Awake та In Bed виключаються, крім
  fallback In Bed мінус Awake, коли інших придатних даних немає; покриття лише
  з Awake дорівнює нулю.
- Нові імпорти зберігають лише оброблені рядки за сімействами. Сирі тіла запитів
  не записуються в `health_data` і не пересилаються в Telegram.
- Спочатку запускай `python -m app.backfill_apple_health` без видалення.
  Використовуй `--delete-raw` лише після read-back і перевірки кількості рядків;
  будь-яка residual purge помилка завершує процес із ненульовим кодом.
  Destructive mode відмовляється від unsupported metrics і блокує concurrent
  writers до фінальної residual-перевірки.
- Під час rollout читачі спочатку timezone-фільтрують candidates, віддають
  перевагу найновішому in-window live collector для кожної дати/сімейства, а
  потім доповнюють пропуски зі schema v2, backfill і legacy raw джерел.
- HAE приймає одну підтримувану метрику на automation і лише кумулятивні
  periods Default/Today/Yesterday/Previous 7 Days з вимкненими Summarize Data
  та Batch Requests. Обов'язкові custom headers:
  `X-Health-Tracker-HAE-Mode: complete-unbatched-unaggregated-single-metric-v1`
  та `X-Health-Tracker-Timezone`, а також client-minted
  `X-Health-Tracker-Generated-At`, створений до dispatch. Стандартні прямі HAE
  REST automations не можуть надати causal marker і fail closed; timestamps від
  ingress proxy заборонені. Incremental, grouped, multi-metric та malformed
  snapshots також fail closed.
- Сімейства schema v3: `steps`, `active_energy`, `heart_rate`, `hrv`, `sleep`,
  та (міграція 011) `resting_heart_rate`, `body_mass` (кг, середнє),
  `distance` (м, сума), `exercise_time` (хв, сума). Сума чи середнє
  визначається `SUM_METRIC_FAMILIES` / `AVERAGE_METRIC_FAMILIES` в `apple_health.py`.
- Готовий Shortcut виконує 8 запитів. Вага й дистанція надсилають властивість
  семпла `Unit` (залежить від локалі); якщо Unit порожній, сервер бере суфікс
  одиниці з тексту Value.
- `token` Apple Health передається в URL: `SecretRedactingFilter`
  (`app/main.py`) маскує його в усіх логах, включно з access-логами uvicorn.
- Після зміни `docs/shortcuts/apple-health-sync.shortcut.plist` перепідпиши файл
  `shortcuts sign --mode anyone` (вхідний файл має закінчуватися на `.shortcut`)
  і запусти `python -m unittest tests.test_apple_health_shortcut_artifact`.
- Тренування пишуться в `health_workouts` (події, upsert за external id);
  приймаються і необов'язковий масив `workouts` у native payload, і HAE
  `data.workouts`, зокрема запити лише з тренуваннями. Підписаний Shortcut їх
  поки не надсилає.
- Секрет Apple Health зберігається хешованим (`sha256:`); бот показує URL лише
  один раз.

## 10. Облікові дані, мова та BMR (2026-09-26)

- **Токени в БД:** OAuth-токени WHOOP/FatSecret свідомо зберігаються у
  відкритому вигляді (без `TOKEN_ENCRYPTION_KEY`). Хешується лише секрет Apple Health.
- **Мова:** кожен фіксований текст для користувача йде через `t(key, lang)`
  (`app/i18n.py`); додавайте ключі в **обидва** `_UK` і `_EN` (тест перевіряє
  збіг ключів і плейсхолдерів). Нові користувачі отримують `users.language` з
  Telegram `language_code`; `/language uk|en` змінює її. GPT відповідає мовою,
  якою пише користувач.
- **BMR:** `/profile 1990 m 180` зберігає рік народження, стать і зріст. Вага
  береться з Apple Health (останнє зважування ≤30 днів) або з вимірів тіла
  WHOOP. Калорії циклу WHOOP вже містять базальний витрат і беруться як є;
  активна енергія Apple Health — ні, тому `calories_out = active + BMR × частка
  минулого локального дня` (`calories_burned_source = "apple_health_bmr"`).

