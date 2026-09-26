# 🏃 Health & Wellness Tracker Bot

[🇬🇧 English version](README.md)

Telegram-бот для відстеження калорій, фізичної активності, сну та настрою з інтеграцією FatSecret, WHOOP та Apple Health.

## 📋 Опис

Система дозволяє:
- 🎤 Логувати їжу через голосові повідомлення
- 🍎 Автоматично визначати калорійність продуктів (FatSecret API), повторно
  використовуючи твої попередні вибори та історію щоденника FatSecret («Мої продукти»)
- 📷 Записувати упаковані продукти з фото штрихкоду (Open Food Facts) або фото
  етикетки з харчовою цінністю, з вагою, яку ти надсилаєш («135 г»)
- ↩️ Скасовувати/виправляти записи; кожен запис зберігається локально й
  синхронізується з FatSecret через надійний outbox (без подвійного підрахунку)
- 💪 Читати live-дані WHOOP (сон, відновлення, strain, тренування)
- ❤️ Імпортувати Apple Health через готовий iOS Shortcut: кроки, активні
  калорії, сон, HRV, пульс у спокої, дистанцію ходьби/бігу, хвилини тренувань
  і вагу; тренування через Health Auto Export
- 🧬 BMR (Mifflin-St Jeor) з `/profile` + останньої ваги
- 🌐 Інтерфейс українською та англійською (`/language`)
- 📊 Отримувати щоденні звіти про калорійний баланс о 08:00 / 21:00 у своєму часовому поясі
- 😊 Вести щоденник настрою та самопочуття

## 🏗 Архітектура

```
Telegram Bot (long polling) → FastAPI (Python) → APIs (FatSecret, WHOOP, OpenAI) → PostgreSQL
iPhone Shortcut ──POST──────▶ /api/v1/health/apple-health/sync ─────────────▶ PostgreSQL
Telegram Web App ─────────────▶ /api/v1/webapp/*, /api/v1/admin/* (React Mini App на /app/, див. docs/uk/webapp.md)
```

## 📁 Структура проекту

```
health-tracker/
├── app/                  # FastAPI Python додаток
│   ├── routers/          # API route handlers
│   ├── services/         # Бізнес-логіка (WHOOP, FatSecret, Apple Health, AI, бот)
│   ├── security.py       # Підписаний OAuth state, admin guard
│   ├── timeutils.py      # Хелпери часового поясу користувача
│   ├── db_preflight.py   # Міграції Apple Health + перевірка схеми
│   ├── main.py           # Точка входу
│   └── scheduler.py      # Періодичні задачі
├── docs/shortcuts/       # Apple Health Shortcut (підписаний + редагований plist)
├── tests/                # pytest (+ тести на реальному PostgreSQL)
├── .github/specs/        # GitHub Spec Kit специфікації
├── docs/                 # Двомовна документація (uk/en)
├── database/migrations/  # SQL міграції
├── CLAUDE.md             # Інструкції для AI асистента
├── README.md             # Англійська версія
└── README.uk.md          # Цей файл
```

## 🚀 Швидкий старт

### Передумови

- Python 3.12+
- PostgreSQL 15+
- API ключі: Telegram, FatSecret, WHOOP, OpenAI

### Налаштування

1. Клонувати репозиторій
2. Скопіювати `.env.example` у `.env` і заповнити credentials. Задати
   `OAUTH_STATE_SECRET` і (для `/debug/*`) `ADMIN_API_TOKEN`.
3. Запустити міграції бази даних: `bash database/init-db.sh`
4. Запустити додаток: `uvicorn app.main:app`

### Тести

```bash
pytest
# Тести Apple Health на реальному PostgreSQL (вони роблять DROP схеми public!):
APPLE_HEALTH_TEST_DATABASE_URL=postgresql://postgres:test@localhost:55432/ahtest pytest tests/test_apple_health_db.py
# Тести журналу їжі / каталогу / Web App на реальному PostgreSQL (теж DROP схеми):
FOOD_TEST_DATABASE_URL=postgresql://postgres:test@localhost:55432/ahtest pytest tests/test_food_ledger_db.py
```

### Production preflight бази даних

Production-деплой на Dokploy використовує `CMD` з Dockerfile як єдиний шлях
міграції. Перед стартом Uvicorn контейнер виконує:

```bash
python -m app.db_preflight --apply-apple-health-migration
```

Команда застосовує міграції `007` і `009`–`018` (агрегати Apple Health, профіль
користувача, тренування, хешування секрету, захищене видалення невикористаних
таблиць, журнал їжі/каталог/outbox, сесії/ролі/налаштування Web App) під PostgreSQL advisory lock і перевіряє потрібні таблиці, індекси та
constraints. FastAPI повторює перевірку під час
старту й не обслуговує трафік, якщо чогось бракує.

## 📖 Документація

- [Початок роботи](docs/uk/getting-started.md)
- [Інтеграція API](docs/uk/api-integration.md)
- [Архітектура](docs/uk/architecture.md)
- [Облік їжі](docs/uk/food-logging.md)
- [Дизайн-специфікації](docs/design/)

## 🔗 Зовнішні API

| API | Призначення | Документація |
|-----|-------------|--------------|
| FatSecret | Калорійність продуктів | [Docs](https://platform.fatsecret.com/docs) |
| WHOOP | Сон, відновлення, strain, тренування | [Docs](https://developer.whoop.com/docs) |
| Apple Health | Webhook для iOS Shortcut (без серверного API) | [Інтеграція API](docs/uk/api-integration.md#apple-health-sync) |
| Open Food Facts | Упаковані продукти за штрихкодом (API v3.4) | [Docs](https://openfoodfacts.github.io/openfoodfacts-server/api/) |
| OpenAI Whisper / vision | Speech-to-Text, розпізнавання етикеток/упаковок | [Docs](https://platform.openai.com/docs) |
| Telegram Bot API | Користувацький інтерфейс | [Docs](https://core.telegram.org/bots/api) |

## 📄 Ліцензія

MIT

---

*Створено: Січень 2026*
