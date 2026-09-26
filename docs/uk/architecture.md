# 🏗 Архітектура

[🇬🇧 English version](../en/architecture.md)

## Огляд системи

Health & Wellness Tracker побудований як FastAPI Python-додаток, який виконує роль бекенду Telegram-бота (long polling), API-сервера для OAuth callbacks і webhook Apple Health та JSON-бекенду Telegram Web App (`/api/v1/webapp/*`, `/api/v1/admin/*`). Інтерфейс Telegram Web App — це Vite + React у `web/`: збірка лежить у `web/dist` і віддається за адресою `/app/`. Облік їжі детально описано в [food-logging.md](food-logging.md).

```
┌─────────────────────────────────────────────────────────────────┐
│                      TELEGRAM BOT                                │
│               (python-telegram-bot v21)                          │
└──────────────────────────┬──────────────────────────────────────┘
                           │ Long polling
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                      FastAPI App                                 │
│                                                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │  Telegram   │  │   AI        │  │   Food      │              │
│  │  Bot Handler│  │  Assistant  │  │   Logging   │              │
│  └─────────────┘  └─────────────┘  └─────────────┘              │
│                                                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │   WHOOP     │  │  FatSecret  │  │  Scheduler  │              │
│  │    Sync     │  │    Sync     │  │  (APSched)  │              │
│  └─────────────┘  └─────────────┘  └─────────────┘              │
└──────────────────────────┬──────────────────────────────────────┘
                           │
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
    ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
    │  FatSecret  │ │    WHOOP    │ │  PostgreSQL │
    │     API     │ │   API v2   │ │  Database   │
    └─────────────┘ └─────────────┘ └─────────────┘
           │               │               │
           └───────────────┴───────────────┘
                           │
                           ▼
                    ┌─────────────┐
                    │   OpenAI    │
                    │   (Whisper  │
                    │    + GPT)   │
                    └─────────────┘
```

---

## Компоненти

### 1. FastAPI додаток

**Технології:**
- Python 3.12+, FastAPI, uvicorn
- asyncpg (PostgreSQL async драйвер)
- python-telegram-bot v21
- APScheduler (періодичні задачі)

**Модулі:**
- `app/main.py` — Точка входу, управління життєвим циклом, заглушка `/app/` або `web/dist`
- `app/config.py` — Налаштування зі змінних оточення
- `app/database.py` — Пул з'єднань PostgreSQL
- `app/scheduler.py` — Планування періодичних задач
- `app/security.py` — Підписаний OAuth `state`, залежність `require_admin`
- `app/timeutils.py` — Визначення часового поясу користувача
- `app/db_preflight.py` — Міграції 007, 009–018 + перевірка схеми
- `app/crypto.py` — хешування секрету webhook Apple Health
- `app/i18n.py` — Каталог повідомлень uk/en

### 2. Сервіси

| Сервіс | Файл | Призначення |
|--------|------|-------------|
| Telegram Bot | `app/services/telegram_bot.py` | Обробка повідомлень, команди |
| AI Assistant | `app/services/ai_assistant.py` | GPT класифікація + відповідь |
| WHOOP Sync | `app/services/whoop_sync.py` | OAuth 2.0, live-контекст (кеш 120 с), оновлення токенів під lock |
| FatSecret API | `app/services/fatsecret_api.py` | OAuth 2.0 пошук (кешований токен), OAuth 1.0 щоденник |
| FatSecret Auth | `app/services/fatsecret_auth.py` | OAuth 1.0 HMAC-SHA1 підписання |
| Apple Health | `app/services/apple_health.py` | Валідація schema v3, агрегація за сімействами, читання |
| Briefings | `app/services/briefings.py` | Ранкові/вечірні повідомлення за місцевим часом, нагадування щоденника |
| Gym / Journal | `app/services/gym_service.py`, `journal_service.py` | Журнал вправ, записи щоденника |
| Workouts | `app/services/health_workouts.py` | Тренування з Apple Health (події) |
| BMR | `app/services/bmr.py` | Mifflin-St Jeor з `/profile` + останньої ваги |
| Food nutrition | `app/services/food_nutrition.py` | Розрахунок порцій на Decimal, одиниці, розбір грамів |
| Food catalog / resolver | `app/services/food_catalog.py`, `food_resolver.py` | Продукти, «Мої продукти», правила за замовчуванням, пошук спершу в історії |
| Food ledger / sync | `app/services/food_logging.py`, `food_sync.py` | Чернетки, ідемпотентні коміти, денне об'єднання, outbox FatSecret + звірка |
| History import | `app/services/catalog_import.py` | Щоденник FatSecret → «Мої продукти» (відновлюваний) |
| Barcode / OFF / vision | `app/services/barcode_reader.py`, `open_food_facts.py`, `food_vision.py` | Локальне декодування, Open Food Facts v3.4, розпізнавання етикеток/страв |
| Food bot flows | `app/services/food_bot.py` | Оркестрація тексту/голосу/фото/кнопок |
| Web App auth / prefs / flags | `app/services/webapp_auth.py`, `preferences.py`, `feature_flags.py` | Сесії з initData, ролі, типізовані налаштування, прапори |

