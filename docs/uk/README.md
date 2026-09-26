# 📖 Документація

[🇬🇧 English version](../en/README.md)

Ласкаво просимо до документації Health & Wellness Tracker Bot!

## 📚 Зміст

- [Початок роботи](getting-started.md) - Як почати користуватися ботом
- [Інтеграція API](api-integration.md) - Технічна документація API
- [Архітектура](architecture.md) - Опис архітектури системи
- [⚠️ Критичні проблеми](critical-issues.md) - Ризики та рішення
- [🍽 Облік їжі](food-logging.md) - Пошук спершу в історії, штрихкоди, фото етикеток, outbox FatSecret, API Web App
- [Історія їжі, фото, штрихкоди та Web App](plans/2026-09-26-food-history-photo-barcode.md) - Дослідження, ручне керування, default-продукти й план адмінки

## 🎯 Швидкий старт

1. Знайдіть бота в Telegram: `@HealthTrackerBot`
2. Натисніть `/start` для початку
3. Підключіть WHOOP або Apple Health (опціонально)
4. Почніть логувати їжу голосовими повідомленнями!

## 🔗 Корисні посилання

- [Дизайн-специфікації](../design/)
- [GitHub Specs](../../.github/specs/)
- [База даних](../../database/migrations/)

## ⚠️ Важливо перед розробкою

Перед початком розробки обов'язково ознайомтесь з [критичними проблемами](critical-issues.md):
- Безпека OAuth токенів
- Вимоги WHOOP API (потрібен пристрій)
- Обмеження FatSecret для українських продуктів
- WebView обмеження Telegram Mini App
