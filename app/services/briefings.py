from __future__ import annotations

import logging

from openai import AsyncOpenAI
from telegram import Bot

from app.config import settings
from app.database import get_pool

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=settings.openai_api_key)


async def _get_users_with_telegram() -> list[dict]:
    """Fetch all users that have a telegram_user_id."""
    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT id, telegram_user_id, daily_calorie_goal, language
           FROM users
           WHERE telegram_user_id IS NOT NULL"""
    )
    return [dict(r) for r in rows]


async def _generate_briefing(prompt: str, data_summary: str) -> str:
    """Call GPT to generate a briefing message."""
    response = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": data_summary},
        ],
        temperature=0.7,
        max_tokens=512,
    )
    return response.choices[0].message.content


async def _send_telegram_message(telegram_user_id: int, text: str) -> None:
    """Send a message via Telegram Bot API."""
    bot = Bot(token=settings.telegram_bot_token)
    try:
        await bot.send_message(chat_id=telegram_user_id, text=text)
    except Exception:
        logger.exception(
            "Failed to send briefing to telegram_user_id=%s", telegram_user_id
        )


async def morning_briefing() -> None:
    """Morning briefing job (8:00 Kyiv)."""
    if not settings.telegram_bot_token or not settings.openai_api_key:
        logger.warning("Missing tokens, skipping morning briefing")
        return

    logger.info("Starting morning briefing")
    pool = await get_pool()
    users = await _get_users_with_telegram()

    for user in users:
        try:
            user_id = user["id"]
            lang = user.get("language", "uk")

            sleep_row = await pool.fetchrow(
                """SELECT sleep_performance_percentage, total_sleep_time_milli
                   FROM whoop_sleep
                   WHERE user_id = $1
                   ORDER BY started_at DESC LIMIT 1""",
                user_id,
            )
            recovery_row = await pool.fetchrow(
                """SELECT recovery_score, resting_heart_rate, hrv_rmssd_milli
                   FROM whoop_recovery
                   WHERE user_id = $1
                   ORDER BY recorded_at DESC LIMIT 1""",
                user_id,
            )
            cal_row = await pool.fetchrow(
                """SELECT COALESCE(SUM(calories), 0) AS total_in
                   FROM food_entries
                   WHERE user_id = $1
                     AND logged_at >= CURRENT_DATE - INTERVAL '1 day'
                     AND logged_at < CURRENT_DATE""",
                user_id,
            )

            sleep_hours = 0.0
            sleep_perf = 0.0
            if sleep_row and sleep_row["total_sleep_time_milli"]:
                sleep_hours = round(sleep_row["total_sleep_time_milli"] / 3600000, 1)
                sleep_perf = float(sleep_row["sleep_performance_percentage"] or 0)

            recovery_score = (
                float(recovery_row["recovery_score"])
                if recovery_row and recovery_row["recovery_score"]
                else 0
            )
            yesterday_cal = float(cal_row["total_in"]) if cal_row else 0
            goal = user["daily_calorie_goal"] or 2000

            data_summary = (
                f"Sleep: {sleep_hours}h, performance {sleep_perf:.0f}%. "
                f"Recovery: {recovery_score:.0f}%. "
                f"Yesterday calories: {yesterday_cal:.0f} / {goal} kcal goal. "
                f"Language: {lang}."
            )

            prompt = (
                "You are a health assistant bot sending a morning briefing. "
                "Summarize sleep, recovery, yesterday's calories. "
                "Add one actionable tip. Keep it under 5 lines. "
                f"Respond in {'Ukrainian' if lang == 'uk' else 'English'}."
            )

            text = await _generate_briefing(prompt, data_summary)
            await _send_telegram_message(user["telegram_user_id"], text)
            logger.info("Morning briefing sent to user_id=%s", user_id)

        except Exception:
            logger.exception("Morning briefing failed for user_id=%s", user.get("id"))

    logger.info("Morning briefing complete")


async def evening_summary() -> None:
    """Evening summary job (21:00 Kyiv)."""
    if not settings.telegram_bot_token or not settings.openai_api_key:
        logger.warning("Missing tokens, skipping evening summary")
        return

    logger.info("Starting evening summary")
    pool = await get_pool()
    users = await _get_users_with_telegram()

    for user in users:
        try:
            user_id = user["id"]
            lang = user.get("language", "uk")

            food_rows = await pool.fetch(
                """SELECT food_name, calories, protein, meal_type
                   FROM food_entries
                   WHERE user_id = $1
                     AND logged_at >= CURRENT_DATE
                     AND logged_at < CURRENT_DATE + INTERVAL '1 day'
                   ORDER BY logged_at""",
                user_id,
            )
            workout_row = await pool.fetchrow(
                """SELECT COALESCE(SUM(calories), 0) AS total_out,
                          COUNT(*) AS workout_count
                   FROM whoop_activities
                   WHERE user_id = $1
                     AND started_at >= CURRENT_DATE
                     AND started_at < CURRENT_DATE + INTERVAL '1 day'""",
                user_id,
            )

            total_in = sum(float(r["calories"]) for r in food_rows)
            total_protein = sum(float(r["protein"] or 0) for r in food_rows)
            total_out = float(workout_row["total_out"]) if workout_row else 0
            workout_count = int(workout_row["workout_count"]) if workout_row else 0
            goal = user["daily_calorie_goal"] or 2000

            meals_text = ""
            if food_rows:
                meals_text = "Meals: " + "; ".join(
                    f"{r['food_name']} ({r['calories']} kcal)" for r in food_rows
                )

            data_summary = (
                f"Calories in: {total_in:.0f} kcal. Goal: {goal} kcal. "
                f"Protein: {total_protein:.0f}g. "
                f"Burned: {total_out:.0f} kcal ({workout_count} workouts). "
                f"Net: {total_in - total_out:.0f} kcal. "
                f"{meals_text} Language: {lang}."
            )

            prompt = (
                "You are a health assistant bot sending an evening summary. "
                "Summarize today's nutrition and activity. Mention surplus/deficit. "
                "Add one tip for tomorrow. Keep it under 6 lines. "
                f"Respond in {'Ukrainian' if lang == 'uk' else 'English'}."
            )

            text = await _generate_briefing(prompt, data_summary)
            await _send_telegram_message(user["telegram_user_id"], text)
            logger.info("Evening summary sent to user_id=%s", user_id)

        except Exception:
            logger.exception("Evening summary failed for user_id=%s", user.get("id"))

    logger.info("Evening summary complete")


async def cleanup_old_conversations() -> None:
    """Delete conversation messages older than 7 days."""
    pool = await get_pool()
    result = await pool.execute(
        "DELETE FROM conversation_messages WHERE created_at < NOW() - INTERVAL '7 days'"
    )
    logger.info("Conversation cleanup: %s", result)
