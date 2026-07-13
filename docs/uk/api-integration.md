# 🔌 Інтеграція API

[🇬🇧 English version](../en/api-integration.md)

## Огляд

Система інтегрується з чотирма зовнішніми джерелами/API:
- **FatSecret** - база даних продуктів та калорійності
- **WHOOP** - дані про фізичну активність
- **Apple Health** - iOS health-метрики через native Shortcuts або підтриманий
  сторонній застосунок експорту
- **OpenAI** - розпізнавання мови та аналіз тексту

---

## FatSecret API

### Автентифікація

FatSecret використовує OAuth 2.0 (Client Credentials) для публічних даних.

```bash
POST https://oauth.fatsecret.com/connect/token
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials
&client_id={YOUR_CLIENT_ID}
&client_secret={YOUR_CLIENT_SECRET}
&scope=basic
```

**Відповідь:**
```json
{
  "access_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6...",
  "token_type": "Bearer",
  "expires_in": 86400
}
```

### Пошук продуктів

```bash
GET https://platform.fatsecret.com/rest/food/search/v1
Authorization: Bearer {access_token}
Content-Type: application/json

?search_expression=oatmeal&format=json&max_results=10
```

**Параметри:**
| Параметр | Тип | Опис |
|----------|-----|------|
| search_expression | string | Пошуковий запит |
| format | string | json або xml |
| max_results | int | Максимум результатів (1-50) |
| page_number | int | Номер сторінки |

### Отримання деталей продукту

```bash
GET https://platform.fatsecret.com/rest/food/v5
Authorization: Bearer {access_token}

?food_id=33691&format=json
```

**Відповідь:**
```json
{
  "food": {
    "food_id": "33691",
    "food_name": "Oatmeal",
    "food_type": "Generic",
    "servings": {
      "serving": [
        {
          "serving_id": "34324",
          "serving_description": "1 cup cooked",
          "metric_serving_amount": "234.000",
          "metric_serving_unit": "g",
          "calories": "158",
          "protein": "6.00",
          "fat": "3.20",
          "carbohydrate": "27.40"
        }
      ]
    }
  }
}
```

---

## WHOOP API

### Автентифікація

WHOOP використовує OAuth 2.0 Authorization Code Flow.

**Крок 1: Авторизація**
```
GET https://api.prod.whoop.com/oauth/oauth2/auth
?client_id={CLIENT_ID}
&redirect_uri={REDIRECT_URI}
&response_type=code
&scope=read:workout read:recovery read:sleep read:cycles
&state={RANDOM_STATE}
```

**Крок 2: Обмін коду на токен**
```bash
POST https://api.prod.whoop.com/oauth/oauth2/token
Content-Type: application/x-www-form-urlencoded

grant_type=authorization_code
&code={AUTH_CODE}
&redirect_uri={REDIRECT_URI}
&client_id={CLIENT_ID}
&client_secret={CLIENT_SECRET}
```

**Крок 3: Оновлення токена**
```bash
POST https://api.prod.whoop.com/oauth/oauth2/token
Content-Type: application/x-www-form-urlencoded

grant_type=refresh_token
&refresh_token={REFRESH_TOKEN}
&client_id={CLIENT_ID}
&client_secret={CLIENT_SECRET}
```

### Scopes (Дозволи)

| Scope | Опис |
|-------|------|
| read:workout | Дані тренувань |
| read:recovery | Показники відновлення |
| read:sleep | Дані сну |
| read:cycles | Фізіологічні цикли |
| read:body_measurement | Виміри тіла |

### Отримання тренувань

```bash
GET https://api.prod.whoop.com/developer/v2/activity/workout
Authorization: Bearer {access_token}

?limit=10&start=2026-01-01T00:00:00Z
```

