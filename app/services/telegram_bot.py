from __future__ import annotations

import logging
from decimal import Decimal

from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.config import settings
from app.database import get_pool
from app.services.ai_assistant import (
    classify_and_respond,
    save_conversation_message,
    transcribe_voice,
    get_today_stats,
)
from app.services.fatsecret_api import (
    search_food,
    get_food_servings,
    create_food_diary_entry,
)
from app.services.gym_service import (
    log_exercises,
    get_last_exercise,
    get_exercise_progress,
)
from app.services.journal_service import (
    save_journal_entry,
    get_journal_history,
    get_journal_summary_data,
)
from app.services.apple_health import ensure_apple_health_sync

logger = logging.getLogger(__name__)

_application: Application | None = None


async def send_message(telegram_user_id: int, text: str) -> None:
    """Send a message to a user via the bot. Used by OAuth callbacks."""
    if _application is None:
        logger.warning("Bot not started, cannot send message to %s", telegram_user_id)
        return
    await _application.bot.send_message(
        chat_id=telegram_user_id, text=text, disable_web_page_preview=True,
    )


def _is_pure_gram_serving(desc: str) -> bool:
    """Check if serving description is a pure gram amount like '100g' or '1 g'."""
    d = desc.strip().lower()
    if not d.endswith("g"):
        return False
    try:
        float(d[:-1].strip())
        return True
    except ValueError:
        return False


def _parse_fatsecret_description(description: str) -> dict:
    """Parse FatSecret food_description string into numeric values.

    Example: "Per 100g - Calories: 165kcal | Fat: 3.57g | Carbs: 0.00g | Protein: 31.02g"
    """
    result = {"calories": 0.0, "fat": 0.0, "carbs": 0.0, "protein": 0.0, "serving_size": 100.0}
    if not description:
        return result

    try:
        if " - " not in description:
            return result
        serving_part, nutrients_part = description.split(" - ", 1)

        # Parse "Per XXg"
        serving_part = serving_part.replace("Per ", "")
        for unit in ("g", "ml", "oz"):
            if unit in serving_part.lower():
                num_str = serving_part.lower().replace(unit, "").strip()
                try:
                    result["serving_size"] = float(num_str) if num_str else 100.0
                except ValueError:
                    result["serving_size"] = 100.0
                break

        for part in nutrients_part.split("|"):
            part = part.strip()
            if "Calories:" in part:
                result["calories"] = float(part.split(":")[1].replace("kcal", "").strip())
            elif "Fat:" in part:
                result["fat"] = float(part.split(":")[1].replace("g", "").strip())
            elif "Carbs:" in part:
                result["carbs"] = float(part.split(":")[1].replace("g", "").strip())
            elif "Protein:" in part:
                result["protein"] = float(part.split(":")[1].replace("g", "").strip())
    except (ValueError, IndexError):
        logger.warning("Failed to parse FatSecret description: %s", description)

    return result


async def _ensure_user(telegram_user_id: int, username: str | None) -> dict:
    """Get or create user by telegram_user_id."""
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT id, daily_calorie_goal FROM users WHERE telegram_user_id = $1",
        telegram_user_id,
    )
    if row:
        return {"id": row["id"], "daily_calorie_goal": row["daily_calorie_goal"]}

    row = await pool.fetchrow(
        """INSERT INTO users (telegram_user_id, telegram_username)
           VALUES ($1, $2)
           RETURNING id, daily_calorie_goal""",
        telegram_user_id,
        username or "",
    )
    logger.info("Created new user: telegram_user_id=%s, db_id=%s", telegram_user_id, row["id"])
    return {"id": row["id"], "daily_calorie_goal": row["daily_calorie_goal"]}


