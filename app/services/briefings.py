from __future__ import annotations

import logging
from datetime import datetime, time, timezone
from typing import Optional

from openai import AsyncOpenAI
from telegram import Bot

from app.config import settings
from app.database import get_pool
from app.timeutils import resolve_timezone

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=settings.openai_api_key)


async def _get_users_with_telegram() -> list[dict]:
    """Fetch all users that have a telegram_user_id."""
    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT id, telegram_user_id, daily_calorie_goal, language, timezone
           FROM users
           WHERE telegram_user_id IS NOT NULL"""
    )
    return [dict(r) for r in rows]


def _users_at_local_hour(
    users: list[dict], hour: Optional[int], now: Optional[datetime] = None,
) -> list[dict]:
    """Keep users whose local wall-clock hour equals `hour` (None = everyone)."""
    if hour is None:
        return users
    current = now or datetime.now(timezone.utc)
    return [
        u for u in users
        if current.astimezone(resolve_timezone(u.get("timezone"))).hour == hour
    ]


BRIEFING_WINDOW_MINUTES = 15


async def _due_by_preferences(users: list[dict], kind: str, now: Optional[datetime] = None) -> list[dict]:
    """Users whose configured local briefing time just passed, claimed once per
    local date in ``notification_sends`` (restart/DST/replica safe)."""
    from app.services.preferences import get_preferences

    pool = await get_pool()
    current = now or datetime.now(timezone.utc)
    due = []
    for user in users:
        prefs, _ = await get_preferences(pool, user["id"])
        enabled = prefs.briefing_morning_enabled if kind == "morning" else prefs.briefing_evening_enabled
        if not enabled:
            continue
        at = prefs.briefing_morning_time if kind == "morning" else prefs.briefing_evening_time
        hh, mm = (int(x) for x in at.split(":"))
        local = current.astimezone(resolve_timezone(user.get("timezone")))
        since = (local.hour * 60 + local.minute) - (hh * 60 + mm)
        if not 0 <= since < BRIEFING_WINDOW_MINUTES:
            continue
        claimed = await pool.fetchval(
            """INSERT INTO notification_sends (user_id, kind, local_date) VALUES ($1, $2, $3)
               ON CONFLICT DO NOTHING RETURNING 1""",
            user["id"], f"briefing_{kind}", local.date(),
        )
        if claimed:
            due.append(user)
    return due


def _minutes_apart(a: time, b: time) -> int:
    """Circular distance in minutes between two wall-clock times (23:58 vs 00:02 = 4)."""
    diff = abs((a.hour * 60 + a.minute) - (b.hour * 60 + b.minute))
    return min(diff, 24 * 60 - diff)


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
    """Send a message via the running bot application (fallback: one-off Bot)."""
    try:
        from app.services import telegram_bot

        if telegram_bot._application is not None:
            await telegram_bot.send_message(telegram_user_id, text)
            return
        async with Bot(token=settings.telegram_bot_token) as bot:
            await bot.send_message(chat_id=telegram_user_id, text=text)
    except Exception:
        logger.exception(
            "Failed to send briefing to telegram_user_id=%s", telegram_user_id
        )


async def morning_briefing(
    only_local_hour: Optional[int] = None, use_preferences: bool = False,
) -> None:
    """Morning briefing job. Uses live API data.

    The scheduler runs it hourly with only_local_hour=8 so every user gets it
    at 08:00 in their own timezone; without a filter it targets everyone.
    """
    if not settings.telegram_bot_token or not settings.openai_api_key:
        logger.warning("Missing tokens, skipping morning briefing")
        return

    from app.services.ai_assistant import get_today_stats

    if use_preferences:
        users = await _due_by_preferences(await _get_users_with_telegram(), "morning")
    else:
        users = _users_at_local_hour(await _get_users_with_telegram(), only_local_hour)
    if not users:
        return
    logger.info("Starting morning briefing for %d users", len(users))

    for user in users:
        try:
            user_id = user["id"]
            lang = user.get("language") or "uk"
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
            if stats.get("apple_health_summary"):
                data_summary += f"{stats['apple_health_summary']}. "
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


async def evening_summary(
    only_local_hour: Optional[int] = None, use_preferences: bool = False,
) -> None:
    """Evening summary job (hourly with only_local_hour=21). Uses live API data."""
    if not settings.telegram_bot_token or not settings.openai_api_key:
        logger.warning("Missing tokens, skipping evening summary")
        return

    from app.services.ai_assistant import get_today_stats

    if use_preferences:
        users = await _due_by_preferences(await _get_users_with_telegram(), "evening")
    else:
        users = _users_at_local_hour(await _get_users_with_telegram(), only_local_hour)
    if not users:
        return
    logger.info("Starting evening summary for %d users", len(users))

    for user in users:
        try:
            user_id = user["id"]
            lang = user.get("language") or "uk"
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
            if stats.get("apple_health_summary"):
                data_summary += f"{stats['apple_health_summary']}. "
            if stats.get("bmr_kcal"):
                data_summary += f"Estimated BMR: {stats['bmr_kcal']} kcal/day. "
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

    logger.info("Starting journal reminders check")

    now_utc = datetime.now(timezone.utc)

    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT id, telegram_user_id, journal_time_1, journal_time_2,
                  daily_calorie_goal, language, timezone
           FROM users
           WHERE telegram_user_id IS NOT NULL
             AND journal_enabled = true"""
    )

    from app.services.ai_assistant import get_today_stats

    def _time_matches(t, now_t) -> bool:
        """Check if time t is within ±5 minutes of now_t (wraps midnight)."""
        if t is None:
            return False
        return _minutes_apart(t, now_t) <= 5

    sent = 0
    for row in rows:
        try:
            current_time = now_utc.astimezone(resolve_timezone(row["timezone"])).time()
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

            from app.i18n import t

            lang = row["language"]
            if is_morning:
                # Morning: sleep + recovery context
                parts = [t("reminder_morning", lang)]
                if stats.get("whoop_sleep"):
                    sleep_short = stats["whoop_sleep"].split(",")[0] if stats["whoop_sleep"] else ""
                    parts.append(f"😴 {sleep_short}")
                if stats.get("whoop_recovery"):
                    rec_short = stats["whoop_recovery"].split(",")[0] if stats["whoop_recovery"] else ""
                    parts.append(f"💚 {rec_short}")
                if stats.get("apple_health_sleep_hours"):
                    parts.append(t("reminder_apple_sleep", lang, hours=stats["apple_health_sleep_hours"]))
                if stats.get("apple_health_avg_hrv_ms"):
                    parts.append(t("reminder_hrv", lang, hrv=stats["apple_health_avg_hrv_ms"]))
                parts.append(t("reminder_morning_q", lang))
                text = "\n".join(parts)
            else:
                # Evening: calorie + strain context
                goal = row["daily_calorie_goal"] or 2000
                parts = [t("reminder_evening", lang)]
                cal_in = stats.get("today_calories_in", 0)
                cal_out = stats.get("today_calories_out", 0)
                if cal_in > 0 or cal_out > 0:
                    parts.append(f"📊 {cal_in}/{goal} kcal")
                    if cal_out > 0:
                        parts[-1] += t("reminder_burned", lang, kcal=cal_out)
                if stats.get("apple_health_steps"):
                    parts.append(t("reminder_steps", lang, steps=stats["apple_health_steps"]))
                strain = stats.get("today_strain", 0)
                if strain > 0:
                    parts.append(f"💪 Strain: {strain}")
                parts.append(t("reminder_evening_q", lang))
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