**Відповідь:**
```json
{
  "records": [
    {
      "id": "ecfc6a15-4661-442f-a9a4-f160dd7afae8",
      "user_id": 9012,
      "sport_name": "running",
      "start": "2026-01-28T10:00:00Z",
      "end": "2026-01-28T10:45:00Z",
      "score_state": "SCORED",
      "score": {
        "strain": 8.5,
        "kilojoule": 1340.5,
        "average_heart_rate": 145,
        "max_heart_rate": 172,
        "zone_durations": {
          "zone_one_milli": 300000,
          "zone_two_milli": 600000,
          "zone_three_milli": 1200000,
          "zone_four_milli": 900000,
          "zone_five_milli": 180000
        }
      }
    }
  ],
  "next_token": "MTIzOjEyMzEyMw"
}
```

### Отримання відновлення

```bash
GET https://api.prod.whoop.com/developer/v2/recovery
Authorization: Bearer {access_token}
```

**Відповідь:**
```json
{
  "records": [
    {
      "cycle_id": "12345",
      "score_state": "SCORED",
      "score": {
        "recovery_score": 78,
        "resting_heart_rate": 52,
        "hrv_rmssd_milli": 45.5,
        "spo2_percentage": 98.2,
        "skin_temp_celsius": 36.5
      }
    }
  ]
}
```

### Формула конвертації

```
Калорії (kcal) = Кілоджоулі / 4.184
```

---

## Apple Health Sync

Apple Health не має backend Web API. Підтриманий прямий спосіб налаштування:

- **Native iOS Shortcuts, без встановлення додаткових застосунків** —
  рекомендований low-friction шлях для користувачів, які не хочуть сторонній
  застосунок.

JSON у форматі HAE приймається лише від розширеної клієнтської обгортки, яка
може сформувати причинну часову мітку експорту до мережевого запиту. Стандартна
REST-автоматизація *Health Auto Export — JSON+CSV* не підтримується як прямий
клієнт, оскільки її користувацькі заголовки статичні й не містять часу створення
експорту.

Справжнього backend-only або zero-device-setup способу для Apple Health немає,
бо дані Apple Health залишаються на iPhone користувача, доки iOS їх не надішле.

### Рекомендовано: готовий iOS Shortcut

Apple Health не має backend Web API. Користувач підключає його через
`/connect_apple_health`: бот генерує персональний токен і webhook URL для iOS
Shortcut.
Повторний запуск `/connect_apple_health` змінює token: новий URL починає
працювати одразу, а всі попередні URL відхиляються. Використовуй повторне
підключення як спосіб відкликати URL або token, якщо він потрапив не туди.

Рекомендований onboarding — готовий Shortcut з назвою
`Health Tracker Apple Health Sync`:

1. У Telegram виконай `/connect_apple_health`.
2. Відкрий Shortcut import link/file на iPhone або iPad користувача.
3. Встав згенерований webhook URL в import question Shortcut.
4. Запусти Shortcut один раз і дозволь доступ до Health та Network.
5. У **Shortcuts** -> **Automation** створи **Personal Automation**, наприклад
   **Time of Day**, і вибери імпортований Shortcut для регулярного запуску.

Відкривай і запускай Shortcut на iPhone або iPad. macOS не підтримує дію
**Find Health Samples**. Коли Mac відкриває download endpoint, сервер показує
сторінку-підказку для переходу на мобільний пристрій замість встановлення
Shortcut, який не запуститься. iPad із desktop-style User-Agent `Macintosh`
далі отримує підписаний Shortcut-файл.

Готовий Shortcut виконує чотири запити **Find Health Samples** і об'єднує їх
результати в один POST:

| Тип Health (назва у picker) | Надсилається як `type` | Unit | Фільтр дати |
|---|---|---|---|
| Steps | `step_count` | `count` | Start Date is today |
| Active Calories | `active_energy` | `kcal` | Start Date is today |
| Sleep | `sleep_analysis` | `s` | End Date is today |
| Heart Rate Variability SDNN | `heart_rate_variability` | `ms` | Start Date is today |

