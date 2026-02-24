# 🏃 Health & Wellness Tracker Bot

[🇬🇧 English version](README.md)

Telegram Web App для відстеження калорій, фізичної активності та настрою з інтеграцією FatSecret і WHOOP API.

## 📋 Опис

Система дозволяє:
- 🎤 Логувати їжу через голосові повідомлення
- 🍎 Автоматично визначати калорійність продуктів (FatSecret API)
- 💪 Синхронізувати дані тренувань з WHOOP
- 📊 Отримувати щоденні звіти про калорійний баланс
- 😊 Вести щоденник настрою та самопочуття

## 🏗 Архітектура

```
Telegram Web App → n8n Workflows → APIs (FatSecret, WHOOP, OpenAI) → PostgreSQL
```

## 📁 Структура проекту

```
health-tracker/
├── .github/specs/        # GitHub Spec Kit специфікації
├── docs/
│   ├── uk/               # 🇺🇦 Українська документація
│   ├── en/               # 🇬🇧 Англійська документація
│   └── design/pages/     # Дизайн-специфікації
├── database/migrations/  # SQL міграції
├── n8n/workflows/        # n8n workflow файли
├── CLAUDE.md             # Інструкції для AI асистента
├── README.md             # Англійська версія
└── README.uk.md          # Цей файл
```

## 🚀 Швидкий старт

### Передумови

- n8n (self-hosted або cloud)
- PostgreSQL 15+
- API ключі: Telegram, FatSecret, WHOOP, OpenAI

### Налаштування

1. Клонувати репозиторій
2. Створити `.env` файл з credentials
3. Імпортувати n8n workflows
4. Запустити міграції бази даних

## 📖 Документація

- [Початок роботи](docs/uk/getting-started.md)
- [Інтеграція API](docs/uk/api-integration.md)
- [Архітектура](docs/uk/architecture.md)
- [Дизайн-специфікації](docs/design/)

## 🔗 Зовнішні API

| API | Призначення | Документація |
|-----|-------------|--------------|
| FatSecret | Калорійність продуктів | [Docs](https://platform.fatsecret.com/docs) |
| WHOOP | Дані тренувань | [Docs](https://developer.whoop.com/docs) |
| OpenAI Whisper | Speech-to-Text | [Docs](https://platform.openai.com/docs) |
| Telegram Bot API | Користувацький інтерфейс | [Docs](https://core.telegram.org/bots/api) |

## 📄 Ліцензія

MIT

---

*Створено: Січень 2026*
