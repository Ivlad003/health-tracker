from __future__ import annotations

import logging
from decimal import Decimal

import httpx

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


async def _handle_log_food(user_id: int, food_items: list[dict]) -> list[dict]:
    """Look up each food item in FatSecret, store in food_entries, sync to FatSecret diary."""
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
                serving = nutrients.get("serving_size", 100.0) or 100.0
                factor = quantity_g / serving
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
                    # Find best serving for gram-based entry:
                    # 1. "1g" serving → number_of_units = exact grams (FatSecret shows "200g")
                    # 2. Any gram serving (e.g. "100g") → proportional units
                    # 3. Fallback to first serving
                    gram_servings = [
                        s for s in servings
                        if s["metric_serving_unit"] == "g" and s["metric_serving_amount"] > 0
                    ]
                    serving = next(
                        (s for s in gram_servings if s["metric_serving_amount"] == 1.0),
                        gram_servings[0] if gram_servings else servings[0],
                    )
                    metric_amount = serving["metric_serving_amount"] or 100.0
                    units = quantity_g / metric_amount
                    logger.info(
                        "FatSecret sync: food_id=%s serving=%s metric=%sg units=%.2f for %dg",
                        food_id, serving["description"], metric_amount, units, quantity_g,
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

        logged.append({"name": name_original, "calories": round(calories)})

    return logged


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


async def _handle_calorie_goal(user_id: int, calorie_goal: int) -> None:
    """Update user's daily calorie goal."""
    pool = await get_pool()
    await pool.execute(
        "UPDATE users SET daily_calorie_goal = $1 WHERE id = $2",
        calorie_goal,
        user_id,
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Main handler for all incoming Telegram messages."""
    if not update.message or not update.effective_user:
        return

    telegram_user_id = update.effective_user.id
    username = update.effective_user.username

    user = await _ensure_user(telegram_user_id, username)
    user_id = user["id"]
    daily_calorie_goal = user["daily_calorie_goal"] or 2000

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

    expired_services = []

    try:
        if intent == "log_food" and gpt_result["food_items"]:
            logged = await _handle_log_food(user_id, gpt_result["food_items"])
            just_logged_cals = sum(item["calories"] for item in logged)
            stats = await get_today_stats(user_id)
            expired_services = stats.get("expired_services", [])
            # FatSecret API has a delay — just-synced entries may not appear yet.
            # Add logged calories to compensate.
            total_in = stats["today_calories_in"] + just_logged_cals
            total_out = stats["today_calories_out"]
            balance_line = f"\n\n📊 {total_in} / {daily_calorie_goal} kcal"
            if total_out > 0:
                balance_line += f"  🔥 {total_out} спалено"
            response_text += balance_line

        elif intent == "delete_entry":
            deleted = await _handle_delete_entry(user_id)
            if not deleted:
                response_text = "🤷 Немає записів для видалення."

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


HELP_TEXT = (
    "👋 Привіт! Я твій персональний помічник з здоров'я.\n"
    "\n"
    "🍎 Що я вмію:\n"
    "  ▸ Записувати їжу — просто напиши що з'їв\n"
    "     Наприклад: «200г курячої грудки з рисом»\n"
    "  ▸ 🎙 Голосові — скажи що з'їв голосом\n"
    "  ▸ 📊 Калорії за день з FatSecret + WHOOP\n"
    "  ▸ 😴 Дані WHOOP — сон, відновлення, тренування\n"
    "  ▸ 🗑 Видалити останній запис — «видали останнє»\n"
    "  ▸ 🎯 Встановити ціль — «встанови ціль 2500 ккал»\n"
    "\n"
    "🔗 Підключення сервісів:\n"
    "  ⌚ WHOOP → /connect_whoop\n"
    "  🥗 FatSecret → /connect_fatsecret\n"
    "  🔄 Синхронізувати → /sync\n"
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
        f"&scope=read%3Aworkout%20read%3Arecovery%20read%3Asleep%20read%3Abody_measurement"
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


async def handle_sync(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /sync command — force sync all data from WHOOP and FatSecret."""
    if not update.message or not update.effective_user:
        return

    telegram_user_id = update.effective_user.id
    user = await _ensure_user(telegram_user_id, update.effective_user.username)
    user_id = user["id"]

    await update.message.reply_text("🔄 Синхронізую дані...")

    pool = await get_pool()
    results = []

    # Sync WHOOP (7-day lookback)
    whoop_row = await pool.fetchrow(
        """SELECT id, telegram_user_id, whoop_user_id, whoop_access_token,
                  whoop_refresh_token, whoop_token_expires_at
           FROM users
           WHERE id = $1
                 AND whoop_access_token IS NOT NULL
                 AND whoop_user_id IS NOT NULL""",
        user_id,
    )
    if whoop_row:
        try:
            from app.services.whoop_sync import sync_whoop_user, TokenExpiredError
            await sync_whoop_user(dict(whoop_row), pool, lookback_hours=168)
            results.append("⌚ WHOOP — ✅ синхронізовано (7 днів)")
        except TokenExpiredError:
            results.append("⌚ WHOOP — 🔑 сесія закінчилась → /connect_whoop")
        except Exception:
            logger.exception("Sync WHOOP failed for user_id=%s", user_id)
            results.append("⌚ WHOOP — ❌ помилка синхронізації")
    else:
        results.append("⌚ WHOOP — ⚠️ не підключено")

    # Sync FatSecret diary (fetch today's data to verify connection)
    fs_row = await pool.fetchrow(
        "SELECT fatsecret_access_token, fatsecret_access_secret FROM users WHERE id = $1",
        user_id,
    )
    if fs_row and fs_row["fatsecret_access_token"]:
        try:
            from app.services.fatsecret_api import fetch_food_diary, FatSecretAuthError
            diary = await fetch_food_diary(
                access_token=fs_row["fatsecret_access_token"],
                access_secret=fs_row["fatsecret_access_secret"],
            )
            count = diary.get("entries_count", 0)
            cals = diary.get("total_calories", 0)
            results.append(f"🥗 FatSecret — ✅ {count} записів, {cals} kcal сьогодні")
        except (httpx.HTTPStatusError, FatSecretAuthError) as e:
            is_auth = (
                isinstance(e, FatSecretAuthError)
                or (isinstance(e, httpx.HTTPStatusError) and e.response.status_code in (401, 403))
            )
            if is_auth:
                await pool.execute(
                    """UPDATE users
                       SET fatsecret_access_token = NULL,
                           fatsecret_access_secret = NULL,
                           updated_at = NOW()
                       WHERE id = $1""",
                    user_id,
                )
                results.append("🥗 FatSecret — 🔑 сесія закінчилась → /connect_fatsecret")
            else:
                logger.exception("Sync FatSecret failed for user_id=%s", user_id)
                results.append("🥗 FatSecret — ❌ помилка синхронізації")
        except Exception:
            logger.exception("Sync FatSecret failed for user_id=%s", user_id)
            results.append("🥗 FatSecret — ❌ помилка синхронізації")
    else:
        results.append("🥗 FatSecret — ⚠️ не підключено")

    await update.message.reply_text("✅ Синхронізація завершена\n\n" + "\n".join(results))


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
    _application.add_handler(CommandHandler("connect_fatsecret", handle_connect_fatsecret))
    _application.add_handler(CommandHandler("sync", handle_sync))
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
        BotCommand("connect_fatsecret", "Підключити FatSecret"),
        BotCommand("sync", "Синхронізувати дані"),
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