async def _handle_log_food(user_id: int, food_items: list[dict]) -> dict:
    """Look up each food item in FatSecret, store in food_entries, sync to FatSecret diary.

    Returns dict with 'items' list and 'fs_connected' flag.
    """
    pool = await get_pool()
    logged = []

    # Check if user has FatSecret connected for two-way sync
    user_row = await pool.fetchrow(
        "SELECT fatsecret_access_token, fatsecret_access_secret FROM users WHERE id = $1",
        user_id,
    )
    fs_token = user_row["fatsecret_access_token"] if user_row else ""
    fs_secret = user_row["fatsecret_access_secret"] if user_row else ""
    fs_connected = bool(fs_token and fs_secret)

    for item in food_items:
        name_en = item.get("name_en", "")
        name_original = item.get("name_original", name_en)
        quantity_g = item.get("quantity_g", 100)
        meal_type = item.get("meal_type", "snack")

        if meal_type not in ("breakfast", "lunch", "dinner", "snack"):
            meal_type = "snack"

        calories = 0.0
        protein = 0.0
        fat = 0.0
        carbs = 0.0
        food_id = ""
        food_name_fs = ""

        try:
            result = await search_food(name_en, max_results=1)
            foods = result.get("results", [])
            if foods:
                food_id = foods[0].get("food_id", "")
                food_name_fs = foods[0].get("name", name_en)
                nutrients = _parse_fatsecret_description(foods[0].get("description", ""))
                desc_serving_size = nutrients.get("serving_size", 100.0) or 100.0
                factor = quantity_g / desc_serving_size
                calories = round(nutrients["calories"] * factor, 1)
                protein = round(nutrients["protein"] * factor, 1)
                fat = round(nutrients["fat"] * factor, 1)
                carbs = round(nutrients["carbs"] * factor, 1)
        except Exception:
            logger.warning("FatSecret lookup failed for '%s'", name_en)

        # Sync to FatSecret diary if connected (FatSecret is source of truth)
        synced_to_fs = False
        if fs_connected and food_id:
            try:
                servings = await get_food_servings(food_id)
                if servings:
                    # Filter out derived servings (serving_id=0) — they
                    # cannot be used with food_entry.create
                    real_servings = [
                        s for s in servings
                        if str(s["serving_id"]) != "0"
                    ]
                    if not real_servings:
                        real_servings = servings  # fallback if all derived

                    # All gram-based real servings
                    all_gram = [
                        s for s in real_servings
                        if s["metric_serving_unit"] == "g"
                        and s["metric_serving_amount"] > 0
                    ]
                    # Pure gram servings (description like "1g", "100g")
                    pure_gram = [
                        s for s in all_gram
                        if _is_pure_gram_serving(s["description"])
                    ]
                    # 1g serving: search ALL gram servings
                    one_g = next(
                        (s for s in all_gram if s["metric_serving_amount"] == 1.0),
                        None,
                    )
                    hundred_g = next(
                        (s for s in pure_gram if s["metric_serving_amount"] == 100.0),
                        None,
                    )
                    if one_g:
                        serving = one_g
                    elif hundred_g:
                        serving = hundred_g
                    elif pure_gram:
                        serving = min(pure_gram, key=lambda s: s["metric_serving_amount"])
                    elif all_gram:
                        serving = all_gram[0]
                    else:
                        serving = real_servings[0]
                    if not one_g:
                        logger.warning(
                            "No 1g serving for food_id=%s, using %s (%.1fg). "
                            "Available: %s",
                            food_id, serving["description"],
                            serving["metric_serving_amount"],
                            ", ".join(
                                f"{s['description']}(id={s['serving_id']}, "
                                f"{s['metric_serving_amount']}g, "
                                f"units={s['number_of_units']})"
                                for s in servings[:10]
                            ),
                        )
                    # FatSecret number_of_units = base units in the serving.
                    # E.g. "100 g" → number_of_units=100 (100 units of 1g).
                    # To log X grams: send (X / metric_serving_amount) * number_of_units.
                    metric_amount = serving["metric_serving_amount"] or 100.0
                    serving_units = serving.get("number_of_units", 1.0) or 1.0
                    units = (quantity_g / metric_amount) * serving_units
                    logger.info(
                        "FatSecret sync: food_id=%s serving=%s metric=%sg "
                        "serving_units=%.1f units=%.2f for %dg",
                        food_id, serving["description"], metric_amount,
                        serving_units, units, quantity_g,
                    )
                    await create_food_diary_entry(
                        access_token=fs_token,
                        access_secret=fs_secret,
                        food_id=food_id,
                        food_entry_name=food_name_fs,
                        serving_id=serving["serving_id"],
                        number_of_units=round(units, 2),
                        meal_type=meal_type,
                    )
                    synced_to_fs = True
            except Exception:
                logger.warning("Failed to sync '%s' to FatSecret diary", name_en)

        # Only store locally if not synced to FatSecret (fallback)
        if not synced_to_fs:
            await pool.execute(
                """INSERT INTO food_entries
                       (user_id, food_name, calories, protein, fat, carbs,
                        serving_size, serving_unit, meal_type, source_text)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::meal_type, $10)""",
                user_id,
                name_original,
                Decimal(str(calories)),
                Decimal(str(protein)),
                Decimal(str(fat)),
                Decimal(str(carbs)),
                Decimal(str(quantity_g)),
                "g",
                meal_type,
                name_en,
            )

        logged.append({
            "name": name_original,
            "calories": round(calories),
            "synced_to_fs": synced_to_fs,
        })

    return {"items": logged, "fs_connected": fs_connected}


