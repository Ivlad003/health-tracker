# 🍽 Облік їжі: історія, штрихкоди, фото етикеток, API Web App

[🇬🇧 English version](../en/food-logging.md)

Реалізація [плану від 2026-09-26](plans/2026-09-26-food-history-photo-barcode.md) (P1 бекенд: фази 1–4 + API Web App/адмінки). React-фронтенд (`web/`) **ще не зроблено**; `/app/` показує сторінку «Відкрийте в Telegram», доки в `web/dist` немає збірки.

## 1. Що отримує користувач

| Вхід | Поведінка |
|---|---|
| «гречка варена 180 г» (текст/голос) | Спершу історія: закріплений продукт за замовчуванням → раніше підтверджений вибір → «Мої продукти» → пошук FatSecret. Єдиний однозначний підтверджений збіг із явною вагою записується одразу з кнопкою **Скасувати**; інакше показуються до 3 варіантів кнопками. |
| Без грамів | Бот питає; відповідь «135 г» або просто «135» **відповіддю (reply)** на це повідомлення (число поза reply ніколи не створює запис). Кнопки-пресети грамів беруться з налаштувань. |
| Фото штрихкоду (+ підпис «135 г») | Локальне декодування (zxing-cpp) → перевірка GTIN → власний/спільний продукт → Open Food Facts → опційно платний штрихкод FatSecret. Внутрішні коди магазину / вагові товари відхиляються. |
| Невідомий штрихкод | Бот просить фото етикетки з харчовою цінністю (відповіддю або в тому ж альбомі). Показує розпізнані значення; **Зберегти продукт і записати** створює особистий продукт, прив'язаний до штрихкоду. |
| Фото упаковки | Назва/бренд → варіанти резолвера (ніколи не записується автоматично). |
| Фото страви | Під прапором `food_plate_photos` (типово вимкнено). Кілька компонентів з однією загальною вагою → бот просить вагу кожного. |
| «видали останнє» | Анулює останній запис (ревізія) і ставить у чергу видалення у FatSecret, якщо запис синхронізовано. |

Відповіді показують джерело (FatSecret / Open Food Facts / етикетка / вручну), стан синхронізації з FatSecret і денний підсумок із позначкою *(неповні дані)*, якщо щось невідомо або неоднозначно.

## 2. Архітектура

| Модуль | Відповідальність |
|---|---|
| `app/services/food_nutrition.py` | Арифметика на `Decimal`, одиниці (g/kg/mg/oz/lb; мл ніколи не стають грамами), kJ→kcal (4.184), перевірка ваги, розбір кількості з тексту, порція FatSecret → база/одиниці |
| `app/services/barcode_reader.py` | EAN-13/EAN-8/UPC-A/UPC-E, контрольні цифри, розгортання UPC-E, GTIN-13 для FatSecret, нормалізація OFF, заборонені префікси, декодування зображення в окремому потоці |
| `app/services/food_catalog.py` | Продукти (спільні ідентичності провайдерів + особисті), ревізії харчової цінності, членство в «Моїх продуктах», виключення/архів, правила за замовчуванням, вивчені аліаси, списки, очищення кешу провайдера |
| `app/services/food_resolver.py` | Сумісність (штрихкод/бренд/жирність/сире-готове), рівні ранжування, рішення про автовибір, кандидати лише цього користувача, пошук FatSecret як останній варіант |
| `app/services/food_logging.py` | `UserContext`, чернетки (оптимістичні версії), `prepare_item`, атомарний коміт (запис + outbox + членство + аліас), ревізії анулювання/редагування, `merge_daily`/`daily_view` |
| `app/services/food_sync.py` | Воркер outbox (SKIP LOCKED + lease), типізовані результати, звірка `unknown` створень |
| `app/services/catalog_import.py` | Відновлювані задачі «щоденник FatSecret → Мої продукти», вибірковий режим, збагачення (улюблені/найчастіші/нещодавні), щоденне оновлення |
| `app/services/open_food_facts.py` | Адаптер із зафіксованим API v3.4, лімітер, User-Agent, кеш знайдених/ненайдених |
| `app/services/food_vision.py` | Межі та нормалізація зображення (EXIF видаляється), витяг через OpenAI vision, сувора валідація |
| `app/services/food_bot.py` | Оркестрація без залежності від PTB, повертає `BotReply` (текст + inline-кнопки + посилання на Web App) |
| `app/services/preferences.py` | Типізовані версійовані налаштування, цілі з датою набрання чинності |
| `app/services/feature_flags.py` | Прапори з перевіркою доступності можливості |
| `app/services/webapp_auth.py` | HMAC-перевірка initData, сесії, CSRF, ролі, аудит |
| `app/routers/webapp.py`, `app/routers/admin.py` | `/api/v1/webapp/*`, `/api/v1/admin/*` |

