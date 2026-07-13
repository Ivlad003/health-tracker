# План посилення Apple Health у PR #9

> **Для агентних виконавців:** ОБОВ'ЯЗКОВИЙ ПІДСКІЛ: використовуйте superpowers:subagent-driven-development, щоб виконувати цей план завдання за завданням.

**Мета:** Зробити PR #9 безпечним для merge: забезпечити причинно впорядковані snapshots Apple Health за сімействами метрик, зберегти legacy-дані під час rollout, прибрати пересилання raw payload і вимагати живу PostgreSQL-перевірку.

**Архітектура:** Schema v3 зберігає один оброблений рядок на користувача, collector, локальну дату та сімейство метрик у новій additive-таблиці. Ingestion перевіряє явне покриття, агрегує в пам'яті та freshness-upsert'ить усі рядки в одній транзакції. Під час expand-migrate-contract читачі накладають v3-рядки поверх legacy daily rows і raw rows за датою/сімейством. Backfill за замовчуванням не видаляє дані; видалення є окремим purge за вибраними ID.

**Технології:** Python 3.12, FastAPI, asyncpg, PostgreSQL 15, pytest/pytest-asyncio, Apple Shortcuts plist і `shortcuts sign`.

## Глобальні обмеження

- Ніколи не зберігати й не пересилати raw HealthKit request body; поза пам'ять запиту можуть виходити лише оброблені добові aggregates і санітизовані summaries.
- Snapshot може обнулити метрику лише тоді, коли це сімейство метрик і дата явно покриті.
- Старі snapshots ніколи не перезаписують нові; однаковий timestamp з іншим обробленим вмістом є конфліктом.
- Native Shortcut і Health Auto Export не підсумовуються, коли описують однакове сімейство/дату; читач вибирає найсвіжіший collector row.
- Усі записи одного request, включно з success bookkeeping та import log, commit'яться або rollback'яться разом.
- Legacy raw rows залишаються доступними для читання та збереженими, доки явно запущений purge не перевірить точний набір ID.
- Зміни документації дзеркаляться в `docs/en/` і `docs/uk/`.
- Нові runtime-залежності не додаються.

---

## Завдання 1: Зафіксувати виправлений протокол та агрегацію failing-тестами

**Файли:**

- Змінити: `tests/test_apple_health.py`
- Змінити: `tests/test_apple_health_db.py`
- Змінити: `tests/test_apple_health_stats.py`
- Змінити: `tests/test_apple_health_shortcut_artifact.py`
- Додати: `tests/test_backfill_apple_health.py`

- [ ] Додати schema-v3 тести для обов'язкових `collector`, offset-aware `generatedAt` і покриття за сімейством метрик/датою.
- [ ] Додати контрольовані offset-тести для `+24:00`, `+03:60`, валідних `+14:00`, `-12:00`, `+05:45` і `Europe/Kyiv`.
- [ ] Додати sleep-stage тести: staged sleep виключає Awake/In Bed; In Bed minus Awake є fallback лише без asleep stages; Awake-only дає нуль; overlap asleep stages об'єднується.
- [ ] Додати real-PostgreSQL тести для stale/equal/newer snapshots, partial family coverage, native/HAE collision, equal-time conflict і rollback транзакції після помилки пізнішого рядка.
- [ ] Додати reader-overlay тести: v3 перемагає за сімейством, legacy daily rows заповнюють пропуски, raw-only windows видимі, zero aggregate залишається авторитетним.
- [ ] Додати backfill-тести для Kyiv attribution, nondestructive default, selected-ID purge, збереження concurrent late row, residual failure і ненульового CLI exit.
- [ ] Оновити структурні Shortcut-тести: schema v3, `collector=shortcut`, чотири явно покриті сімейства.
- [ ] Запустити targeted tests і підтвердити очікувані падіння до implementation.

## Завдання 2: Реалізувати schema-v3 ingestion, freshness, sleep, atomicity і privacy

**Файли:**

- Додати: `database/migrations/010_health_daily_metric_aggregates.sql`
- Додати: `database/migrations/010_health_daily_metric_aggregates_rollback.sql`
- Змінити: `app/db_preflight.py`
- Змінити: `app/services/apple_health.py`
- Змінити: `app/routers/apple_health.py`
- Змінити: `app/services/telegram_bot.py`