Точкові метрики надсилають лише зразки, для яких **Start Date is today** у
локальному календарі iPhone, тож Shortcut не експортує всю історію Health або
рухоме вікно за попередні 24 години.

Сон обробляється інакше у трьох аспектах:

- Зразки сну — це category-семпли, чиї Value/Duration у Shortcuts рендеряться
  локалізованим текстом, тому кожна метрика сну надсилається з `value: 0` та
  полями `end` (ISO 8601 дата завершення) і `stage`; сервер обчислює тривалість
  з `end`, а `stage` зберігає як діагностичні дані.
- Запит сну використовує **End Date is today**. Це включає ніч, яка почалася до
  опівночі, але залишає заявлений snapshot у межах одного календарного дня,
  повного станом на момент sync; сервер зараховує зразок сну до дня, в який його
  інтервал *завершується*.
- Зразки сну, що перекриваються (обгортка In Bed плюс стадії Core/REM/Deep, або
  джерела iPhone і Watch), сервер об'єднує як часові інтервали, а не сумує, тож
  ніч не рахується двічі.

HRV (SDNN) показується як **стрес-проксі** у брифінгах і асистенті: в Apple
Health немає нативної метрики стресу, а нижчий за звичний HRV корелює з вищим
стресом. Для користувачів з підключеним WHOOP основним сигналом відновлення
залишається WHOOP recovery. Якщо після імпорту в запиті HRV поле Type порожнє —
вручну вибери *Heart Rate Variability SDNN* у редакторі Shortcuts: назва у
picker може відрізнятися залежно від версії iOS.

Після зміни шаблону Shortcut наявні користувачі мають видалити раніше
імпортований Shortcut, імпортувати його заново за тим самим посиланням і
дозволити доступ до Health для типів даних (дозволи Health видаються окремо на
кожен тип). Сервер відхиляє стару імпортовану копію без schema-v3 snapshot
envelope.

Backend віддає підписаний artifact тут:

```text
GET /api/v1/health/apple-health/shortcut
```

`/connect_apple_health` напряму дає посилання на цей URL, тому окрема env-змінна
для Shortcut URL не потрібна. Editable source template лежить у
`docs/shortcuts/apple-health-sync.shortcut.plist`; підписаний artifact можна
згенерувати через Apple Shortcuts CLI:

```bash
cp docs/shortcuts/apple-health-sync.shortcut.plist \
  /tmp/apple-health-sync-source.shortcut
shortcuts sign --mode anyone \
  --input /tmp/apple-health-sync-source.shortcut \
  --output docs/shortcuts/apple-health-sync.shortcut
```

Вхідний файл має зберігати розширення `.shortcut`: Shortcuts використовує його,
щоб розпізнати джерело як Shortcut, який можна підписати.

Apple усе одно вимагає підтвердження на пристрої. Повністю backend-only або
zero-touch setup неможливий: імпорт Shortcut, Health permission, Network
permission і Personal Automation прив'язані до конкретного iPhone/iPad.

```bash
POST /api/v1/health/apple-health/sync?userId={telegram_user_id}&token={per_user_token}
Content-Type: application/json
```

```json
{
  "sourceType": "apple_health",
  "schemaVersion": 3,
  "snapshot": {
    "collector": "shortcut",
    "timezone": "+03:00",
    "coveredDates": ["2026-07-11"],
    "coveredMetricFamilies": ["steps", "active_energy", "sleep", "hrv"],
    "generatedAt": "2026-07-11T10:05:00+03:00"
  },
  "metrics": [
    {
      "type": "step_count",
      "value": 5000,
      "unit": "count",
      "timestamp": "2026-07-11T10:00:00+03:00"
    },
    {
      "type": "sleep_analysis",
      "value": 0,
      "unit": "s",
      "timestamp": "2026-07-10T23:04:00+03:00",
      "end": "2026-07-11T06:34:00+03:00",
      "stage": "Core"
    }
  ]
}
```

