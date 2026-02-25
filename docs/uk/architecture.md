# 🏗 Архітектура

[🇬🇧 English version](../en/architecture.md)

## Огляд системи

Health & Wellness Tracker побудований як FastAPI Python-додаток, який виконує роль бекенду Telegram-бота та API-сервера.

```
┌─────────────────────────────────────────────────────────────────┐
│                      TELEGRAM BOT                                │
│               (python-telegram-bot v21)                          │
└──────────────────────────┬──────────────────────────────────────┘
                           │ Webhook / Polling
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
- `app/main.py` — Точка входу, управління життєвим циклом
- `app/config.py` — Налаштування зі змінних оточення
- `app/database.py` — Пул з'єднань PostgreSQL
- `app/scheduler.py` — Планування періодичних задач

### 2. Сервіси

| Сервіс | Файл | Призначення |
|--------|------|-------------|
| Telegram Bot | `app/services/telegram_bot.py` | Обробка повідомлень, команди |
| AI Assistant | `app/services/ai_assistant.py` | GPT класифікація + відповідь |
| WHOOP Sync | `app/services/whoop_sync.py` | OAuth 2.0, синхронізація, оновлення токенів |
| FatSecret API | `app/services/fatsecret_api.py` | OAuth 1.0, пошук їжі, синхронізація щоденника |
| FatSecret Auth | `app/services/fatsecret_auth.py` | OAuth 1.0 HMAC-SHA1 підписання |
| Briefings | `app/services/briefings.py` | Ранкові/вечірні повідомлення |

### 3. API Роутери

| Роутер | Шлях | Призначення |
|--------|------|-------------|
| WHOOP | `app/routers/whoop.py` | `/whoop/callback` OAuth flow |
| FatSecret | `app/routers/fatsecret.py` | `/fatsecret/connect`, `/fatsecret/callback` |
| Utils | `app/routers/utils.py` | `/ip` health check |

### 4. Заплановані задачі

| Задача | Частота | Призначення |
|--------|---------|-------------|
| WHOOP Data Sync | Кожну 1г | Синхронізація тренувань, сну, відновлення |
| WHOOP Token Refresh | Кожні 30хв | Проактивне оновлення токенів |
| FatSecret Data Sync | Кожну 1г | Синхронізація щоденника їжі |
| FatSecret Token Check | Кожні 30хв | Перевірка токенів, сповіщення при закінченні |
| Morning Briefing | 08:00 Київ | Ранковий огляд здоров'я |
| Evening Summary | 21:00 Київ | Вечірній звіт |
| Conversation Cleanup | 03:00 UTC | Очищення старої історії розмов |

### 5. PostgreSQL Database

**Характеристики:**
- PostgreSQL 15+
- INTEGER первинні ключі
- asyncpg для асинхронних операцій

**Основні таблиці:**
- `users` — профілі користувачів, OAuth токени
- `food_entries` — записи про їжу з калоріями/макросами
- `whoop_activities` — тренування з WHOOP
- `whoop_sleep` — дані сну
- `whoop_recovery` — показники відновлення
- `conversation_messages` — історія чату для контексту GPT

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

- **Telegram:** Bot token для верифікації webhook
- **WHOOP:** OAuth 2.0 токени з авто-оновленням
- **FatSecret:** OAuth 1.0 HMAC-SHA1 підписані запити

### Зберігання секретів

Всі секрети зберігаються у змінних оточення (`.env` файл).

### GDPR Compliance

- Користувач може експортувати всі свої дані
- Користувач може видалити акаунт та всі дані
- Мінімальний збір даних
- Дані не передаються третім сторонам

---

## Deployment

### Docker

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Dokploy

Система розгорнута через Dokploy:

1. Створіть новий проект
2. Додайте PostgreSQL сервіс
3. Додайте додаток як Docker application
4. Налаштуйте environment variables
5. Налаштуйте домен та SSL

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

- Python `logging` модуль (структуровані логи)
- PostgreSQL query logs
- API error tracking

---

## Масштабування

### Горизонтальне масштабування

Декілька FastAPI інстансів за load balancer з PostgreSQL primary/replica.

### Кешування

- Redis для сесій та кешу API
- CDN для статичних файлів Web App