## 3. Модель даних (міграції 016, 017)

`016_food_ledger_catalog.sql`: `food_products`, `food_nutrition_versions`, `user_product_memberships`, `food_default_rules`, `food_log_drafts`, `food_sync_outbox`, `catalog_import_jobs`, `catalog_import_candidates`, `external_lookup_cache`, а також нові колонки `food_entries` (продукт/ревізія, грами, локальна дата, походження, статус, ключ ідемпотентності, віддалені id, стан синхронізації, revision/version). `calories` тепер може бути NULL (невідомо ≠ 0); для старих записів заповнюється `local_date`.

`017_webapp_admin_preferences.sql`: `user_preferences`, `user_goal_history`, `webapp_sessions`, `user_roles`, `feature_flags`, `admin_audit_log`, `notification_sends`.

Обидві ідемпотентні й зареєстровані в `APPLE_HEALTH_MIGRATIONS` (preflight у Docker CMD) з перевіркою таблиць, унікальних індексів і обмежень.

### Політика зберігання

- ID FatSecret (`food_id`, `serving_id`, `food_entry_id`) зберігаються постійно.
- Назви та харчова цінність від FatSecret кешуються ≤ `FATSECRET_CACHE_HOURS` (макс. 24): `food_products.provider_*`, `food_nutrition_versions` (`provider_cache`), `food_entries.nutrition_expires_at`, знімки в чернетках. Щогодинне очищення видаляє/обнуляє їх; підписи тоді беруться з власних назв користувача (мітка зі щоденника, текст боту).
- Дані Open Food Facts, етикеток і ручні — тривкі ревізії з походженням. Зміна продукту створює нову ревізію; минулі прийоми їжі зберігають свою.

## 4. Надійний запис і денні підсумки

1. Коміт = одна транзакція: рядок `food_entries` + створення в `food_sync_outbox` + членство + вивчений аліас.
2. Воркер надсилає `food_entry.create.v2` зі справжньою грамовою порцією; успіх вимагає підтвердженого `food_entry_id`.
3. Тайм-аут після відправки / 5xx / некоректна «успішна» відповідь → `unknown`: повторно не надсилається. `reconcile_unknown` читає щоденник: рівно один відповідний непов'язаний запис → зв'язати; жодного за 15 хв → безпечний повтор; кілька → лишається неоднозначним (видно в адмінці).
4. Продукти не з FatSecret (OFF/етикетка/вручну) лишаються локальними зі статусом `not_supported`.
5. `merge_daily` рахує пов'язані записи один раз, лише-віддалені та лише-локальні — по одному разу, приховує один відповідний віддалений запис для неоднозначного локального, виключає віддалені записи анульованих локальних і повертає `partial` + причини.
6. Дати — локальний календарний день користувача (`users.timezone`), перетворений у денне число FatSecret.

## 5. Історія FatSecret → «Мої продукти»