`snapshot.timezone` — це назва IANA (наприклад, `"Europe/Kyiv"`) або фіксований
зсув UTC (наприклад, `"+03:00"`). Готовий Shortcut форматує поточний зсув
пристрою як `XXXXX`. `snapshot.collector` — стабільний ідентифікатор відправника,
а `snapshot.generatedAt` — offset-aware timestamp свіжості, за яким упорядковуються
snapshots цього collector. Єдиний елемент `snapshot.coveredDates` — поточна
локальна дата, а `coveredMetricFamilies` оголошує сімейства, повні станом на
момент sync. Точкові семпли використовують **Start Date is today**, а сон —
**End Date is today**.
Приймаються лише live collectors `shortcut` і `health_auto_export`; native
webhook payload має використовувати `shortcut`. Кожне сімейство може покривати
не більш як 31 дату в межах 30 днів до ingestion або одного дня після нього,
а payload має використовувати рівно один із двох форматів опису coverage.

Згенерований URL вже містить Telegram user ID (`users.telegram_user_id`) і
персональний token, тому в Shortcut достатньо вказати URL, `Content-Type:
application/json` і payload з метриками. **Не додавай поля `userId` чи `token`
у Request Body** — вони вже у URL, і дублювання у тілі — типова помилка при
налаштуванні Shortcut. Webhook також підтримує старий варіант:
`X-Apple-Health-Token` header і поле `userId` у body (для зворотної сумісності).
Метрики старші за 30 днів відхиляються. Дані Apple Health більше **не**
записуються в уніфіковану таблицю `health_data`. Сервер парсить і агрегує знімок
у пам'яті та зберігає один оброблений рядок на (`user_id`, `source`, `collector`,
`metric_date`, `metric_family`) у таблиці `health_daily_metric_aggregates`
(міграція `010_health_daily_metric_aggregates.sql`). Таблиця schema v2
`health_daily_aggregates` залишається доступною для читання під час rollout.
Зберігання сирих семплів для нових імпортів Apple Health дорівнює **0** — у
`health_data` не пишуться рядки, а сире тіло запиту ніколи не пересилається в
Telegram. Telegram отримує лише санітизований підсумок з лічильниками. Webhook
потоково приймає не більш як 5 MiB, повертає HTTP 413 до парсингу більшого тіла
та відображає malformed або надмірно вкладений JSON у HTTP 400.

#### Модель агрегації за сімействами (зберігання сирих даних 0)

- **Конверт знімка (обовʼязковий).** Кожен POST має містити `schemaVersion: 3`,
  `snapshot.collector`, offset-aware `snapshot.generatedAt`,
  `snapshot.timezone` і або `coveredDates` разом із `coveredMetricFamilies`, або
  `coveredDatesByFamily`. Готовий Shortcut оголошує поточну локальну дату та
  чотири сімейства, які він фактично запитав.
- **Повнота за сімействами.** Замінюються лише оголошені сімейства. Порожнє
  покрите сімейство записує авторитетний нуль, не стираючи інші метрики,
  зібрані іншим запитом чи інтеграцією.
- **Свіжість та ідемпотентність.** Новіший snapshot того самого collector
  замінює його рядок сімейства. Явне новіше спостереження підвищує freshness,
  навіть якщо оброблений total не змінився. Той самий timestamp і вміст є replay,
  старіший snapshot ігнорується як stale, а той самий timestamp свіжості з
  іншими обробленими даними повертає HTTP 409. Для HAE freshness відстежується
  окремо за family/date, тому новіший семпл одного сімейства не робить старі дані
  іншого сімейства новішими.
