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
    """Morning briefing job (8:00 Kyiv). Uses live API data."""
    if not settings.telegram_bot_token or not settings.openai_api_key:
        logger.warning("Missing tokens, skipping morning briefing")
        return

    logger.info("Starting morning briefing")
    from app.services.ai_assistant import get_today_stats

    users = await _get_users_with_telegram()

    for user in users:
        try:
            user_id = user["id"]
            lang = user.get("language", "uk")
            goal = user["daily_calorie_goal"] or 2000

            stats = await get_today_stats(user_id)

            data_summary = (
                f"Calories eaten today: {stats['today_calories_in']} kcal (goal: {goal}). "
                f"Calories burned: {stats['today_calories_out']} kcal. "
            )
            if stats.get("whoop_sleep"):
                data_summary += f"{stats['whoop_sleep']}. "
            if stats.get("whoop_recovery"):
                data_summary += f"{stats['whoop_recovery']}. "
            data_summary += f"Language: {lang}."

            prompt = (
                "You are a health assistant bot sending a morning briefing. "
                "Summarize sleep, recovery, and current calorie status. "
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
    """Evening summary job (21:00 Kyiv). Uses live API data."""
    if not settings.telegram_bot_token or not settings.openai_api_key:
        logger.warning("Missing tokens, skipping evening summary")
        return

    logger.info("Starting evening summary")
    from app.services.ai_assistant import get_today_stats

    users = await _get_users_with_telegram()

    for user in users:
        try:
            user_id = user["id"]
            lang = user.get("language", "uk")
            goal = user["daily_calorie_goal"] or 2000

            stats = await get_today_stats(user_id)
            total_in = stats["today_calories_in"]
            total_out = stats["today_calories_out"]
            net = total_in - total_out

            data_summary = (
                f"Calories in: {total_in} kcal. Goal: {goal} kcal. "
                f"Burned: {total_out} kcal ({stats['today_workout_count']} workouts). "
                f"Net: {net} kcal. "
                f"Strain: {stats['today_strain']}. "
            )
            if stats.get("today_fatsecret_meals"):
                data_summary += f"Meals: {stats['today_fatsecret_meals']}. "
            if stats.get("whoop_sleep"):
                data_summary += f"{stats['whoop_sleep']}. "
            if stats.get("whoop_recovery"):
                data_summary += f"{stats['whoop_recovery']}. "
            if stats.get("whoop_activities"):
                data_summary += f"{stats['whoop_activities']}. "
            data_summary += f"Language: {lang}."

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