### 3. API Роутери

| Роутер | Шлях | Призначення |
|--------|------|-------------|
| WHOOP | `app/routers/whoop.py` | `/whoop/callback` OAuth flow (підписаний state) |
| FatSecret | `app/routers/fatsecret.py` | `/fatsecret/connect`, `/fatsecret/callback` (підписаний state); admin `/fatsecret/diary`, `/food/search` |
| Apple Health | `app/routers/apple_health.py` | `/api/v1/health/apple-health/shortcut`, `/api/v1/health/apple-health/sync` |
| Utils | `app/routers/utils.py` | Лише для адміна: `/ip-check`, `/debug/*` |
| Web App | `app/routers/webapp.py` | `/api/v1/webapp/*` (сесія з Telegram initData) |
| Admin | `app/routers/admin.py` | `/api/v1/admin/*` (серверна роль `admin`) |

### 4. Заплановані задачі

| Задача | Частота | Призначення |
|--------|---------|-------------|
| WHOOP Token Refresh | Кожні 30хв | Оновлення токенів, що спливають протягом 10 хв |
| FatSecret Token Check | Кожні 3г | Перевірка токенів, сповіщення при відкликанні |
| Morning Briefing | Кожні 5 хв → налаштований місцевий час (типово 08:00), раз на локальну дату | Ранковий огляд здоров'я |
| Evening Summary | Кожні 5 хв → налаштований місцевий час (типово 21:00), раз на локальну дату | Вечірній звіт |
| Food outbox / reconcile | 1 хв / 10 хв | Запис у щоденник FatSecret; звірка неоднозначних записів |
| Імпорт / оновлення історії FatSecret | 5 хв / 04:00 UTC | Наповнення «Моїх продуктів» |
| Очищення кешів їжі | Щогодини | Кеш FatSecret ≤24 год, прострочені чернетки, сесії |
| Journal Reminders | Кожні 10хв | Час, налаштований користувачем, за місцевим часом |
| Conversation Cleanup | 03:00 UTC | Очищення старої історії розмов |

Дані WHOOP і FatSecret беруться наживо; Apple Health надсилає iPhone.

### 5. PostgreSQL Database

**Характеристики:**
- PostgreSQL 15+
- INTEGER первинні ключі
- asyncpg для асинхронних операцій

**Основні таблиці (пише застосунок):**
- `users` — профілі користувачів, OAuth токени, `timezone`
- `food_entries` — локальний журнал їжі (кожен підтверджений запис; віддалені id + стан синхронізації)
- `food_products`, `food_nutrition_versions`, `user_product_memberships`, `food_default_rules`,
  `food_log_drafts`, `food_sync_outbox`, `catalog_import_jobs`/`_candidates`,
  `external_lookup_cache` (міграція 016)
- `user_preferences`, `user_goal_history`, `webapp_sessions`, `user_roles`, `feature_flags`,
  `admin_audit_log`, `notification_sends` (міграція 017); `webapp_sessions.init_data_hash`
  та індекси для запитів за користувачем (міграція 018)
- `conversation_messages` — історія чату для контексту GPT
- `gym_exercises`, `journal_entries`
- `apple_health_sync`, `apple_health_import_logs`
- `health_daily_metric_aggregates` — денні значення Apple Health за сімействами
  (steps, active_energy, heart_rate, hrv, sleep, resting_heart_rate,
  body_mass, distance, exercise_time)
- `health_workouts` — тренування Apple Health (рядок на тренування)

Таблиці з 002/007, які ніколи не заповнювались (`whoop_*`, `daily_summaries`,
`sync_logs`, `mood_entries`, `health_conflicts`), видаляються міграцією 015,
якщо порожні.

---

## Data Flow

### Логування їжі голосом

```
┌──────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│ User │───▶│ Telegram │───▶│ FastAPI  │───▶│ OpenAI   │
│      │    │   Bot    │    │          │    │ Whisper  │
└──────┘    └──────────┘    └──────────┘    └──────────┘
                                 │               │
                                 │◀──────────────┘
                                 │         Text
                                 ▼
                           ┌──────────┐    ┌──────────┐
                           │  OpenAI  │───▶│ FatSecret│
                           │   GPT    │    │   API    │
                           └──────────┘    └──────────┘
                                 │               │
                                 │◀──────────────┘
                                 │       Calories
                                 ▼
                           ┌──────────┐
                           │ PostgreSQL│
                           └──────────┘
                                 │
                                 ▼
                            ┌──────────┐
                            │ Telegram │
                            │ Response │
                            └──────────┘
```

---

## Безпека

### Автентифікація

- **Telegram:** ідентичність — `update.effective_user.id` з long polling
- **Прив'язка WHOOP / FatSecret:** HMAC-підписаний OAuth `state` з
  призначенням і терміном 1 година (`app/security.py`) — захист від
  account-linking CSRF