- **День атрибуції має бути покритий.** День атрибуції кожного семпла
  (offset-aware локальна дата його timestamp; для сну — локальна дата end
  timestamp) має бути оголошений для цього сімейства. Закодований у timestamp
  offset зберігається під час переходів DST замість застосування одного
  фіксованого offset до всього export. Неоголошені дні відхиляються як часткові
  або неоднозначні.
- **Застарілі payloads відхиляються, а не мержаться.** Payloads без
  метаданих повноти й свіжості schema v3 відхиляються
  з дієвою помилкою «Re-import the latest Shortcut…». Це навмисно: старий payload
  зі змінним вікном не міг гарантувати повні дні.
- **HAE-shaped JSON працює за строгим raw-snapshot контрактом.** Кожен request
  експортує рівно одну unaggregated метрику. Сервер виводить повне покриття цього
  сімейства з attested date period та явного `X-Health-Tracker-Timezone`, тому
  покритий день без точки записується як нуль, а не залишає застаріле значення.
  Кожен request також має містити `X-Health-Tracker-Generated-At`, створений на
  клієнті разом із export і до network dispatch. Receipt time та mutable
  timestamps семплів HealthKit ніколи не визначають порядок snapshots.
- **Сон враховує стадії.** Core, REM, Deep та інші asleep-стадії об'єднуються;
  Awake та In Bed не збільшують тривалість сну. Якщо asleep-стадій немає,
  fallback — об'єднання In Bed мінус Awake. День лише з Awake зберігає нуль.
  Відомі українські назви стадій Apple нормалізуються до тих самих канонічних
  стадій; невідома локалізована назва ігнорується та враховується в діагностиці,
  а не вважається сном.
- **Перехідне читання.** Статистика спочатку фільтрує candidate rows за запитаним
  вікном у timezone кожного рядка, потім окремо для кожної дати й сімейства
  вибирає найновіший in-window live collector. Відсутні значення доповнюються зі
  schema-v2 aggregates, migration/backfill рядків і наостанок legacy raw samples.
  Різні live collectors одного сімейства ніколи не сумуються.
- **Відповідь синхронізації / підсумок у Telegram.** Відповідь тепер повідомляє:
  отримано й агреговано семплів, оновлено/replayed/stale рядків сімейств,
  помилки та `raw stored: 0`.

#### Backfill застарілих сирих даних

Міграція `010` є адитивною. Спочатку виконай backfill застарілих рядків Apple
Health без їх видалення:

```bash
python -m app.backfill_apple_health
```

Timezone за замовчуванням — `Europe/Kyiv`, а створені рядки мають collector
`legacy_backfill`. Після перевірки читання застосунком і кількості рядків
оператор може явно запустити contract phase:

```bash
python -m app.backfill_apple_health --delete-raw
```

Delete mode видаляє лише точні ID сирих рядків, заблокованих і перевірених у
цьому запуску, звіряє набір повернутих ID та завершується з ненульовим кодом за
будь-якої помилки backfill або residual purge. Він відмовляється видаляти
непідтримувані raw metric types і тримає writer-blocking table lock до фінальної
перевірки нульової кількості рядків. Неруйнівний режим за
замовчуванням треба зберегти для rollback.
Rollback-скрипти `009` і `010` також fail closed, якщо відповідна aggregate
table містить рядки, та беруть exclusive lock до перевірки; експортуй дані або
застосуй forward fix замість тихого видалення обробленої Health-історії.

Endpoint очікує JSON, але якщо тіло не є валідним JSON, він пробує розпарсити
його як Apple property list (бінарний `bplist00` або XML plist). Це покриває
налаштування Shortcuts, де словник проходить через кроки **Get Type** / plist і
в тіло запиту потрапляють байти plist замість JSON. Дати з plist конвертуються
в ISO 8601 рядки, а data-блоби — в UTF-8 (або Base64) рядки перед інжестом.
Тіла, які не є ані JSON, ані plist-словником, як і раніше відхиляються з
`400 Invalid JSON payload`.

#### Ручний fallback для Shortcut

