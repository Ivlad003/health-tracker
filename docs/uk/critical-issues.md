# Критичні проблеми та ризики

> **Версія:** 1.0.0  
> **Оновлено:** 2026-01-28

Цей документ описує критичні проблеми, які потрібно вирішити перед запуском проекту, та готові рішення знайдені в open source.

---

## 🔴 Критичні проблеми

### 1. Безпека OAuth токенів

**Проблема:** Токени WHOOP зберігаються як plain text в базі даних.

**Ризик:** При компрометації БД — всі акаунти користувачів під загрозою.

**Рішення:**
- Використовувати `pgcrypto` для шифрування at-rest
- Або application-level AES-256-GCM
- Для production — HashiCorp Vault або AWS Secrets Manager

**Пріоритет:** Обов'язково до production

---

### 2. WHOOP API — Вимоги

**Проблема:** 
- Потрібен WHOOP пристрій та активна підписка
- Realtime heart rate недоступний через API
- Rate limit: 100 requests/minute

**Рішення:**
- Перевірити доступ до API перед розробкою
- Мати Plan Б: ручне введення тренувань або Apple Health

---

### 3. FatSecret — Українські продукти

**Проблема:** FatSecret не має офіційної підтримки українського регіону.

**Тести перед початком:**
- Пошук: "борщ", "вареники", "сирники", "голубці"
- Перевірити точність КБЖУ

**Рішення:**
- Локальна база українських продуктів як fallback
- Можливість додавати власні продукти
- Альтернатива: USDA FoodData Central (безкоштовно)

**Статус (2026-09-26):** власні особисті продукти (вручну / з фото етикетки), пошук за штрихкодом в Open Food Facts і «Мої продукти» з історії FatSecret користувача реалізовано ([food-logging.md](food-logging.md)); реальне покриття українських продуктів ще не виміряне.

---

### 4. Calorie Balance — Неповний розрахунок

**Проблема:** Не враховано BMR (базовий метаболізм ~1500-2000 ккал/день).

**Рішення:** Додати формулу Mifflin-St Jeor для розрахунку BMR.

---

### 5. Telegram Mini App — WebView обмеження

**Проблеми:**
- Local storage не надійний
- iOS keyboard баги
- Telegram може закрити app без попередження

**Рішення:**
- Використовувати Telegram CloudStorage API
- Тестувати на реальних пристроях
- Graceful degradation для анімацій

---

## 📦 Готові рішення

### WHOOP API

| Бібліотека | Мова | Примітки |
|------------|------|----------|
| [whoopy](https://pypi.org/project/whoopy/) | Python | OAuth 2.0, async, Pandas |
| [hedgertronic/whoop](https://github.com/hedgertronic/whoop) | Python | Простий клієнт |
| [kryoseu/whoops](https://github.com/kryoseu/whoops) | Flask | Export в PostgreSQL |

### FatSecret API

| Бібліотека | Мова | Примітки |
|------------|------|----------|
| [pyfatsecret](https://pypi.org/project/fatsecret/) | Python | OAuth 1.0, всі endpoints |
| [fatsecret](https://www.npmjs.com/package/fatsecret) | Node.js | Promise-based |

### Telegram Mini App

| Шаблон | Стек | Примітки |
|--------|------|----------|
| [reactjs-template](https://github.com/Telegram-Mini-Apps/reactjs-template) | React + Vite | Офіційний |
| [@telegram-apps/sdk-react](https://www.npmjs.com/package/@telegram-apps/sdk-react) | React | Готові hooks |

---

## 📋 План дій

| # | Задача | Час | Пріоритет |
|---|--------|-----|-----------|
| 1 | Перевірити WHOOP API доступ | 1 день | 🔴 P0 |
| 2 | Тест FatSecret з UA продуктами | 2 години | 🔴 P0 |
| 3 | Шифрування токенів | 1 день | 🔴 P0 |
| 4 | BMR в calorie balance | 4 години | 🟠 P1 |
| 5 | Error handling voice flow | 1 день | 🟠 P1 |
| 6 | Локальна база UA продуктів | 3-5 днів | 🟡 P2 |

---

## Посилання

- [WHOOP Developer Platform](https://developer.whoop.com/)
- [FatSecret Platform API](https://platform.fatsecret.com/)
- [Telegram Mini Apps Docs](https://core.telegram.org/bots/webapps)
- [USDA FoodData Central](https://fdc.nal.usda.gov/) — безкоштовна альтернатива