async def _handle_delete_entry(user_id: int) -> str | None:
    """Delete the most recent food entry. Returns deleted food description or None."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """DELETE FROM food_entries
           WHERE id = (
               SELECT id FROM food_entries
               WHERE user_id = $1
               ORDER BY created_at DESC
               LIMIT 1
           )
           RETURNING food_name, calories""",
        user_id,
    )
    if row:
        return f"{row['food_name']} ({row['calories']} kcal)"
    return None


async def _handle_gym(user_id: int, gpt_result: dict) -> str | None:
    """Handle gym intent: log exercises, show last workout, show progress."""
    action = gpt_result.get("gym_action", "log")

    if action == "log" and gpt_result.get("exercises"):
        logged = await log_exercises(user_id, gpt_result["exercises"])
        lines = []
        for ex in logged:
            line = f"  {ex['name']}"
            parts = []
            if ex.get("weight_kg"):
                parts.append(f"{ex['weight_kg']}кг")
            if ex.get("sets") and ex.get("reps"):
                parts.append(f"{ex['sets']}×{ex['reps']}")
            if parts:
                line += f" — {', '.join(parts)}"
            if ex.get("prev"):
                p = ex["prev"]
                prev_parts = []
                if p.get("weight_kg"):
                    prev_parts.append(f"{p['weight_kg']}кг")
                if p.get("sets") and p.get("reps"):
                    prev_parts.append(f"{p['sets']}×{p['reps']}")
                if prev_parts:
                    line += f"\n    ↩️ Минулого разу ({p['date']}): {', '.join(prev_parts)}"
            lines.append(line)
        return "✅ Записано:\n" + "\n".join(lines)

    elif action == "last":
        key = gpt_result.get("exercise_key", "")
        if not key:
            return "🏋️ Вкажи вправу. Наприклад: «що робив на жимі?»"
        ex = await get_last_exercise(user_id, key)
        if not ex:
            return f"🏋️ Немає записів для «{key}». Запиши тренування — тоді покажу."
        # GPT already has recent gym context and generated a response
        return None

    elif action == "progress":
        key = gpt_result.get("exercise_key", "")
        if not key:
            return "🏋️ Вкажи вправу. Наприклад: «прогрес присідань»"
        history = await get_exercise_progress(user_id, key)
        if not history:
            return f"🏋️ Немає записів для «{key}». Потрібно мінімум 2 тренування для прогресу."
        lines = []
        for entry in history:
            date_str = entry["created_at"].strftime("%d.%m")
            parts = []
            if entry.get("weight_kg"):
                parts.append(f"{entry['weight_kg']}кг")
            if entry.get("sets") and entry.get("reps"):
                parts.append(f"{entry['sets']}×{entry['reps']}")
            lines.append(f"  {date_str} — {', '.join(parts)}")
        if len(history) >= 2 and history[0].get("weight_kg") and history[-1].get("weight_kg"):
            first_w = history[0]["weight_kg"]
            last_w = history[-1]["weight_kg"]
            diff = last_w - first_w
            pct = round(diff / first_w * 100, 1) if first_w else 0
            sign = "+" if diff >= 0 else ""
            lines.append(f"\n  📈 {sign}{diff}кг ({sign}{pct}%)")
        return "🏋️ Прогрес:\n" + "\n".join(lines)

    return None


async def _handle_calorie_goal(user_id: int, calorie_goal: int) -> None:
    """Update user's daily calorie goal."""
    pool = await get_pool()
    await pool.execute(
        "UPDATE users SET daily_calorie_goal = $1 WHERE id = $2",
        calorie_goal,
        user_id,
    )


