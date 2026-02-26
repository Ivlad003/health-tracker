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


async def journal_reminders() -> None:
    """Send journal reminders to users whose reminder time matches now (±5 min)."""
    if not settings.telegram_bot_token:
        return

    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    logger.info("Starting journal reminders check")

    now_kyiv = datetime.now(ZoneInfo("Europe/Kyiv"))
    current_time = now_kyiv.time()

    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT id, telegram_user_id, journal_time_1, journal_time_2,
                  daily_calorie_goal, language
           FROM users
           WHERE telegram_user_id IS NOT NULL
             AND journal_enabled = true"""
    )

    from app.services.ai_assistant import get_today_stats

    def _time_matches(t, now_t) -> bool:
        """Check if time t is within ±5 minutes of now_t."""
        if t is None:
            return False
        # Compare as minutes since midnight
        t_min = t.hour * 60 + t.minute
        now_min = now_t.hour * 60 + now_t.minute
        return abs(t_min - now_min) <= 5

    sent = 0
    for row in rows:
        try:
            t1_match = _time_matches(row["journal_time_1"], current_time)
            t2_match = _time_matches(row["journal_time_2"], current_time)

            if not t1_match and not t2_match:
                continue

            # Prevent duplicate reminders — check if we sent one in last 30 min
            user_id = row["id"]
            recent = await pool.fetchval(
                """SELECT COUNT(*) FROM conversation_messages
                   WHERE user_id = $1 AND role = 'assistant'
                     AND intent = 'journal_reminder'
                     AND created_at > NOW() - INTERVAL '30 minutes'""",
                user_id,
            )
            if recent and recent > 0:
                continue

            is_morning = t1_match
            stats = await get_today_stats(user_id)

            if is_morning:
                # Morning: sleep + recovery context
                parts = ["🌅 Доброго ранку!"]
                if stats.get("whoop_sleep"):
                    sleep_short = stats["whoop_sleep"].split(",")[0] if stats["whoop_sleep"] else ""
                    parts.append(f"😴 {sleep_short}")
                if stats.get("whoop_recovery"):
                    rec_short = stats["whoop_recovery"].split(",")[0] if stats["whoop_recovery"] else ""
                    parts.append(f"💚 {rec_short}")
                parts.append("\nЯк настрій? Які плани на день?")
                text = "\n".join(parts)
            else:
                # Evening: calorie + strain context
                goal = row["daily_calorie_goal"] or 2000
                parts = ["🌙 Як пройшов день?"]
                cal_in = stats.get("today_calories_in", 0)
                cal_out = stats.get("today_calories_out", 0)
                if cal_in > 0 or cal_out > 0:
                    parts.append(f"📊 {cal_in}/{goal} kcal")
                    if cal_out > 0:
                        parts[-1] += f", 🔥 {cal_out} спалено"
                strain = stats.get("today_strain", 0)
                if strain > 0:
                    parts.append(f"💪 Strain: {strain}")
                parts.append("\nОпиши як себе почуваєш.")
                text = "\n".join(parts)

            await _send_telegram_message(row["telegram_user_id"], text)
            # Record reminder to prevent duplicates
            await pool.execute(
                """INSERT INTO conversation_messages (user_id, role, content, intent)
                   VALUES ($1, 'assistant', $2, 'journal_reminder')""",
                user_id, text,
            )
            sent += 1
            logger.info("Journal reminder sent to user_id=%s (%s)",
                        user_id, "morning" if is_morning else "evening")

        except Exception:
            logger.exception("Journal reminder failed for user_id=%s", row.get("id"))

    logger.info("Journal reminders complete: %d sent", sent)


async def cleanup_old_conversations() -> None:
    """Delete conversation messages older than 7 days."""
    pool = await get_pool()
    result = await pool.execute(
        "DELETE FROM conversation_messages WHERE created_at < NOW() - INTERVAL '7 days'"
    )
    logger.info("Conversation cleanup: %s", result)
