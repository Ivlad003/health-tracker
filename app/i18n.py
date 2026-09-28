"""User-facing message catalog (Ukrainian + English).

Usage: ``t("sync_done", lang)`` or ``t("tz_set", lang, tz="Europe/Warsaw")``.
``users.language`` stores ``uk`` or ``en``; new users get it from their
Telegram ``language_code``; ``/language`` changes it. GPT replies already
follow the language the user writes in — this catalog covers fixed texts.
"""
from __future__ import annotations

from typing import Any, Optional

DEFAULT_LANGUAGE = "uk"
SUPPORTED_LANGUAGES = ("uk", "en")


def normalize_language(code: Optional[str]) -> str:
    """Map a Telegram language_code / stored value to a supported language."""
    if not code:
        return DEFAULT_LANGUAGE
    code = str(code).strip().lower()
    if code.startswith("uk") or code.startswith("ua"):
        return "uk"
    if code.startswith("en"):
        return "en"
    # Russian-speaking Ukrainians are the main other audience; everyone else
    # gets English.
    return "uk" if code.startswith("ru") else "en"


_UK: dict[str, str] = {
    "help": (
        "👋 Привіт! Я твій персональний помічник з здоров'я.\n"
        "\n"
        "🍎 Що я вмію:\n"
        "  ▸ Записувати їжу — просто напиши що з'їв\n"
        "     Наприклад: «200г курячої грудки з рисом»\n"
        "  ▸ 🏋️ Записувати тренування — «жим 80кг 3×8»\n"
        "  ▸ 📊 Що робив минулого разу — «що робив на жимі?»\n"
        "  ▸ 📈 Прогрес — «прогрес присідань»\n"
        "  ▸ 📓 Щоденник — просто опиши свій стан\n"
        "  ▸ 📓 Історія — «покажи щоденник»\n"
        "  ▸ 🎙 Голосові — скажи що з'їв або зробив голосом\n"
        "  ▸ 📷 Фото штрихкоду або етикетки + вага, напр. «135 г»\n"
        "  ▸ 🔁 Пам'ятаю твої продукти з FatSecret і попередні вибори\n"
        "  ▸ 📊 Калорії за день з FatSecret + WHOOP / Apple Health\n"
        "  ▸ 😴 Дані WHOOP — сон, відновлення, тренування\n"
        "  ▸ 🗑 Видалити останній запис — «видали останнє»\n"
        "  ▸ 🎯 Встановити ціль — «встанови ціль 2500 ккал»\n"
        "\n"
        "🔗 Підключення сервісів:\n"
        "  ⌚ WHOOP → /connect_whoop\n"
        "  ❤️ Apple Health → /connect_apple_health\n"
        "  🧭 Інструкція Apple Health → /apple_health_help\n"
        "  🥗 FatSecret → /connect_fatsecret\n"
        "  🔄 Синхронізувати → /sync\n"
        "  🕐 Часовий пояс → /timezone\n"
        "  🧬 Профіль для BMR → /profile\n"
        "  🌐 Мова → /language\n"
        "  🏋️ Gym промпт → /gym_prompt\n"
        "  📓 Щоденник → /journal, /journal_time, /journal_off, /journal_on\n"
        "  📱 Застосунок → /app\n"
        "\n"
        "⏰ Авто-зведення: 08:00 🌅 та 21:00 🌙 (за твоїм часовим поясом)\n"
        "\n"
        "Просто пиши мені як другу — я розумію 🇺🇦 та 🇬🇧!"
    ),
    "apple_help": (
        "🧭 Як підключити Apple Health\n"
        "\n"
        "Рекомендований шлях — готовий Shortcut для Apple Shortcuts на iPhone або iPad. "
        "Це вбудований додаток Apple, нічого додатково встановлювати не треба.\n"
        "\n"
        "Shortcut синхронізує: 👣 кроки, 🔥 активні калорії, 😴 сон, 🧘 HRV "
        "(варіабельність пульсу — використовуємо як стрес-проксі: нижчий за звичний "
        "HRV ≈ вищий стрес), 💓 пульс у спокої, 🚶 дистанцію ходьби/бігу, "
        "🏃 хвилини тренувань і ⚖️ вагу (для оцінки базового метаболізму).\n"
        "\n"
        "1. У Telegram натисни /connect_apple_health.\n"
        "2. Відкрий готовий Shortcut `Health Tracker Apple Health Sync` саме на iPhone або iPad. "
        "На Mac дія Find Health Samples не підтримується.\n"
        "3. Під час імпорту встав URL з повідомлення бота в Import Question.\n"
        "4. Запусти Shortcut один раз, дозволь доступ до Health і Network. "
        "Health питає дозвіл окремо для кожного типу даних (кроки, калорії, сон, HRV, "
        "пульс у спокої, дистанція, хвилини тренувань, вага) "
        "— дозволь усі, які хочеш синхронізувати.\n"
        "5. У Shortcuts → Automation створи Personal Automation Time of Day і вибери "
        "цей Shortcut для регулярного запуску.\n"
        "\n"
        "Після першого запуску перевір статус командою /sync.\n"
        "\n"
        "🏃 Тренування (Workouts) бот приймає з Health Auto Export або з Shortcut, "
        "який додає масив workouts — див. документацію.\n"
        "\n"
        "⚠️ Якщо ти імпортував Shortcut раніше, видали його і імпортуй заново за тим "
        "самим посиланням, потім знову дозволь доступ до Health — інакше бот і далі "
        "отримуватиме лише старий набір показників.\n"
        "\n"
        "Apple не дозволяє повністю zero-touch sync: імпорт Shortcut, дозволи Health/Network "
        "і Personal Automation завжди підтверджуються на конкретному iPhone/iPad. "
        "Якщо готовий Shortcut не підходить, ручний варіант і Health Auto Export описані "
        "в документації.\n"
        "\n"
        "userId і token не додавай у Body — вони вже є в URL з бота.\n"
        "\n"
        "Якщо випадково переслав URL або хочеш скинути доступ, запусти "
        "/connect_apple_health ще раз. Бот створить новий URL, а старий перестане працювати."
    ),
    "connect_whoop": (
        "⌚ Підключити WHOOP\n"
        "\n"
        "Сон, відновлення, активність — все буде доступно після авторизації.\n"
        "Посилання діє 1 годину.\n"
        "\n"
        "👉 {url}"
    ),
    "connect_fatsecret": (
        "🥗 Підключити FatSecret\n"
        "\n"
        "Щоденник їжі — синхронізується автоматично.\n"
        "Посилання діє 1 годину.\n"
        "\n"
        "👉 {url}"
    ),
    "connect_apple": (
        "❤️ Apple Health\n"
        "\n"
        "Apple Health не має backend API, тому дані має надсилати сам iPhone або iPad. "
        "Рекомендований шлях — імпортувати готовий iOS Shortcuts template.\n"
        "\n"
        "1) Готовий Shortcut для iOS Shortcuts (кроки, активні калорії, сон, "
        "HRV як стрес-проксі, пульс у спокої, дистанція, хвилини тренувань, вага):\n"
        "⚠️ Відкрий посилання саме на iPhone або iPad. На Mac дія "
        "Find Health Samples не підтримується.\n"
        "👉 {shortcut_import_url}\n"
        "Назва: Health Tracker Apple Health Sync\n"
        "Під час імпорту встав цей URL в Import Question:\n"
        "{shortcut_url}\n"
        "\n"
        "Після імпорту запусти Shortcut один раз на iPhone або iPad і дозволь доступ до "
        "Health/Network. Для авто-sync створи Personal Automation → Time of Day → "
        "Run Shortcut. Apple вимагає, щоб ця automation була налаштована на "
        "конкретному iPhone/iPad.\n"
        "\n"
        "2) Fallback з встановленням додатку: Health Auto Export — JSON+CSV. "
        "У ньому вкажи цей самий URL і Output: JSON (REST API). "
        "Так можна надсилати й тренування (Workouts).\n"
        "\n"
        "Не додавай userId/token у Request Body — вони вже є в URL. "
        "Бот показує цей URL лише один раз (на сервері зберігається тільки хеш). "
        "Якщо URL потрапив не туди, запусти /connect_apple_health ще раз: "
        "бот створить новий token, а старий URL перестане працювати."
    ),
    "whoop_connected": (
        "⌚ WHOOP підключено!\n"
        "\n"
        "✅ Дані доступні в реальному часі.\n"
        "Тепер можеш питати про сон, відновлення та тренування 💪"
    ),
    "fatsecret_connected": (
        "🥗 FatSecret підключено!\n"
        "\n"
        "✅ Тепер я бачу твій щоденник їжі.\n"
        "Можеш питати скільки калорій за сьогодні 📊"
    ),
    "whoop_expired": "⌚ WHOOP сесія закінчилась.\n\n🔑 Потрібно перепідключити → /connect_whoop",
    "fatsecret_expired": "🥗 FatSecret сесія закінчилась.\n\n🔑 Потрібно перепідключити → /connect_fatsecret",
    "voice_failed": "🎙 Не вдалося обробити голосове повідомлення. Спробуй ще раз.",
    "gpt_failed": "😔 Щось пішло не так. Спробуй ще раз через хвилинку.",
    "intent_failed": "😔 Виникла помилка при обробці запиту.",
    "burned": "  🔥 {total} спалено ({source})",
    "not_synced_fs": "\n⚠️ Не синхронізовано з FatSecret: {items}",
    "nothing_to_delete": "🤷 Немає записів для видалення.",
    "reconnect_header": "\n\n🔑 Сесія закінчилась, потрібно перепідключити:\n",
    "kg": "кг",
    "gym_logged": "✅ Записано:\n",
    "gym_prev": "\n    ↩️ Минулого разу ({date}): {parts}",
    "gym_need_exercise_last": "🏋️ Вкажи вправу. Наприклад: «що робив на жимі?»",
    "gym_no_records": "🏋️ Немає записів для «{key}». Запиши тренування — тоді покажу.",
    "gym_need_exercise_progress": "🏋️ Вкажи вправу. Наприклад: «прогрес присідань»",
    "gym_no_progress": "🏋️ Немає записів для «{key}». Потрібно мінімум 2 тренування для прогресу.",
    "gym_progress": "🏋️ Прогрес:\n",
    "gym_prompt_current": (
        "🏋️ Поточний gym промпт:\n{current}\n\n"
        "Щоб змінити: /gym_prompt <текст>\n"
        "Приклад: /gym_prompt Я тренуюсь для пауерліфтингу, фокус на базових вправах"
    ),
    "gym_prompt_unset": "не встановлено",
    "gym_prompt_set": "✅ Gym промпт встановлено:\n{text}",
    "journal_empty": "📓 Щоденник порожній. Просто напиши як справи!",
    "journal_title": "📓 Щоденник (7 днів):\n\n",
    "journal_no_week": "📓 Немає записів за останній тиждень.",
    "journal_status": (
        "📓 Нагадування щоденника: {status}\n"
        "  🌅 {t1}  🌙 {t2}\n\n"
        "Змінити: /journal_time 09:00 21:00\n"
        "Вимкнути: /journal_off\n"
        "Увімкнути: /journal_on"
    ),
    "enabled": "увімкнено",
    "disabled": "вимкнено",
    "journal_need_two": "Вкажи два часи: /journal_time 10:00 20:00",
    "journal_bad_format": "Невірний формат часу. Приклад: /journal_time 10:00 20:00",
    "journal_set": "✅ Нагадування: 🌅 {t1}  🌙 {t2}",
    "journal_off": "📓 Нагадування щоденника вимкнено.\nУвімкнути: /journal_on",
    "journal_on": "✅ Нагадування увімкнено: 🌅 {t1}  🌙 {t2}",
    "tz_current": "🕐 Твій часовий пояс: {tz}\nЗмінити: /timezone Europe/Kyiv (назва з бази IANA)",
    "tz_unknown": "❌ Невідомий часовий пояс. Приклади: Europe/Kyiv, Europe/Warsaw, America/New_York",
    "tz_set": (
        "✅ Часовий пояс оновлено: {tz}\n"
        "Статистика «за сьогодні» та щоденник FatSecret тепер рахуються в ньому."
    ),
    "lang_current": "🌐 Мова: українська\nЗмінити: /language en",
    "lang_set": "✅ Мову змінено на українську.",
    "lang_unknown": "❌ Підтримуються: /language uk або /language en",
    "profile_current": (
        "🧬 Профіль для BMR\n"
        "Рік народження: {birth_year}\nСтать: {sex}\nЗріст: {height}\nВага: {weight}\n"
        "BMR: {bmr}\n\n"
        "Змінити: /profile 1990 m 180  (рік, m/f, зріст у см)\n"
        "Вага береться з Apple Health або WHOOP."
    ),
    "profile_set": "✅ Профіль збережено. BMR: {bmr}",
    "profile_bad": "❌ Формат: /profile 1990 m 180  (рік народження, m або f, зріст у см)",
    "not_set": "не задано",
    "male": "чоловіча",
    "female": "жіноча",
    "bmr_value": "{kcal} ккал/день",
    "bmr_need_weight": "потрібна вага з Apple Health / WHOOP",
    "sync_checking": "🔄 Перевіряю з'єднання...",
    "sync_done": "✅ Перевірка завершена\n\n",
    "sync_whoop_expired": "⌚ WHOOP — 🔑 сесія закінчилась → /connect_whoop",
    "sync_whoop_ok": "⌚ WHOOP — ✅ {parts}",
    "sync_whoop_burned": "{kcal} kcal спалено",
    "sync_whoop_pending": "⌚ WHOOP — ✅ підключено (дані ще збираються)",
    "sync_whoop_off": "⌚ WHOOP — ⚠️ не підключено",
    "sync_apple_counts": "❤️ Apple Health — ✅ імпортовано сьогодні: {counts}",
    "sync_apple_latest": "; останній показник {latest}",
    "sync_apple_last_sync": (
        "❤️ Apple Health — ✅ остання синхронізація {last}, показників за сьогодні ще немає"
    ),
    "sync_apple_waiting": "❤️ Apple Health — ✅ підключено, очікую перший sync",
    "sync_apple_off": "❤️ Apple Health — ⚠️ не підключено",
    "sync_apple_workouts": "; тренувань сьогодні: {count}",
    "sync_fs_expired": "🥗 FatSecret — 🔑 сесія закінчилась → /connect_fatsecret",
    "sync_fs_ok": "🥗 FatSecret — ✅ {kcal} kcal сьогодні",
    "sync_fs_off": "🥗 FatSecret — ⚠️ не підключено",
    "ah_title": "📊 Apple Health синхронізовано",
    "ah_received": "Отримано {received} семплів, агреговано {aggregated}.",
    "ah_rows": (
        "Рядків сімейств оновлено: {updated}; повторів без змін: {replayed}; застарілих: {stale}."
    ),
    "ah_breakdown": "Отримано за типами: {breakdown}.",
    "ah_no_metrics": "немає метрик",
    "ah_raw": "Сирих семплів збережено: {raw} (зберігаються лише добові підсумки).",
    "ah_days": "Дні: {days}.",
    "ah_failed": "⚠️ Помилок: {failed}.",
    "ah_unmapped": "ℹ️ Непідтримувані типи не збережено: {types}.",
    "ah_workouts": "🏃 Тренувань: {received} (нових {inserted}, оновлено {updated}).",
    "reminder_morning": "🌅 Доброго ранку!",
    "reminder_morning_q": "\nЯк настрій? Які плани на день?",
    "reminder_evening": "🌙 Як пройшов день?",
    "reminder_evening_q": "\nОпиши як себе почуваєш.",
    "reminder_burned": ", 🔥 {kcal} спалено",
    "reminder_steps": "👣 {steps} кроків",
    "reminder_hrv": "🧘 HRV (стрес-проксі): {hrv} ms",
    "reminder_apple_sleep": "❤️ Apple sleep: {hours}h",

    # --- Food logging (history, barcode, label, Web App) ---
    "food_added_header": "✅ Додано:",
    "food_entry_line": "• {name} — {grams} г, {kcal} ккал",
    "food_sync_synced": " · FatSecret ✓",
    "food_sync_pending": " · FatSecret ⏳",
    "food_sync_failed": " · FatSecret ✗ (збережено тут)",
    "food_sync_unknown": " · FatSecret: перевіряю",
    "food_sync_not_supported": " · лише тут (немає відповідника у FatSecret)",
    "food_daily_total": "📊 {total} / {goal} ккал за сьогодні",
    "food_partial": " (неповні дані)",
    "food_why_default": "за твоїм правилом «{alias}»",
    "food_why_learned": "як минулого разу для «{alias}»",
    "food_why_confirmed": "з «Моїх продуктів»",
    "food_choose": "Уточни продукт для «{text}»:",
    "food_need_grams": "Скільки грамів «{name}»? Відповідай на це повідомлення, напр. «135 г».",
    "food_btn_undo": "↩️ Скасувати",
    "food_btn_cancel": "✖️ Відмінити",
    "food_btn_save_label": "💾 Зберегти продукт і записати",
    "food_btn_open_app": "📱 Відкрити в застосунку",
    "food_undone": "↩️ Скасовано: {name}",
    "food_cancelled": "✖️ Чернетку скасовано.",
    "food_draft_outdated": "Це повідомлення застаріло — ось актуальний стан.",
    "food_draft_closed": "Цей запис уже закрито.",
    "food_nothing_found": "Не знайшов «{text}». Вкажи точнішу назву або надішли фото етикетки.",
    "food_nutrition_missing": "Для «{name}» немає даних про калорійність на грам. Надішли фото етикетки або заповни продукт у застосунку.",
    "food_saved_card_unusable": "У «{name}» зі збережених немає калорій на грам. Обери варіант із пошуку:",
    "food_volume": "Для «{name}» потрібна вага в грамах — мілілітри не перетворюю без щільності.",
    "food_which_draft": "У тебе кілька незавершених записів — відповідай (reply) на потрібне повідомлення.",
    "food_grams_invalid": "Некоректна вага. Вкажи грами, напр. «135 г».",
    "food_barcode_invalid": "Штрихкод не пройшов перевірку ({reason}). Сфотографуй ближче або надішли цифри.",
    "food_barcode_restricted": "Це внутрішній код магазину або ваговий товар — його не можна однозначно визначити. Надішли фото етикетки.",
    "food_barcode_unreadable": "Не вдалося прочитати штрихкод. Сфотографуй ближче або надішли цифри текстом.",
    "food_barcode_unknown": "Товар зі штрихкодом {code} не знайдено. Надішли фото етикетки з харчовою цінністю — відповіддю на це повідомлення.",
    "food_label_extracted": "🏷 {name}\nНа {basis}: {kcal} ккал, Б {protein} г, Ж {fat} г, В {carbs} г{missing}",
    "food_label_missing": "\nНе видно: {fields}",
    "food_label_unusable": "Не видно енергетичної цінності або бази (на 100 г / порцію з вагою) — не можу порахувати. Спробуй чіткіше фото.",
    "food_label_per_ml": "Значення вказані на 100 мл — для розрахунку потрібна вага в грамах. Заповни продукт у застосунку.",
    "food_label_saved": "💾 Продукт «{name}» збережено в «Мої продукти».",
    "food_per_serving": "порцію",
    "food_unnamed": "Продукт без назви",
    "food_photo_other": "Не бачу на фото їжі, етикетки чи штрихкоду.",
    "food_photo_failed": "Не вдалося обробити фото. Спробуй ще раз.",
    "food_image_too_large": "Зображення завелике (до 10 МБ) або пошкоджене.",
    "food_plate_disabled": "Розпізнавання страв з фото поки вимкнене. Напиши, що з'їв і скільки грамів.",
    "food_plate_split": "На фото кілька компонентів ({items}), а вага одна ({grams} г). Вкажи вагу кожного, напр. «рис 200 г, курка 150 г».",
    "food_source_fatsecret": "FatSecret",
    "food_source_off": "Open Food Facts",
    "food_source_label": "етикетка",
    "food_source_manual": "вручну",
    "food_import_started": "🔄 Додаю в «Мої продукти» їжу з твоєї історії FatSecret за {days} днів. Це відбувається у фоні.",
    "app_open": "📱 Застосунок: щоденник, «Мої продукти», продукти за замовчуванням і налаштування.",
    "app_unavailable": "Застосунок ще не налаштований (потрібна HTTPS-адреса WEBAPP_URL).",
    "app_button": "📱 Відкрити",
}