async def _handle_journal(user_id: int, gpt_result: dict, message_text: str) -> str | None:
    """Handle journal intent: save entry, show history, show summary."""
    action = gpt_result.get("journal_action", "entry")

    if action == "entry":
        je = gpt_result.get("journal_entry") or {}
        await save_journal_entry(
            user_id=user_id,
            content=message_text,
            mood_score=je.get("mood_score"),
            energy_level=je.get("energy_level"),
            tags=je.get("tags"),
        )
        # GPT already generated a context-aware empathetic response
        return None

    elif action == "history":
        entries = await get_journal_history(user_id, days=7)
        if not entries:
            return "📓 Щоденник порожній. Просто напиши як справи!"
        lines = []
        for e in entries:
            date_str = e["created_at"].strftime("%d.%m %H:%M")
            text = e["content"][:100]
            mood = f" 😊{e['mood_score']}" if e["mood_score"] else ""
            energy = f" ⚡{e['energy_level']}" if e["energy_level"] else ""
            lines.append(f"  {date_str}{mood}{energy}\n    {text}")
        return "📓 Щоденник (7 днів):\n\n" + "\n\n".join(lines)

    elif action == "summary":
        data = await get_journal_summary_data(user_id, days=7)
        if data["entries_count"] == 0:
            return "📓 Немає записів за останній тиждень."
        # GPT has recent journal context and will generate a natural summary
        return None

    return None


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Main handler for all incoming Telegram messages."""
    if not update.message or not update.effective_user:
        return

    telegram_user_id = update.effective_user.id
    username = update.effective_user.username

    user = await _ensure_user(telegram_user_id, username)
    user_id = user["id"]
    daily_calorie_goal = user["daily_calorie_goal"] or 2000

    logger.info("Incoming message from user_id=%s (tg=%s), type=%s",
                user_id, telegram_user_id,
                "voice" if update.message.voice else "text")

    # Extract text from voice or text message
    message_text = ""
    if update.message.voice:
        try:
            voice_file = await update.message.voice.get_file()
            voice_bytes = await voice_file.download_as_bytearray()
            message_text = await transcribe_voice(bytes(voice_bytes))
            logger.info("Whisper transcription for user %s: %s", telegram_user_id, message_text)
        except Exception:
            logger.exception("Voice transcription failed for user %s", telegram_user_id)
            await update.message.reply_text(
                "🎙 Не вдалося обробити голосове повідомлення. Спробуй ще раз."
            )
            return
    elif update.message.text:
        message_text = update.message.text
    else:
        return

    # Ignore empty or meaningless messages (single chars, dashes, dots)
    cleaned = message_text.strip().strip(".-–—…_ ")
    if not cleaned:
        return

    await save_conversation_message(user_id, "user", message_text)

    logger.info("Processing message for user_id=%s: '%s'",
                user_id, message_text[:100])

    try:
        gpt_result = await classify_and_respond(user_id, daily_calorie_goal, message_text)
    except Exception:
        logger.exception("GPT call failed for user %s", telegram_user_id)
        await update.message.reply_text(
            "😔 Щось пішло не так. Спробуй ще раз через хвилинку."
        )
        return

    intent = gpt_result["intent"]
    response_text = gpt_result["response"]

    logger.info("GPT result for user_id=%s: intent=%s, food_items=%d",
                user_id, intent, len(gpt_result.get("food_items", [])))

    expired_services = []

    try:
        if intent == "log_food" and gpt_result["food_items"]:
            log_result = await _handle_log_food(user_id, gpt_result["food_items"])
            logged = log_result["items"]
            just_logged_cals = sum(item["calories"] for item in logged)
            stats = await get_today_stats(user_id)
            expired_services = stats.get("expired_services", [])
            # FatSecret API has a delay — just-synced entries may not appear yet.
            # Add logged calories to compensate.
            total_in = stats["today_calories_in"] + just_logged_cals
            total_out = stats["today_calories_out"]
            src = stats.get("calories_source", "none")
            src_label = " (FatSecret)" if src == "fatsecret" else ""
            balance_line = f"\n\n📊 {total_in} / {daily_calorie_goal} kcal{src_label}"
            if total_out > 0:
                balance_line += f"  🔥 {total_out} спалено"
            response_text += balance_line
            # Warn if any items failed to sync to FatSecret
            failed_sync = [i["name"] for i in logged if not i["synced_to_fs"]]
            if failed_sync and log_result["fs_connected"]:
                response_text += (
                    "\n⚠️ Не синхронізовано з FatSecret: "
                    + ", ".join(failed_sync)
                )

        elif intent == "delete_entry":
            deleted = await _handle_delete_entry(user_id)
            if not deleted:
                response_text = "🤷 Немає записів для видалення."

        elif intent == "gym":
            gym_response = await _handle_gym(user_id, gpt_result)
            if gym_response:
                response_text = gym_response

        elif intent == "journal":
            journal_response = await _handle_journal(user_id, gpt_result, message_text)
            if journal_response:
                response_text = journal_response

        elif intent == "general" and gpt_result.get("calorie_goal"):
            try:
                goal = int(float(gpt_result["calorie_goal"]))
            except (ValueError, TypeError):
                goal = 0
            if 500 <= goal <= 10000:
                await _handle_calorie_goal(user_id, goal)

    except Exception:
        logger.exception("Intent handler failed for user %s, intent=%s", telegram_user_id, intent)
        response_text = response_text or "😔 Виникла помилка при обробці запиту."

    # Append reconnect hints for expired tokens
    if expired_services:
        reconnect_lines = []
        if "whoop" in expired_services:
            reconnect_lines.append("  ⌚ WHOOP → /connect_whoop")
        if "fatsecret" in expired_services:
            reconnect_lines.append("  🥗 FatSecret → /connect_fatsecret")
        response_text += (
            "\n\n🔑 Сесія закінчилась, потрібно перепідключити:\n"
            + "\n".join(reconnect_lines)
        )

    await save_conversation_message(user_id, "assistant", response_text, intent)
    await update.message.reply_text(response_text)
    logger.info("Reply sent to user_id=%s, intent=%s, len=%d",
                user_id, intent, len(response_text))


HELP_TEXT = (
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
    "  ▸ 📊 Калорії за день з FatSecret + WHOOP\n"
    "  ▸ 😴 Дані WHOOP — сон, відновлення, тренування\n"
    "  ▸ 🗑 Видалити останній запис — «видали останнє»\n"
    "  ▸ 🎯 Встановити ціль — «встанови ціль 2500 ккал»\n"
    "\n"
    "🔗 Підключення сервісів:\n"
    "  ⌚ WHOOP → /connect_whoop\n"
    "  ❤️ Apple Health → /connect_apple_health\n"
    "  🥗 FatSecret → /connect_fatsecret\n"
    "  🔄 Синхронізувати → /sync\n"
    "  🏋️ Gym промпт → /gym_prompt\n"
    "  📓 Щоденник → /journal, /journal_time, /journal_off, /journal_on\n"
    "\n"
    "⏰ Авто-зведення: 08:00 🌅 та 21:00 🌙 (Київ)\n"
    "\n"
    "Просто пиши мені як другу — я розумію 🇺🇦 та 🇬🇧!"
)


async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start and /help commands."""
    if not update.message or not update.effective_user:
        return

    await update.message.reply_text(HELP_TEXT, disable_web_page_preview=True)


