# 🏗 Архітектура

[🇬🇧 English version](../en/architecture.md)

## Огляд системи

Health & Wellness Tracker побудований на event-driven архітектурі з використанням n8n як центрального оркестратора.

```
┌─────────────────────────────────────────────────────────────────┐
│                      TELEGRAM WEB APP                            │
│                    (Frontend - React/Vue)                        │
└──────────────────────────┬──────────────────────────────────────┘
                           │ HTTPS
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                         TELEGRAM BOT                             │
│                    (Webhook Receiver)                            │
└──────────────────────────┬──────────────────────────────────────┘
                           │ Webhook
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                           n8n                                    │
│                 (Automation & Orchestration)                     │
│                                                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │  Telegram   │  │   Voice     │  │   Food      │              │
│  │  Trigger    │  │  Processing │  │   Search    │              │
│  └─────────────┘  └─────────────┘  └─────────────┘              │
│                                                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │   WHOOP     │  │   Daily     │  │   Data      │              │
│  │    Sync     │  │  Summary    │  │  Storage    │              │
│  └─────────────┘  └─────────────┘  └─────────────┘              │
└──────────────────────────┬──────────────────────────────────────┘
                           │
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
    ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
    │  FatSecret  │ │    WHOOP    │ │  PostgreSQL │
    │     API     │ │     API     │ │  Database   │
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

### 1. Telegram Web App

**Технології:**
- React 18 + TypeScript
- Tailwind CSS
- Telegram Web App SDK

**Відповідальність:**
- Відображення UI
- Взаємодія з користувачем
- Відправка команд боту
- Показ даних в реальному часі

### 2. n8n Workflows

**Головні workflows:**

| Workflow | Тригер | Призначення |
|----------|--------|-------------|
| Voice Processing | Telegram voice message | Обробка голосових повідомлень |
| Text Processing | Telegram text message | Обробка текстових повідомлень |
| WHOOP Sync | Schedule (15 min) | Синхронізація даних WHOOP |
| Daily Summary | Schedule (21:00) | Генерація денного звіту |
| Weekly Report | Schedule (Sunday) | Генерація тижневого звіту |

### 3. PostgreSQL Database

**Характеристики:**
- PostgreSQL 15+
- UUID для первинних ключів
- JSONB для гнучких даних
- Індекси для оптимізації запитів

**Основні таблиці:**
- `users` - профілі користувачів
- `food_entries` - записи про їжу
- `whoop_activities` - тренування
- `mood_entries` - записи настрою
- `daily_summaries` - денні підсумки

---

## Data Flow

### Логування їжі голосом

```
┌──────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│ User │───▶│ Telegram │───▶│   n8n    │───▶│ OpenAI   │
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
                           ┌──────────┐    ┌──────────┐
                           │ PostgreSQL│◀──│   n8n    │
                           │          │    │          │
                           └──────────┘    └──────────┘
                                               │
                                               ▼
                                          ┌──────────┐
                                          │ Telegram │
                                          │ Response │
                                          └──────────┘
```

### WHOOP Sync

```
┌──────────┐    ┌──────────┐    ┌──────────┐
│ Schedule │───▶│   n8n    │───▶│  WHOOP   │
│  Trigger │    │          │    │   API    │
└──────────┘    └──────────┘    └──────────┘
                     │               │
                     │◀──────────────┘
                     │         Data
                     ▼
               ┌──────────┐
               │ PostgreSQL│
               │  (upsert) │
               └──────────┘
                     │
                     ▼
               ┌──────────┐
               │ Telegram │
               │  (notify)│
               └──────────┘
```

---

## n8n Workflow Details

### Voice Processing Workflow

```
[Telegram Trigger]
        │
        ▼
[Download Audio File]
        │
        ▼
[OpenAI Whisper] ───▶ Transcription
        │
        ▼
[OpenAI GPT] ───▶ Extract foods + amounts
        │
        ▼
[Loop: For each food]
        │
        ▼
[FatSecret Search] ───▶ Get calories
        │
        ▼
[Merge Results]
        │
        ▼
[PostgreSQL Insert]
        │
        ▼
[Format Response]
        │
        ▼
[Telegram Send Message]
```

### WHOOP Sync Workflow

```
[Schedule Trigger]
        │
        ▼
[Get Users with WHOOP]
        │
        ▼
[Loop: For each user]
        │
        ├───▶ [Check Token Expiry]
        │           │
        │           ▼ (if expired)
        │     [Refresh Token]
        │
        ▼
[WHOOP API: Get Workouts]
        │
        ▼
[WHOOP API: Get Recovery]
        │
        ▼
[WHOOP API: Get Sleep]
        │
        ▼
[Transform Data]
        │
        ▼
[PostgreSQL Upsert]
        │
        ▼
[Update Sync Log]
```

---

## Security

### Автентифікація

- **Telegram:** Використовується `initData` для верифікації користувача
- **WHOOP:** OAuth 2.0 токени зберігаються зашифрованими
- **FatSecret:** Client credentials, не зберігаються токени користувачів

### Зберігання секретів

```
┌─────────────────┐
│   Environment   │
│   Variables     │
│  (.env файл)    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│      n8n        │
│  Credentials    │
│    Storage      │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   Encrypted     │
│   at Rest       │
└─────────────────┘
```

### GDPR Compliance

- Користувач може експортувати всі свої дані
- Користувач може видалити акаунт та всі дані
- Мінімальний збір даних
- Дані не передаються третім сторонам

---

## Deployment

### Docker Compose

```yaml
version: '3.8'

services:
  n8n:
    image: n8nio/n8n
    environment:
      - DB_TYPE=postgresdb
      - DB_POSTGRESDB_HOST=db
    ports:
      - "5678:5678"
    depends_on:
      - db

  db:
    image: postgres:15
    environment:
      - POSTGRES_DB=healthlog
      - POSTGRES_USER=user
      - POSTGRES_PASSWORD=pass
    volumes:
      - postgres_data:/var/lib/postgresql/data

  web:
    build: ./webapp
    ports:
      - "3000:3000"

volumes:
  postgres_data:
```

### Dokploy

Система може бути розгорнута через Dokploy:

1. Створіть новий проект
2. Додайте PostgreSQL сервіс
3. Додайте n8n як Docker application
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

- n8n execution logs
- PostgreSQL query logs
- API error tracking

---

## Масштабування

### Горизонтальне масштабування

```
                    ┌─────────────┐
                    │   Load      │
                    │  Balancer   │
                    └──────┬──────┘
                           │
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
    ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
    │    n8n      │ │    n8n      │ │    n8n      │
    │  Instance 1 │ │  Instance 2 │ │  Instance 3 │
    └─────────────┘ └─────────────┘ └─────────────┘
           │               │               │
           └───────────────┴───────────────┘
                           │
                           ▼
                    ┌─────────────┐
                    │  PostgreSQL │
                    │   Primary   │
                    └──────┬──────┘
                           │
                    ┌──────┴──────┐
                    ▼             ▼
             ┌──────────┐  ┌──────────┐
             │ Replica  │  │ Replica  │
             └──────────┘  └──────────┘
```

### Кешування

- Redis для сесій та кешу API
- CDN для статичних файлів Web App