_EN: dict[str, str] = {
    "help": (
        "👋 Hi! I'm your personal health assistant.\n"
        "\n"
        "🍎 What I can do:\n"
        "  ▸ Log food — just tell me what you ate\n"
        "     e.g. “200g chicken breast with rice”\n"
        "  ▸ 🏋️ Log gym sets — “bench 80kg 3×8”\n"
        "  ▸ 📊 Last session — “what did I bench last time?”\n"
        "  ▸ 📈 Progress — “squat progress”\n"
        "  ▸ 📓 Journal — just describe how you feel\n"
        "  ▸ 📓 History — “show my journal”\n"
        "  ▸ 🎙 Voice messages — say what you ate or did\n"
        "  ▸ 📷 Barcode or nutrition-label photo + weight, e.g. “135 g”\n"
        "  ▸ 🔁 I remember your FatSecret foods and previous choices\n"
        "  ▸ 📊 Daily calories from FatSecret + WHOOP / Apple Health\n"
        "  ▸ 😴 WHOOP data — sleep, recovery, workouts\n"
        "  ▸ 🗑 Delete last entry — “delete the last one”\n"
        "  ▸ 🎯 Set a goal — “set my goal to 2500 kcal”\n"
        "\n"
        "🔗 Connect services:\n"
        "  ⌚ WHOOP → /connect_whoop\n"
        "  ❤️ Apple Health → /connect_apple_health\n"
        "  🧭 Apple Health guide → /apple_health_help\n"
        "  🥗 FatSecret → /connect_fatsecret\n"
        "  🔄 Check connections → /sync\n"
        "  🕐 Timezone → /timezone\n"
        "  🧬 BMR profile → /profile\n"
        "  🌐 Language → /language\n"
        "  🏋️ Gym prompt → /gym_prompt\n"
        "  📓 Journal → /journal, /journal_time, /journal_off, /journal_on\n"
        "  📱 Web App → /app\n"
        "\n"
        "⏰ Auto summaries: 08:00 🌅 and 21:00 🌙 (your timezone)\n"
        "\n"
        "Just talk to me like a friend — I understand 🇺🇦 and 🇬🇧!"
    ),
    "apple_help": (
        "🧭 How to connect Apple Health\n"
        "\n"
        "Recommended: the ready-made Shortcut for Apple Shortcuts on iPhone or iPad. "
        "Shortcuts is built in — nothing else to install.\n"
        "\n"
        "The Shortcut syncs: 👣 steps, 🔥 active calories, 😴 sleep, 🧘 HRV "
        "(heart-rate variability — used as a stress proxy: lower than usual HRV ≈ "
        "higher stress), 💓 resting heart rate, 🚶 walking/running distance, "
        "🏃 exercise minutes, and ⚖️ weight (for the basal metabolic rate estimate).\n"
        "\n"
        "1. In Telegram, send /connect_apple_health.\n"
        "2. Open the `Health Tracker Apple Health Sync` Shortcut on an iPhone or iPad. "
        "Find Health Samples is not supported on Mac.\n"
        "3. When importing, paste the URL from the bot into the Import Question.\n"
        "4. Run the Shortcut once and allow Health and Network access. "
        "Health asks separately for each data type (steps, calories, sleep, HRV, "
        "resting HR, distance, exercise minutes, weight) — allow all you want to sync.\n"
        "5. In Shortcuts → Automation, create a Time of Day Personal Automation that "
        "runs this Shortcut regularly.\n"
        "\n"
        "After the first run, check the status with /sync.\n"
        "\n"
        "🏃 Workouts are accepted from Health Auto Export or from a Shortcut that "
        "adds a workouts array — see the documentation.\n"
        "\n"
        "⚠️ If you imported the Shortcut before, delete it, import it again from the "
        "same link, and allow Health access again — otherwise the bot keeps receiving "
        "only the old set of metrics.\n"
        "\n"
        "Apple does not allow fully zero-touch sync: the Shortcut import, Health/Network "
        "permissions, and the Personal Automation are always confirmed on the device. "
        "A manual setup and Health Auto Export are described in the documentation.\n"
        "\n"
        "Do not add userId or token to the Body — they are already in the bot's URL.\n"
        "\n"
        "If you shared the URL by mistake or want to reset access, run "
        "/connect_apple_health again. The bot creates a new URL and the old one stops working."
    ),
    "connect_whoop": (
        "⌚ Connect WHOOP\n"
        "\n"
        "Sleep, recovery, and activity become available after authorization.\n"
        "The link is valid for 1 hour.\n"
        "\n"
        "👉 {url}"
    ),
    "connect_fatsecret": (
        "🥗 Connect FatSecret\n"
        "\n"
        "Your food diary syncs automatically.\n"
        "The link is valid for 1 hour.\n"
        "\n"
        "👉 {url}"
    ),
    "connect_apple": (
        "❤️ Apple Health\n"
        "\n"
        "Apple Health has no backend API, so your iPhone or iPad sends the data itself. "
        "Recommended: import the ready iOS Shortcuts template.\n"
        "\n"
        "1) Ready Shortcut for iOS Shortcuts (steps, active calories, sleep, "
        "HRV as a stress proxy, resting HR, distance, exercise minutes, weight):\n"
        "⚠️ Open the link on an iPhone or iPad. Find Health Samples is not "
        "supported on Mac.\n"
        "👉 {shortcut_import_url}\n"
        "Name: Health Tracker Apple Health Sync\n"
        "Paste this URL into the Import Question:\n"
        "{shortcut_url}\n"
        "\n"
        "After importing, run the Shortcut once and allow Health/Network access. "
        "For automatic sync create Personal Automation → Time of Day → Run Shortcut. "
        "Apple requires this automation to be set up on the device.\n"
        "\n"
        "2) App-based fallback: Health Auto Export — JSON+CSV. Use the same URL and "
        "Output: JSON (REST API). It can also send Workouts.\n"
        "\n"
        "Do not add userId/token to the Request Body — they are already in the URL. "
        "The bot shows this URL only once (the server keeps only a hash). "
        "If the URL leaks, run /connect_apple_health again: a new token is issued "
        "and the old URL stops working."
    ),
    "whoop_connected": (
        "⌚ WHOOP connected!\n"
        "\n"
        "✅ Data is available in real time.\n"
        "Ask me about sleep, recovery, and workouts 💪"
    ),
    "fatsecret_connected": (
        "🥗 FatSecret connected!\n"
        "\n"
        "✅ I can see your food diary now.\n"
        "Ask how many calories you've had today 📊"
    ),
    "whoop_expired": "⌚ Your WHOOP session expired.\n\n🔑 Please reconnect → /connect_whoop",
    "fatsecret_expired": "🥗 Your FatSecret session expired.\n\n🔑 Please reconnect → /connect_fatsecret",
    "voice_failed": "🎙 Couldn't process the voice message. Please try again.",
    "gpt_failed": "😔 Something went wrong. Please try again in a minute.",
    "intent_failed": "😔 Something went wrong while handling your request.",
    "burned": "  🔥 {total} burned ({source})",
    "not_synced_fs": "\n⚠️ Not synced to FatSecret: {items}",
    "nothing_to_delete": "🤷 Nothing to delete.",
    "reconnect_header": "\n\n🔑 Session expired, please reconnect:\n",
    "kg": "kg",
    "gym_logged": "✅ Logged:\n",
    "gym_prev": "\n    ↩️ Last time ({date}): {parts}",
    "gym_need_exercise_last": "🏋️ Name the exercise, e.g. “what did I bench last time?”",
    "gym_no_records": "🏋️ No records for “{key}” yet. Log a session and I'll show it.",
    "gym_need_exercise_progress": "🏋️ Name the exercise, e.g. “squat progress”",
    "gym_no_progress": "🏋️ No records for “{key}”. At least 2 sessions are needed for progress.",
    "gym_progress": "🏋️ Progress:\n",
    "gym_prompt_current": (
        "🏋️ Current gym prompt:\n{current}\n\n"
        "To change: /gym_prompt <text>\n"
        "Example: /gym_prompt I train for powerlifting, focus on compound lifts"
    ),
    "gym_prompt_unset": "not set",
    "gym_prompt_set": "✅ Gym prompt saved:\n{text}",
    "journal_empty": "📓 Your journal is empty. Just tell me how you're doing!",
    "journal_title": "📓 Journal (7 days):\n\n",
    "journal_no_week": "📓 No entries in the last week.",
    "journal_status": (
        "📓 Journal reminders: {status}\n"
        "  🌅 {t1}  🌙 {t2}\n\n"
        "Change: /journal_time 09:00 21:00\n"
        "Disable: /journal_off\n"
        "Enable: /journal_on"
    ),
    "enabled": "on",
    "disabled": "off",
    "journal_need_two": "Give two times: /journal_time 10:00 20:00",
    "journal_bad_format": "Invalid time format. Example: /journal_time 10:00 20:00",
    "journal_set": "✅ Reminders: 🌅 {t1}  🌙 {t2}",
    "journal_off": "📓 Journal reminders disabled.\nEnable: /journal_on",
    "journal_on": "✅ Reminders enabled: 🌅 {t1}  🌙 {t2}",
    "tz_current": "🕐 Your timezone: {tz}\nChange: /timezone Europe/Kyiv (IANA name)",
    "tz_unknown": "❌ Unknown timezone. Examples: Europe/Kyiv, Europe/Warsaw, America/New_York",
    "tz_set": (
        "✅ Timezone updated: {tz}\n"
        "“Today” stats and the FatSecret diary now use it."
    ),
    "lang_current": "🌐 Language: English\nChange: /language uk",
    "lang_set": "✅ Language switched to English.",
    "lang_unknown": "❌ Supported: /language uk or /language en",
    "profile_current": (
        "🧬 BMR profile\n"
        "Birth year: {birth_year}\nSex: {sex}\nHeight: {height}\nWeight: {weight}\n"
        "BMR: {bmr}\n\n"
        "Change: /profile 1990 m 180  (year, m/f, height in cm)\n"
        "Weight comes from Apple Health or WHOOP."
    ),
    "profile_set": "✅ Profile saved. BMR: {bmr}",
    "profile_bad": "❌ Format: /profile 1990 m 180  (birth year, m or f, height in cm)",
    "not_set": "not set",
    "male": "male",
    "female": "female",
    "bmr_value": "{kcal} kcal/day",
    "bmr_need_weight": "needs weight from Apple Health / WHOOP",
    "sync_checking": "🔄 Checking connections...",
    "sync_done": "✅ Check complete\n\n",
    "sync_whoop_expired": "⌚ WHOOP — 🔑 session expired → /connect_whoop",
    "sync_whoop_ok": "⌚ WHOOP — ✅ {parts}",
    "sync_whoop_burned": "{kcal} kcal burned",
    "sync_whoop_pending": "⌚ WHOOP — ✅ connected (data still coming in)",
    "sync_whoop_off": "⌚ WHOOP — ⚠️ not connected",
    "sync_apple_counts": "❤️ Apple Health — ✅ imported today: {counts}",
    "sync_apple_latest": "; latest value {latest}",
    "sync_apple_last_sync": "❤️ Apple Health — ✅ last sync {last}, no values for today yet",
    "sync_apple_waiting": "❤️ Apple Health — ✅ connected, waiting for the first sync",
    "sync_apple_off": "❤️ Apple Health — ⚠️ not connected",
    "sync_apple_workouts": "; workouts today: {count}",
    "sync_fs_expired": "🥗 FatSecret — 🔑 session expired → /connect_fatsecret",
    "sync_fs_ok": "🥗 FatSecret — ✅ {kcal} kcal today",
    "sync_fs_off": "🥗 FatSecret — ⚠️ not connected",
    "ah_title": "📊 Apple Health synced",
    "ah_received": "Received {received} samples, aggregated {aggregated}.",
    "ah_rows": "Family rows updated: {updated}; unchanged replays: {replayed}; stale: {stale}.",
    "ah_breakdown": "Received by type: {breakdown}.",
    "ah_no_metrics": "no metrics",
    "ah_raw": "Raw samples stored: {raw} (only daily totals are kept).",
    "ah_days": "Days: {days}.",
    "ah_failed": "⚠️ Errors: {failed}.",
    "ah_unmapped": "ℹ️ Unsupported types not stored: {types}.",
    "ah_workouts": "🏃 Workouts: {received} (new {inserted}, updated {updated}).",
    "reminder_morning": "🌅 Good morning!",
    "reminder_morning_q": "\nHow are you feeling? What are your plans for today?",
    "reminder_evening": "🌙 How was your day?",
    "reminder_evening_q": "\nTell me how you feel.",
    "reminder_burned": ", 🔥 {kcal} burned",
    "reminder_steps": "👣 {steps} steps",
    "reminder_hrv": "🧘 HRV (stress proxy): {hrv} ms",
    "reminder_apple_sleep": "❤️ Apple sleep: {hours}h",

    # --- Food logging (history, barcode, label, Web App) ---
    "food_added_header": "✅ Added:",
    "food_entry_line": "• {name} — {grams} g, {kcal} kcal",
    "food_sync_synced": " · FatSecret ✓",
    "food_sync_pending": " · FatSecret ⏳",
    "food_sync_failed": " · FatSecret ✗ (saved here)",
    "food_sync_unknown": " · FatSecret: verifying",
    "food_sync_not_supported": " · here only (no FatSecret match)",
    "food_daily_total": "📊 {total} / {goal} kcal today",
    "food_partial": " (partial data)",
    "food_why_default": "your rule for “{alias}”",
    "food_why_learned": "same as last time for “{alias}”",
    "food_why_confirmed": "from My Products",
    "food_choose": "Which product is “{text}”?",
    "food_need_grams": "How many grams of “{name}”? Reply to this message, e.g. “135 g”.",
    "food_btn_undo": "↩️ Undo",
    "food_btn_cancel": "✖️ Cancel",
    "food_btn_save_label": "💾 Save product and log",
    "food_btn_open_app": "📱 Open in app",
    "food_undone": "↩️ Undone: {name}",
    "food_cancelled": "✖️ Draft cancelled.",
    "food_draft_outdated": "This message is outdated — here is the current state.",
    "food_draft_closed": "This entry is already closed.",
    "food_nothing_found": "Couldn't find “{text}”. Try a more precise name or send a label photo.",
    "food_nutrition_missing": "No per-gram calorie data for “{name}”. Send a label photo or complete the product in the app.",
    "food_saved_card_unusable": "Saved “{name}” has no calories per gram. Choose a search result:",
    "food_volume": "“{name}” needs a weight in grams — I don't convert millilitres without a density.",
    "food_which_draft": "You have several unfinished entries — reply to the message you mean.",
    "food_grams_invalid": "Invalid weight. Send grams, e.g. “135 g”.",
    "food_barcode_invalid": "The barcode failed validation ({reason}). Take a closer photo or send the digits.",
    "food_barcode_restricted": "This is an in-store / variable-weight code and cannot identify a product. Send a label photo.",
    "food_barcode_unreadable": "Couldn't read the barcode. Take a closer photo or send the digits as text.",
    "food_barcode_unknown": "No product found for barcode {code}. Send a photo of the nutrition label — as a reply to this message.",
    "food_label_extracted": "🏷 {name}\nPer {basis}: {kcal} kcal, P {protein} g, F {fat} g, C {carbs} g{missing}",
    "food_label_missing": "\nNot visible: {fields}",
    "food_label_unusable": "Energy or the basis (per 100 g / per serving with weight) isn't visible — I can't calculate. Try a sharper photo.",
    "food_label_per_ml": "Values are per 100 ml — a weight in grams is needed. Complete the product in the app.",
    "food_label_saved": "💾 Product “{name}” saved to My Products.",
    "food_per_serving": "serving",
    "food_unnamed": "Unnamed product",
    "food_photo_other": "I don't see food, a label or a barcode in this photo.",
    "food_photo_failed": "Couldn't process the photo. Please try again.",
    "food_image_too_large": "The image is too large (max 10 MB) or damaged.",
    "food_plate_disabled": "Dish photo recognition is off for now. Tell me what you ate and how many grams.",
    "food_plate_split": "The photo has several components ({items}) but one weight ({grams} g). Send each weight, e.g. “rice 200 g, chicken 150 g”.",
    "food_source_fatsecret": "FatSecret",
    "food_source_off": "Open Food Facts",
    "food_source_label": "label",
    "food_source_manual": "manual",
    "food_import_started": "🔄 Adding foods from your last {days} days of FatSecret history to My Products. This runs in the background.",
    "app_open": "📱 Web App: diary, My Products, default products and settings.",
    "app_unavailable": "The Web App isn't configured yet (an HTTPS WEBAPP_URL is required).",
    "app_button": "📱 Open",
}

MESSAGES: dict[str, dict[str, str]] = {"uk": _UK, "en": _EN}


def t(key: str, lang: Optional[str] = None, **kwargs: Any) -> str:
    """Translate ``key``; falls back to Ukrainian, then to the key itself."""
    catalog = MESSAGES.get(normalize_language(lang), _UK)
    template = catalog.get(key) or _UK.get(key) or key
    return template.format(**kwargs) if kwargs else template