async def handle_connect_whoop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /connect_whoop command."""
    if not update.message or not update.effective_user:
        return

    telegram_id = update.effective_user.id
    url = (
        f"https://api.prod.whoop.com/oauth/oauth2/auth?"
        f"client_id={settings.whoop_client_id}"
        f"&redirect_uri={settings.whoop_redirect_uri}"
        f"&response_type=code"
        f"&scope=offline%20read%3Acycles%20read%3Aworkout%20read%3Arecovery%20read%3Asleep%20read%3Abody_measurement"
        f"&state={telegram_id}"
    )
    await update.message.reply_text(
        f"⌚ Підключити WHOOP\n"
        f"\n"
        f"Сон, відновлення, активність — все буде доступно після авторизації.\n"
        f"\n"
        f"👉 {url}",
        disable_web_page_preview=True,
    )


async def handle_connect_fatsecret(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /connect_fatsecret command."""
    if not update.message or not update.effective_user:
        return

    telegram_id = update.effective_user.id
    url = f"{settings.app_base_url}/fatsecret/connect?state={telegram_id}"
    await update.message.reply_text(
        f"🥗 Підключити FatSecret\n"
        f"\n"
        f"Щоденник їжі — синхронізується автоматично.\n"
        f"\n"
        f"👉 {url}",
        disable_web_page_preview=True,
    )