Після `/fatsecret/callback` у фоні стартує задача на `history_import_days` (типово 30). Планувальник обробляє до 31 дня за запуск (від найновішого) із чекпоінтом після кожного дня; помилки лишають задачу `partial` з відкладеним повтором. Одна картка на `food_id` (порції об'єднуються), назва — мітка користувача зі щоденника, прийоми їжі не створюються. Видалення = тривке виключення (правила за замовчуванням вимикаються); оновлення та автододавання ботом ніколи не повертають виключені/архівовані картки. Вибірковий режим зберігає кандидатів; явно пропущені стають виключеннями, непереглянуті лишаються в очікуванні. Щоденне оновлення (04:00 UTC) перечитує останні 3 дні.

## 6. API Web App

Автентифікація: `POST /api/v1/webapp/auth/telegram {init_data}` → `session_token`, `csrf_token`. Використовуйте `Authorization: Bearer <session_token>` (рекомендовано у WebView Telegram) або Secure/HttpOnly cookie + `X-CSRF-Token` + `Origin` того ж походження для змін. Сесія живе `WEBAPP_SESSION_TTL_SECONDS`; initData, старші за `WEBAPP_AUTH_MAX_AGE_SECONDS`, відхиляються.

| Група | Ендпоінти |
|---|---|
| Профіль | `GET/PATCH /me` (`profile_version`), `GET/PUT /preferences` (`version`), `GET/PUT /goals` |
| Продукти | `GET /products`, `POST /products`, `GET /products/search`, `POST /products/import`, `GET/PATCH /products/{id}`, `POST /products/{id}/membership`, `POST /products/bulk-membership`, `POST /products/{id}/refresh` |
| За замовчуванням | `GET/POST /default-rules`, `DELETE /default-rules/{id}?version=`, `POST /default-rules/preview` («Перевірити фразу», нічого не записує) |
| Імпорт історії | `POST/GET /catalog-imports`, `GET /catalog-imports/{id}`, `POST /catalog-imports/{id}/selection`, `POST /catalog-imports/{id}/cancel` |
| Щоденник | `GET/PATCH /food-drafts[/{id}]`, `POST /food-drafts/{id}/commit`, `POST /food-drafts/{id}/cancel`, `GET /food-entries?date=`, `POST /food-entries` (`idempotency_key`), `PATCH /food-entries/{id}`, `DELETE /food-entries/{id}?version=`, `POST /food-entries/{id}/copy` |
| Завантаження | `POST /uploads?idempotency_key=&caption=` сире тіло `image/jpeg|png|webp` ≤ 10 МБ → спільна чернетка |
| Інтеграції | `GET /integrations`, `POST /integrations/{fatsecret|whoop}/connect-link`, `POST /integrations/fatsecret/disconnect` |
| Адмін | `GET/POST /api/v1/admin/catalog`, `PATCH /catalog/{id}`, `GET /features`, `PUT /features/{key}`, `GET /jobs`, `POST /jobs/outbox/{id}/retry`, `POST /jobs/imports/{id}/retry`, `GET /audit`, `POST /roles` |

Помилки: `{"detail": {"error": "<код>", ...}}`; застаріла версія → **409**, валідація → 400/422, чужі id → 404. Роль адміна перечитується на кожному запиті; `ADMIN_API_TOKEN` Mini App ніколи не використовує.

## 7. Налаштування

| Рівень | Де |
|---|---|
| Розгортання | `.env`: `FOOD_*`, `FATSECRET_BARCODE_ENABLED`, `FATSECRET_HISTORY_IMPORT_DAYS`, `FATSECRET_CACHE_HOURS`, `OPENAI_VISION_MODEL`, `MEDIA_*`, `OFF_*`, `WEBAPP_*` |
| Прапори (адмін) | `food_history`, `food_barcode`, `fatsecret_barcode`, `food_vision`, `food_plate_photos` — увімкнення недоступної можливості повертає 409 |
| Налаштування користувача | `recording_policy` (`auto_confirmed`/`review_all`), `catalog_auto_add`, `fatsecret_export`, `gram_presets`, `history_import_days`, `history_import_mode`, `history_daily_refresh`, `briefing_morning_enabled/time`, `briefing_evening_enabled/time`, `sync_error_notices` |

Зведення тепер перевіряються кожні 5 хвилин і надсилаються в налаштований локальний час користувача один раз за локальну дату (`notification_sends`).

## 8. Планувальник

| Задача | Інтервал |
|---|---|
| `food_outbox` | 1 хв |
| `food_reconcile` | 10 хв |
| `catalog_import` | 5 хв |
| `catalog_refresh` | 04:00 UTC |
| `food_cache_purge` (кеш провайдера, чернетки, кеш пошуку, сесії) | 1 год |

## 9. Тести

`tests/test_food_*.py`, `test_barcode_reader.py`, `test_fatsecret_writes.py`, `test_webapp_auth.py`, `test_open_food_facts.py` (фікстура записана з OFF v3.4), `test_preferences.py`. Сценарії на реальному PostgreSQL (роблять DROP схеми public!):

```bash
FOOD_TEST_DATABASE_URL=postgresql://... pytest tests/test_food_ledger_db.py
```

## 10. Що досі потребує живої перевірки (план §11)

З репозиторію не перевірити: доступні можливості акаунта FatSecret (штрихкод, розпізнавання зображень, `food.create`), реальні квоти історії, чи `food_entry.create.v2` завжди повертає id, покриття українських товарів у OFF, якість і вартість vision. Код типово поводиться безпечно (штрихкод FatSecret вимкнено, фото страв вимкнено, невизначені записи звіряються), а набір даних для оцінки з плану §10 ще треба зібрати перед увімкненням розпізнавання страв.
