from __future__ import annotations

import logging
from decimal import Decimal

from telegram import Update
from telegram.ext import (
    Application,
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
from app.services.fatsecret_api import search_food

logger = logging.getLogger(__name__)

_application: Application | None = None


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
    """Look up each food item in FatSecret, store in food_entries."""
    pool = await get_pool()
    logged = []

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

        try:
            result = await search_food(name_en, max_results=1)
            foods = result.get("results", [])
            if foods:
                nutrients = _parse_fatsecret_description(foods[0].get("description", ""))
                serving = nutrients.get("serving_size", 100.0) or 100.0
                factor = quantity_g / serving
                calories = round(nutrients["calories"] * factor, 1)
                protein = round(nutrients["protein"] * factor, 1)
                fat = round(nutrients["fat"] * factor, 1)
                carbs = round(nutrients["carbs"] * factor, 1)
        except Exception:
            logger.warning("FatSecret lookup failed for '%s'", name_en)

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
                "Sorry, I couldn't process your voice message. Please try again."
            )
            return
    elif update.message.text:
        message_text = update.message.text
    else:
        return

    if not message_text.strip():
        return

    await save_conversation_message(user_id, "user", message_text)

    try:
        gpt_result = await classify_and_respond(user_id, daily_calorie_goal, message_text)
    except Exception:
        logger.exception("GPT call failed for user %s", telegram_user_id)
        await update.message.reply_text(
            "Something went wrong. Please try again in a moment."
        )
        return

    intent = gpt_result["intent"]
    response_text = gpt_result["response"]

    try:
        if intent == "log_food" and gpt_result["food_items"]:
            await _handle_log_food(user_id, gpt_result["food_items"])
            stats = await get_today_stats(user_id)
            total_in = stats["today_calories_in"]
            total_out = stats["today_calories_out"]
            balance_line = f"\n\nToday: {total_in} / {daily_calorie_goal} kcal"
            if total_out > 0:
                balance_line += f" (burned: {total_out} kcal)"
            response_text += balance_line

        elif intent == "delete_entry":
            deleted = await _handle_delete_entry(user_id)
            if not deleted:
                response_text = "No food entries to delete."

        elif intent == "general" and gpt_result.get("calorie_goal"):
            try:
                goal = int(float(gpt_result["calorie_goal"]))
            except (ValueError, TypeError):
                goal = 0
            if 500 <= goal <= 10000:
                await _handle_calorie_goal(user_id, goal)

    except Exception:
        logger.exception("Intent handler failed for user %s, intent=%s", telegram_user_id, intent)
        response_text = response_text or "I ran into an error processing your request."

    await save_conversation_message(user_id, "assistant", response_text, intent)
    await update.message.reply_text(response_text)


async def start_bot() -> None:
    """Initialize and start the Telegram bot with long polling."""
    global _application

    if not settings.telegram_bot_token:
        logger.warning("TELEGRAM_BOT_TOKEN not set, skipping bot startup")
        return

    _application = Application.builder().token(settings.telegram_bot_token).build()

    _application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    _application.add_handler(
        MessageHandler(filters.VOICE, handle_message)
    )

    await _application.initialize()
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