async def handle_connect_apple_health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /connect_apple_health command."""
    if not update.message or not update.effective_user:
        return

    user = await _ensure_user(update.effective_user.id, update.effective_user.username)
    pool = await get_pool()
    sync = await ensure_apple_health_sync(
        pool,
        user_id=user["id"],
        sync_frequency_hours=settings.apple_health_sync_hours,
    )
    webhook_url = f"{settings.app_base_url}/api/v1/health/apple-health/sync"
    await update.message.reply_text(
        "❤️ Apple Health\n"
        "\n"
        "Apple Health не має backend API, тому дані надсилаються з iOS Shortcut.\n"
        "Налаштуй Shortcut, який кожні 4-6 годин робить POST на webhook:\n"
        f"{webhook_url}\n"
        "\n"
        "Headers:\n"
        f"X-Apple-Health-Token: {sync['secret_key']}\n"
        "Content-Type: application/json\n"
        "\n"
        "Payload має містити `userId` = твій Telegram ID та `metrics` з Apple Health.",
        disable_web_page_preview=True,
    )


async def handle_journal_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /journal_time command — set reminder times."""
    if not update.message or not update.effective_user:
        return

    user = await _ensure_user(update.effective_user.id, update.effective_user.username)
    text = (update.message.text or "").replace("/journal_time", "", 1).strip()

    if not text:
        pool = await get_pool()
        row = await pool.fetchrow(
            "SELECT journal_time_1, journal_time_2, journal_enabled FROM users WHERE id = $1",
            user["id"],
        )
        t1 = row["journal_time_1"].strftime("%H:%M") if row and row["journal_time_1"] else "10:00"
        t2 = row["journal_time_2"].strftime("%H:%M") if row and row["journal_time_2"] else "20:00"
        enabled = row["journal_enabled"] if row else True
        status = "увімкнено" if enabled else "вимкнено"
        await update.message.reply_text(
            f"📓 Нагадування щоденника: {status}\n"
            f"  🌅 {t1}  🌙 {t2}\n\n"
            f"Змінити: /journal_time 09:00 21:00\n"
            f"Вимкнути: /journal_off\n"
            f"Увімкнути: /journal_on"
        )
        return

    import re
    times = re.findall(r'\d{1,2}:\d{2}', text)
    if len(times) < 2:
        await update.message.reply_text("Вкажи два часи: /journal_time 10:00 20:00")
        return

    from datetime import time as dt_time
    try:
        h1, m1 = map(int, times[0].split(":"))
        h2, m2 = map(int, times[1].split(":"))
        t1 = dt_time(h1, m1)
        t2 = dt_time(h2, m2)
    except (ValueError, IndexError):
        await update.message.reply_text("Невірний формат часу. Приклад: /journal_time 10:00 20:00")
        return

    pool = await get_pool()
    await pool.execute(
        "UPDATE users SET journal_time_1 = $1, journal_time_2 = $2, journal_enabled = true WHERE id = $3",
        t1, t2, user["id"],
    )
    await update.message.reply_text(f"✅ Нагадування: 🌅 {times[0]}  🌙 {times[1]}")