Використовуй це тільки якщо готовий Shortcut не імпортується або його потрібно
налагодити.

1. У Telegram виконай `/connect_apple_health` і скопіюй згенерований URL. Запусти команду
   повторно пізніше, якщо потрібно відкликати старий URL і створити новий.
2. На iPhone відкрий **Shortcuts** -> **Automation** -> **New Automation**.
3. Обери **Time of Day**, задай час синхронізації та повторення щодня. Для
   частішої синхронізації створи кілька automation, наприклад ранок, день і
   вечір.
4. Додай дію **Find Health Samples**.
   - Type: обери метрику, наприклад **Steps**.
   - Start Date: обери **is today**. Не використовуй рухомий діапазон
     `Current Date - 1 day`; **is today** використовує локальний календарний
     день iPhone, що починається з опівночі.
   - Group By: **Hour** або **Day**.
5. Додай дію **Repeat with Each** для результату Health Samples.
6. Усередині repeat-блоку додай дію **Dictionary** для одного metric object:
   - `type`: `step_count`
   - `value`: властивість **Value** поточного repeat item («Quantity» не є
     властивістю health sample і рендериться порожнім рядком). Сервер також
     приймає текстові значення на кшталт `"434 count"` чи `"68,5"` і
     видобуває числову частину.
   - `unit`: `count`
   - `timestamp`: start date поточного repeat item через **Format Date** у
     форматі **ISO 8601**
   - `duration`: `3600` для hourly samples або пропусти, якщо duration невідомий
7. Додавай кожен metric dictionary до list variable з назвою `metrics`.
8. Після repeat-блоку відформатуй **Current Date** трьома способами: ISO 8601
   без часу для покритої дати, custom format `XXXXX` для UTC offset та ISO 8601
   з часом для `generatedAt`. Поклади покриту дату в одноелементний **List**,
   потім створи Dictionary `snapshot` з `timezone`, `coveredDates` і
`generatedAt`. Також задай `collector` як `shortcut` і додай List
   `coveredMetricFamilies`, що містить лише сімейства, які запитує цей Shortcut.
   Додай фінальну дію **Dictionary** для request body:
   - `sourceType`: `apple_health`
   - `schemaVersion`: `3`
   - `dataType`: `activity`
   - `snapshot`: Dictionary snapshot
   - `metrics`: list variable `metrics`
9. Додай **Get Contents of URL**:
   - URL: встав URL з Telegram із кроку 1.
   - Method: **POST**.
   - Headers: `Content-Type` = `application/json`.
   - Request Body: **JSON** або **Dictionary**, використай request body
     dictionary з кроку 8.
10. Запусти Shortcut один раз вручну. Успішний перший sync повертає JSON на
    кшталт `{"schema_version": 3, "records_received": 1,
    "records_aggregated": 1, "aggregate_rows_updated": 1,
    "aggregate_rows_replayed": 0, "aggregate_rows_stale": 0,
    "raw_stored": 0, "records_failed": 0}`.

Для іншого підтримуваного сімейства повтори той самий шаблон і оголоси його в
`coveredMetricFamilies`. Підтримуються steps, active energy, heart rate, HRV і
sleep. Тримай timestamps у форматі ISO 8601, а кожну метрику — новішою за 30
днів. Непідтримувані типи потрапляють у діагностику, але не зберігаються.

### Health Auto Export iOS app (сторонній застосунок)

Той самий endpoint `/api/v1/health/apple-health/sync` також приймає JSON у
форматі застосунку *Health Auto Export — JSON+CSV*. Коли тіло запиту має
форму (`{"data": {"metrics": [{"name", "units", "data": [...]}]}}`), сервер
розгортає кожну точку `data[]` в окрему внутрішню метрику й пропускає її через
ту саму пайплайн-обробку.