- **WHOOP:** OAuth 2.0 токени з авто-оновленням під lock
- **FatSecret:** OAuth 1.0 HMAC-SHA1 підписані запити
- **Webhook Apple Health:** випадковий персональний токен, порівняння за
  сталий час, ротація через `/connect_apple_health`
- **Службові endpoints:** bearer `ADMIN_API_TOKEN`; приховані (404), якщо не задано
- **Telegram Web App:** `initData` перевіряється HMAC з токеном бота (не старше 5 хв),
  одна жива сесія на initData (повторне використання відкликає попередню, з лімітом),
  відкликувані ковзні серверні сесії (1 год неактивності, максимум 12 год; хеші), Bearer (типово)
  або лише cookie + CSRF + Origin, роль адміна визначається з `user_roles` разом із сесією на
  кожному запиті; заголовки CSP / nosniff / no-store (`app/main.py`)
- **Логи:** `SecretRedactingFilter` маскує значення `token`, `code`, `state`,
  `oauth_token`, `oauth_verifier` у query (включно з access-логами uvicorn)

### Зберігання секретів

Всі секрети зберігаються у змінних оточення (`.env` файл). У PostgreSQL
OAuth-токени WHOOP/FatSecret зберігаються у відкритому вигляді, а секрет
webhook Apple Health — лише як SHA-256 хеш.

### Локалізація

Фіксовані тексти бота беруться з `app/i18n.py` (українська й англійська). Мова —
`users.language`, ініціалізується з Telegram `language_code` і змінюється
командою `/language`. Меню команд бота реєструється для `uk` і як типове (en).

### GDPR Compliance

- Користувач може експортувати всі свої дані
- Користувач може видалити акаунт та всі дані
- Мінімальний збір даних
- Дані не передаються третім сторонам

---

## Deployment

### Docker

Див. `Dockerfile` у репозиторії: він встановлює залежності, копіює `app/`,
`database/` і `docs/shortcuts/`, а його `CMD` запускає preflight бази перед
`newrelic-admin run-program uvicorn app.main:app`.

### Dokploy

Система розгорнута через Dokploy:

1. Створіть новий проект
2. Додайте PostgreSQL сервіс
3. Додайте додаток як Docker application
4. Налаштуйте environment variables
5. Налаштуйте домен та SSL

Docker-образ копіює `database/` у контейнер і використовує один production-шлях міграції:

```bash
python -m app.db_preflight --apply-apple-health-migration
```

Ця prestart-команда застосовує міграції `007` і `009`–`015`, а
потім перевіряє `apple_health_sync`, `health_data`, `apple_health_import_logs`,
`health_daily_aggregates`, `health_daily_metric_aggregates` та потрібні індекси
до старту Uvicorn. FastAPI lifespan повторює перевірку; якщо схема неповна,
застосунок завершує старт із санітизованою помилкою до обслуговування трафіку.
PostgreSQL session advisory lock серіалізує всю послідовність apply-and-verify
між replicas. Загальний runner `database/init-db.sh` застосовує лише forward
`*.sql`, пропускає `*_rollback.sql` і змушує `psql` зупинятися на першій помилці.

Apple Health schema v3 записує оброблені денні значення на межі
`collector + metric_date + metric_family`. Одна транзакція разом фіксує всі
рядки сімейств, лічильники sync і санітизований import log. Під час rollout
expand/migrate/contract читачі фільтрують рядки за timezone-adjusted query window,
окремо для кожного сімейства вибирають найновіший in-window live collector і
доповнюють лише відсутні значення зі schema-v2 aggregates, backfill-рядків та
legacy raw data.
Ingress приймає лише native collector `shortcut` і конвертований
`health_auto_export`, обмежує кожне сімейство 31 нещодавньою покритою датою та
відхиляє non-finite/out-of-domain значення. Receipt time та mutable timestamps
семплів HealthKit не визначають порядок HAE. Конвертований HAE-shaped request
має містити client-minted offset-aware export timestamp, створений до network
dispatch, а також один complete, unbatched, unaggregated metric, attested period
і явний timezone. Стандартні прямі HAE REST automations fail closed, бо не
надають такого causal marker.
Destructive backfill тримає writer-blocking table lock до commit фінальної
перевірки residual raw rows.

---

## Моніторинг

### Метрики

| Метрика | Опис | Поріг |
|---------|------|-------|
| API Response Time | Час відповіді | < 5 сек |
| Sync Success Rate | % успішних синхронізацій | > 99% |
| Error Rate | % помилок | < 1% |
| Active Users | DAU/MAU | - |

### Логування

- Python `logging` модуль (JSON-логи в stdout, опційно New Relic Log API)
- Секрети маскуються `SecretRedactingFilter`
- PostgreSQL query logs
- API error tracking

---

## Масштабування

### Горизонтальне масштабування

Декілька FastAPI інстансів за load balancer з PostgreSQL primary/replica.

### Кешування

- Кеші в процесі: live-контекст WHOOP (120 с), OAuth 2.0 токен FatSecret
- Per-user lock оновлення WHOOP живе в процесі; для кількох реплік
  перенесіть його в PostgreSQL advisory lock або Redis