async def handle_journal_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /journal_off — disable journal reminders."""
    if not update.message or not update.effective_user:
        return
    user = await _ensure_user(update.effective_user.id, update.effective_user.username)
    pool = await get_pool()
    await pool.execute("UPDATE users SET journal_enabled = false WHERE id = $1", user["id"])
    await update.message.reply_text("📓 Нагадування щоденника вимкнено.\nУвімкнути: /journal_on")


async def handle_journal_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /journal_on — enable journal reminders."""
    if not update.message or not update.effective_user:
        return
    user = await _ensure_user(update.effective_user.id, update.effective_user.username)
    pool = await get_pool()
    await pool.execute("UPDATE users SET journal_enabled = true WHERE id = $1", user["id"])
    row = await pool.fetchrow(
        "SELECT journal_time_1, journal_time_2 FROM users WHERE id = $1", user["id"],
    )
    t1 = row["journal_time_1"].strftime("%H:%M") if row and row["journal_time_1"] else "10:00"
    t2 = row["journal_time_2"].strftime("%H:%M") if row and row["journal_time_2"] else "20:00"
    await update.message.reply_text(f"✅ Нагадування увімкнено: 🌅 {t1}  🌙 {t2}")


async def handle_journal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /journal command — show recent journal entries."""
    if not update.message or not update.effective_user:
        return

    user = await _ensure_user(update.effective_user.id, update.effective_user.username)
    entries = await get_journal_history(user["id"], days=7)

    if not entries:
        await update.message.reply_text("📓 Щоденник порожній. Просто напиши як справи!")
        return

    lines = []
    for e in entries:
        date_str = e["created_at"].strftime("%d.%m %H:%M")
        text = e["content"][:100]
        mood = f" 😊{e['mood_score']}" if e["mood_score"] else ""
        energy = f" ⚡{e['energy_level']}" if e["energy_level"] else ""
        tags = ""
        if e.get("tags"):
            tags = " " + " ".join(f"#{t}" for t in e["tags"])
        lines.append(f"  {date_str}{mood}{energy}{tags}\n    {text}")

    await update.message.reply_text("📓 Щоденник (7 днів):\n\n" + "\n\n".join(lines))


async def handle_gym_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /gym_prompt command — set persistent gym coaching profile."""
    if not update.message or not update.effective_user:
        return

    telegram_user_id = update.effective_user.id
    user = await _ensure_user(telegram_user_id, update.effective_user.username)

    text = update.message.text or ""
    prompt_text = text.replace("/gym_prompt", "", 1).strip()

    pool = await get_pool()
    if not prompt_text:
        row = await pool.fetchrow("SELECT gym_prompt FROM users WHERE id = $1", user["id"])
        current = row["gym_prompt"] if row and row["gym_prompt"] else "не встановлено"
        await update.message.reply_text(
            f"🏋️ Поточний gym промпт:\n{current}\n\n"
            f"Щоб змінити: /gym_prompt <текст>\n"
            f"Приклад: /gym_prompt Я тренуюсь для пауерліфтингу, фокус на базових вправах"
        )
        return

    await pool.execute("UPDATE users SET gym_prompt = $1 WHERE id = $2", prompt_text, user["id"])
    await update.message.reply_text(f"✅ Gym промпт встановлено:\n{prompt_text}")