Стандартна REST automation Health Auto Export **не підтримується як прямий
schema-v3 sync client**: HAE документує статичні custom headers, але не надає
causal timestamp, створений разом з export. Фіксоване значення
`X-Health-Tracker-Generated-At` або `now()` на ingress proxy є небезпечними:
другий варіант лише перейменовує receipt order. Для прямого sync з iPhone
використовуй native Apple Shortcut вище.

Advanced client-side wrapper може пересилати HAE-shaped JSON лише якщо він
створює timestamp до dispatch і надсилає весь контракт:

- `automation-period`: `default`, `none` (трактується як Default), `today`,
  `yesterday` або `previous7days`; incremental/realtime periods відхиляються.
- `automation-aggregation: none`, Batch Requests off і рівно одна підтримувана
  метрика. HAE агрегує кілька вибраних метрик, тому multi-metric requests
  відхиляються; sleep має складатися з unaggregated segments.
- `X-Health-Tracker-HAE-Mode: complete-unbatched-unaggregated-single-metric-v1`.
- `X-Health-Tracker-Timezone`: IANA timezone на кшталт `Europe/Kyiv` або
  фіксований offset, наприклад `+03:00`.
- `X-Health-Tracker-Generated-At`: offset-aware timestamp, створений разом із
  цим export до відправлення HTTP request.

Request має бути меншим за 5 MiB. Malformed points та aggregated sleep fail
closed. Новіший valid marker може авторитетно очистити period через `data: []`;
старіший marker є stale, а різний content під тим самим marker повертає `409`.
Прийняті sleep segments зберігають `startDate`, `endDate` та `value`; `Awake` й
`In Bed` ніколи не перемарковуються як asleep.

---

## OpenAI API

### Whisper (Speech-to-Text)

```bash
POST https://api.openai.com/v1/audio/transcriptions
Authorization: Bearer {OPENAI_API_KEY}
Content-Type: multipart/form-data

file: {audio_file}
model: whisper-1
language: uk
```

**Відповідь:**
```json
{
  "text": "На сніданок їв вівсянку з бананом, приблизно двісті грам каші"
}
```

### GPT (Аналіз тексту)

```bash
POST https://api.openai.com/v1/chat/completions
Authorization: Bearer {OPENAI_API_KEY}
Content-Type: application/json

{
  "model": "gpt-4",
  "messages": [
    {
      "role": "system",
      "content": "Extract food items from text. Return JSON array with name, amount, unit."
    },
    {
      "role": "user",
      "content": "На сніданок їв вівсянку з бананом, приблизно двісті грам каші"
    }
  ],
  "response_format": { "type": "json_object" }
}
```

**Відповідь:**
```json
{
  "foods": [
    {
      "name": "oatmeal",
      "name_uk": "вівсянка",
      "amount": 200,
      "unit": "g"
    },
    {
      "name": "banana",
      "name_uk": "банан",
      "amount": 1,
      "unit": "piece"
    }
  ],
  "meal_type": "breakfast",
  "confidence": 0.95
}
```

---

## Rate Limits

| API | Ліміт |
|-----|-------|
| FatSecret | 5,000 запитів/день |
| WHOOP | 100 запитів/хвилина |
| OpenAI | Залежить від плану |

---

## Обробка помилок

### HTTP коди відповідей

| Код | Значення | Дія |
|-----|----------|-----|
| 200 | Успіх | - |
| 400 | Невірний запит | Перевірити параметри |
| 401 | Не авторизовано | Оновити токен |
| 429 | Перевищено ліміт | Зачекати та повторити |
| 500 | Помилка сервера | Повторити пізніше |

### Retry стратегія

```javascript
const retry = async (fn, maxRetries = 3, delay = 1000) => {
  for (let i = 0; i < maxRetries; i++) {
    try {
      return await fn();
    } catch (error) {
      if (i === maxRetries - 1) throw error;
      if (error.status === 429) {
        await sleep(delay * Math.pow(2, i));
      }
    }
  }
};
```
