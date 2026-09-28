# 🚀 Початок роботи

[🇬🇧 English version](../en/getting-started.md)

## Вступ

Health & Wellness Tracker Bot допомагає відстежувати калорії, фізичну активність та настрій через зручний Telegram інтерфейс.

## Передумови

### Для користувачів
- Telegram акаунт
- (Опціонально) WHOOP пристрій для трекінгу активності

### Для розробників
- Python 3.12+
- PostgreSQL 15+
- API ключі (див. нижче)

## Встановлення

### 1. Клонування репозиторію

```bash
git clone https://github.com/your-username/health-tracker.git
cd health-tracker
```

### 2. Налаштування змінних оточення

Скопіюйте `.env.example` в `.env` та заповніть значення:

```bash
cp .env.example .env
```

```env
# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token

# FatSecret
FATSECRET_CLIENT_ID=your_client_id
FATSECRET_CLIENT_SECRET=your_client_secret

# WHOOP
WHOOP_CLIENT_ID=your_client_id
WHOOP_CLIENT_SECRET=your_client_secret

# OpenAI
OPENAI_API_KEY=your_api_key

# Database
DATABASE_URL=postgresql://user:pass@localhost:5432/healthlog

# Застосунок / безпека
APP_BASE_URL=https://your-domain.com
WHOOP_REDIRECT_URI=https://your-domain.com/whoop/callback
FATSECRET_SHARED_SECRET=your_oauth1_shared_secret
OAUTH_STATE_SECRET=long_random_string
ADMIN_API_TOKEN=long_random_string   # вмикає /debug/*; порожнє = 404
DEFAULT_TIMEZONE=Europe/Kyiv

# Облік їжі / Web App (див. docs/uk/food-logging.md)
OFF_USER_AGENT="HealthTrackerBot/1.0 (contact: you@example.com)"
FATSECRET_HISTORY_IMPORT_DAYS=30
WEBAPP_URL=https://your-domain.com/app/     # HTTPS, для кнопок Web App
WEBAPP_ADMIN_TELEGRAM_IDS=123456789         # початковий власник/адмін
# Опційно: WEBAPP_SESSION_TTL_SECONDS=3600 (неактивність), WEBAPP_AUTH_MAX_AGE_SECONDS=300
```

### 3. Налаштування бази даних

```bash
# Створення бази даних
createdb healthlog

# Запуск міграцій
bash database/init-db.sh   # застосовує 002+ по черзі (001 НЕ використовується), пропускає *_rollback.sql
# або лише частину Apple Health:
python -m app.db_preflight --apply-apple-health-migration
```

### 4. Запуск додатку

```bash
uvicorn app.main:app --reload
```

## Отримання API ключів

### Telegram Bot Token
1. Відкрийте [@BotFather](https://t.me/botfather) в Telegram
2. Створіть нового бота командою `/newbot`
3. Скопіюйте токен

### FatSecret API
1. Зареєструйтесь на [platform.fatsecret.com](https://platform.fatsecret.com/register)
2. Створіть новий додаток
3. Скопіюйте Client ID та Client Secret

### WHOOP API
1. Зареєструйтесь на [developer.whoop.com](https://developer.whoop.com)
2. Створіть новий додаток
3. Налаштуйте redirect URI
4. Скопіюйте Client ID та Client Secret

### OpenAI API
1. Увійдіть на [platform.openai.com](https://platform.openai.com)
2. Перейдіть в API Keys
3. Створіть новий ключ

## Перша взаємодія

### Логування їжі голосом

1. Надішліть текст або голосове: «гречка варена 180 г»
2. Закріплена фраза записується сама (з кнопкою **Скасувати**). Усе інше —
   два варіанти (історія, потім інший збіг FatSecret). Вибір стане першою кнопкою наступного разу.
3. Не вказали вагу? Бот питає її до варіантів — відповідайте «135 г» на його повідомлення
4. Фото штрихкоду з підписом «135 г» записує точний упакований продукт;
   для невідомого штрихкоду надішліть фото етикетки відповіддю
5. Після `/connect_fatsecret` історія щоденника FatSecret наповнює **Мої продукти**

### Підключення WHOOP

1. Натисніть кнопку "Connect WHOOP" в налаштуваннях
2. Авторизуйтесь в WHOOP
3. Надайте доступ до даних
4. Дані почнуть синхронізуватися автоматично

## Команди бота

| Команда | Опис |
|---------|------|
| `/start`, `/help` | Інструкція |
| `/connect_fatsecret`, `/connect_whoop`, `/connect_apple_health` | Підключення сервісів |
| `/sync` | Перевірити підключення |
| `/timezone`, `/language`, `/profile` | Часовий пояс, мова, профіль для BMR |
| `/journal`, `/journal_time`, `/journal_on`, `/journal_off` | Щоденник |
| `/gym_prompt` | Gym профіль |
| `/app` | Відкрити Telegram Web App (потрібна HTTPS `WEBAPP_URL`) |

Їжа записується звичайними повідомленнями, голосом і фото (команда не потрібна).

## Наступні кроки

- [Інтеграція API](api-integration.md) - Детальна документація API
- [Архітектура](architecture.md) - Як працює система
- [Облік їжі](food-logging.md) - Історія, штрихкоди, фото етикеток, API Web App