async def handle_sync(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /sync command — verify WHOOP and FatSecret connections by fetching live data."""
    if not update.message or not update.effective_user:
        return

    telegram_user_id = update.effective_user.id
    user = await _ensure_user(telegram_user_id, update.effective_user.username)
    user_id = user["id"]

    await update.message.reply_text("🔄 Перевіряю з'єднання...")

    stats = await get_today_stats(user_id)
    results = []

    # WHOOP status
    pool = await get_pool()
    whoop_row = await pool.fetchrow(
        "SELECT whoop_access_token FROM users WHERE id = $1 AND whoop_access_token IS NOT NULL",
        user_id,
    )
    if whoop_row:
        if "whoop" in stats.get("expired_services", []):
            results.append("⌚ WHOOP — 🔑 сесія закінчилась → /connect_whoop")
        elif stats["today_calories_out"] > 0 or stats["whoop_sleep"] or stats["whoop_recovery"]:
            parts = []
            if stats["today_calories_out"] > 0:
                parts.append(f"{stats['today_calories_out']} kcal спалено")
            if stats["whoop_recovery"]:
                parts.append("recovery ✓")
            if stats["whoop_sleep"]:
                parts.append("sleep ✓")
            results.append(f"⌚ WHOOP — ✅ {', '.join(parts)}")
        else:
            results.append("⌚ WHOOP — ✅ підключено (дані ще збираються)")
    else:
        results.append("⌚ WHOOP — ⚠️ не підключено")

    apple_health_row = await pool.fetchrow(
        """SELECT last_sync_at
           FROM apple_health_sync
           WHERE user_id = $1 AND is_active = TRUE""",
        user_id,
    )
    if apple_health_row:
        last_sync = apple_health_row["last_sync_at"]
        if last_sync:
            results.append(f"❤️ Apple Health — ✅ остання синхронізація {last_sync:%d.%m %H:%M}")
        else:
            results.append("❤️ Apple Health — ✅ підключено, очікую перший sync")
    else:
        results.append("❤️ Apple Health — ⚠️ не підключено")

    # FatSecret status
    fs_row = await pool.fetchrow(
        "SELECT fatsecret_access_token FROM users WHERE id = $1 AND fatsecret_access_token IS NOT NULL",
        user_id,
    )
    if fs_row:
        if "fatsecret" in stats.get("expired_services", []):
            results.append("🥗 FatSecret — 🔑 сесія закінчилась → /connect_fatsecret")
        else:
            cals = stats["today_calories_in"]
            results.append(f"🥗 FatSecret — ✅ {cals} kcal сьогодні")
    else:
        results.append("🥗 FatSecret — ⚠️ не підключено")

    await update.message.reply_text("✅ Перевірка завершена\n\n" + "\n".join(results))


async def start_bot() -> None:
    """Initialize and start the Telegram bot with long polling."""
    global _application

    if not settings.telegram_bot_token:
        logger.warning("TELEGRAM_BOT_TOKEN not set, skipping bot startup")
        return

    _application = Application.builder().token(settings.telegram_bot_token).build()

    _application.add_handler(CommandHandler("start", handle_help))
    _application.add_handler(CommandHandler("help", handle_help))
    _application.add_handler(CommandHandler("connect_whoop", handle_connect_whoop))
    _application.add_handler(CommandHandler("connect_apple_health", handle_connect_apple_health))
    _application.add_handler(CommandHandler("connect_fatsecret", handle_connect_fatsecret))
    _application.add_handler(CommandHandler("sync", handle_sync))
    _application.add_handler(CommandHandler("gym_prompt", handle_gym_prompt))
    _application.add_handler(CommandHandler("journal", handle_journal))
    _application.add_handler(CommandHandler("journal_time", handle_journal_time))
    _application.add_handler(CommandHandler("journal_off", handle_journal_off))
    _application.add_handler(CommandHandler("journal_on", handle_journal_on))
    _application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    _application.add_handler(
        MessageHandler(filters.VOICE, handle_message)
    )

    await _application.initialize()

    await _application.bot.set_my_commands([
        BotCommand("start", "Почати / Інструкція"),
        BotCommand("help", "Допомога"),
        BotCommand("connect_whoop", "Підключити WHOOP"),
        BotCommand("connect_apple_health", "Підключити Apple Health"),
        BotCommand("connect_fatsecret", "Підключити FatSecret"),
        BotCommand("sync", "Синхронізувати дані"),
        BotCommand("gym_prompt", "Налаштувати gym профіль"),
        BotCommand("journal", "Записи щоденника"),
        BotCommand("journal_time", "Час нагадувань щоденника"),
        BotCommand("journal_off", "Вимкнути нагадування"),
        BotCommand("journal_on", "Увімкнути нагадування"),
    ])

    await _application.start()
    await _application.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram bot started (long polling)")


async def stop_bot() -> None:
    """Stop the Telegram bot gracefully."""
    global _application

    if _application is None:
        return

    await _application.updater.stop()
    await _application.stop()
    await _application.shutdown()
    _application = None
    logger.info("Telegram bot stopped")