- [ ] Додати `health_daily_metric_aggregates` з ключем `(user_id, source, collector, metric_date, metric_family)`, non-null freshness time і hash обробленого вмісту.
- [ ] Additive-мігрувати наявні daily aggregates у family rows collector `legacy_daily`, не видаляючи стару таблицю.
- [ ] Парсити schema-v3 coverage у family-to-date map; відхиляти missing, naive, future, malformed та inconsistent metadata.
- [ ] Зберігати encoded local attribution date кожної метрики, щоб HAE через DST не bucket'ив усі samples за offset першого sample.
- [ ] Незалежно агрегувати steps, active energy, heart rate, HRV і sleep; реалізувати stage-aware sleep rules.
- [ ] Upsert'ити лише новіші family rows; identical replay — no-op, older — stale, equal-time/different-content — conflict.
- [ ] Використати одну connection/transaction для family rows, sync success bookkeeping та success log.
- [ ] Підіймати `AppleHealthPersistenceError` для DB failures і повертати санітизований HTTP 500.
- [ ] Прибрати пересилання raw Telegram documents і невикористаний `send_document`.
- [ ] Запускати targeted unit та PostgreSQL tests до green.

## Завдання 3: Реалізувати безпечний expand-migrate-contract reader і backfill

**Файли:**

- Змінити: `app/services/apple_health.py`
- Змінити: `app/backfill_apple_health.py`
- Змінити: `database/migrations/009_health_daily_aggregates_rollback.sql`
- Змінити: `tests/test_apple_health_stats.py`
- Змінити: `tests/test_apple_health_db.py`
- Змінити: `tests/test_backfill_apple_health.py`

- [ ] Читати найсвіжіший v3 collector row за датою/сімейством, потім заповнювати лише відсутні authorities із legacy daily rows, потім із raw rows.
- [ ] Для legacy raw attribution використовувати `Europe/Kyiv` за замовчуванням і зберегти explicit timezone override.
- [ ] Писати backfilled family rows під collector `legacy_backfill` із freshness, виведеним із вибраних historical samples.
- [ ] За замовчуванням зберігати raw rows; додати `--delete-raw` для destructive phase.
- [ ] Вибирати raw IDs з `FOR UPDATE`, видаляти лише ці IDs, порівнювати returned/selected sets і падати за наявності residual rows.
- [ ] Підіймати explicit backfill error для будь-якої per-user failure або incomplete purge, завжди закриваючи connection.
- [ ] Rollback не повинен drop'ати непорожню processed aggregate table.
- [ ] Запускати reader/backfill unit і live-PostgreSQL tests до green.

## Завдання 4: Замінити чутливі test data, додати CI, оновити docs і повторно підписати

**Файли:**

- Змінити: `tests/fixtures/apple-health-payload.json`
- Змінити: `tests/test_apple_health.py`
- Змінити: `tests/test_apple_health_db.py`
- Додати: `.github/workflows/apple-health-postgres.yml`
- Змінити: `docs/shortcuts/apple-health-sync.shortcut.plist`
- Змінити: `docs/shortcuts/apple-health-sync.shortcut`
- Змінити: `docs/en/api-integration.md`
- Змінити: `docs/uk/api-integration.md`
- Змінити: `docs/en/architecture.md`
- Змінити: `docs/uk/architecture.md`
- Змінити: `docs/en/session-knowledge.md`
- Змінити: `docs/uk/session-knowledge.md`

- [ ] Замінити production-like export на компактний fixture з явною позначкою synthetic і обчислювати expectations із його вмісту.
- [ ] Додати GitHub Actions job із PostgreSQL 15 service, `APPLE_HEALTH_TEST_DATABASE_URL` і повним suite.
- [ ] Оновити Shortcut payload до schema v3 з collector і covered-family metadata.
- [ ] Провалідувати source plist, підписати через `shortcuts sign --mode anyone`, decrypt/read back signed workflow і перевірити action/import-question parity.
- [ ] У двох мовах описати protocol, zero-raw Telegram behavior, transition reader, nondestructive backfill, explicit purge і rollback limits.

## Завдання 5: Фінальна перевірка та доставка

**Файли:** усі змінені файли

- [ ] Запустити `git diff --check` і перевірити повний staged diff на secrets та unrelated files.
- [ ] Запустити full suite з `APPLE_HEALTH_TEST_DATABASE_URL` проти disposable PostgreSQL 15.
- [ ] Запустити plist lint, signed-artifact decrypt/readback і SHA checks.
- [ ] Отримати незалежний whole-branch code/architecture review і усунути всі critical/important findings.
- [ ] Commit з Conventional Commit + Lore trailers і push у PR head branch.
- [ ] Не переписувати remote history без явного дозволу; повідомити, що видалення старого sensitive blob із reachable PR commits потребує force-push-with-lease та, можливо, GitHub cache purge.
