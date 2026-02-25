from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
from openai import AsyncOpenAI

from app.config import settings
from app.database import get_pool

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=settings.openai_api_key)

SYSTEM_PROMPT = """You are a personal health assistant Telegram bot. You help users track food, monitor activity, and stay healthy.

RULES:
1. Classify every user message into exactly one intent: log_food, query_data, delete_entry, or general.
2. Respond in the SAME language the user writes in (Ukrainian, English, or mixed).
3. Be concise, friendly, and use emoji sparingly.

INTENT DEFINITIONS:
- log_food: User describes food they ate/drank. Extract each food item with English name (for database lookup), original name, estimated weight in grams, and meal_type (breakfast if before 11:00, lunch if 11:00-16:00, dinner if 16:00-21:00, snack otherwise — use current_time provided). The bot automatically syncs entries to FatSecret if connected, so always log food when user asks.
- query_data: User asks about their health data (sleep, recovery, calories, workouts, mood, history, stats). You have access to WHOOP data (sleep, recovery, strain, activities), FatSecret diary, and bot-logged food — use all available data when answering.
- delete_entry: User wants to remove/undo the last food entry or a specific entry.
- general: Everything else — greetings, setting calorie goal (extract number), health tips, questions about the bot.

For log_food, also extract:
- food_items: array of objects with name_en (English), name_original (user's language), quantity_g (grams, estimate if not specified), meal_type.
- IMPORTANT: For log_food response, just confirm what was added (e.g. "Додано 100г рису"). Do NOT include calorie totals or daily summary — the system appends an accurate balance line automatically.

For general, if user wants to set calorie goal, extract:
- calorie_goal: integer (e.g., 2500)

ALWAYS respond with valid JSON (no markdown fences):
{
  "intent": "log_food|query_data|delete_entry|general",
  "food_items": [{"name_en": "...", "name_original": "...", "quantity_g": 100, "meal_type": "lunch"}],
  "calorie_goal": null,
  "response": "Your friendly response text here"
}"""


def _build_context_messages(
    conversation_history: list[dict],
    user_data: dict,
    current_message: str,
) -> list[dict]:
    """Build the messages array for the GPT API call."""
    local_now = datetime.now(ZoneInfo("Europe/Kyiv"))
    calorie_goal = user_data.get("daily_calorie_goal") or 2000
    fs_meals = user_data.get("today_fatsecret_meals", "")
    calories_in = user_data.get("today_calories_in", 0)
    calories_out = user_data.get("today_calories_out", 0)
    data_context = (
        f"Current local time (Europe/Kyiv): {local_now.strftime('%Y-%m-%d %H:%M')}. "
        f"User calorie goal: {calorie_goal} kcal. "
        f"Today's calories eaten: {calories_in} kcal. "
        f"Today's calories burned (WHOOP): {calories_out} kcal "
        f"(daily strain: {user_data.get('today_strain', 0)}, "
        f"{user_data.get('today_workout_count', 0)} tracked workouts). "
        f"IMPORTANT: Use ONLY these exact numbers when answering about calories. "
        f"Do NOT add or recalculate — these are already the correct totals."
    )
    if fs_meals:
        data_context += f" FatSecret meals today: {fs_meals}."
    if user_data.get("whoop_sleep"):
        data_context += f" {user_data['whoop_sleep']}."
    if user_data.get("whoop_recovery"):
        data_context += f" {user_data['whoop_recovery']}."
    if user_data.get("whoop_activities"):
        data_context += f" {user_data['whoop_activities']}."

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": f"USER DATA: {data_context}"},
    ]

    for msg in conversation_history:
        messages.append({"role": msg["role"], "content": msg["content"]})

    messages.append({"role": "user", "content": current_message})
    return messages


async def get_today_stats(user_id: int) -> dict:
    """Fetch today's calorie stats: bot-logged food + FatSecret diary + WHOOP activity."""
    pool = await get_pool()

    # Calories from bot-logged food_entries
    row = await pool.fetchrow(
        """SELECT COALESCE(SUM(fe.calories), 0) AS today_calories_in
           FROM food_entries fe
           WHERE fe.user_id = $1
             AND fe.logged_at >= CURRENT_DATE
             AND fe.logged_at < CURRENT_DATE + INTERVAL '1 day'""",
        user_id,
    )
    bot_calories = float(row["today_calories_in"]) if row else 0

    # Calories from FatSecret diary (if connected)
    fatsecret_calories = 0.0
    fatsecret_meals = ""
    fatsecret_ok = False
    expired_services = []
    user_row = await pool.fetchrow(
        "SELECT fatsecret_access_token, fatsecret_access_secret FROM users WHERE id = $1",
        user_id,
    )
    if user_row and user_row["fatsecret_access_token"]:
        try:
            from app.services.fatsecret_api import fetch_food_diary, FatSecretAuthError
            diary = await fetch_food_diary(
                access_token=user_row["fatsecret_access_token"],
                access_secret=user_row["fatsecret_access_secret"],
            )
            fatsecret_calories = float(diary.get("total_calories", 0))
            fatsecret_ok = True
            meals = diary.get("meals", [])
            if meals:
                fatsecret_meals = "; ".join(
                    f"{m['food']} ({m['calories']} kcal)" for m in meals[:10]
                )
        except FatSecretAuthError:
            logger.warning("FatSecret auth error for user_id=%s, clearing tokens", user_id)
            await pool.execute(
                """UPDATE users
                   SET fatsecret_access_token = NULL,
                       fatsecret_access_secret = NULL,
                       updated_at = NOW()
                   WHERE id = $1""",
                user_id,
            )
            expired_services.append("fatsecret")
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403):
                logger.warning("FatSecret HTTP auth failed for user_id=%s, clearing tokens", user_id)
                await pool.execute(
                    """UPDATE users
                       SET fatsecret_access_token = NULL,
                           fatsecret_access_secret = NULL,
                           updated_at = NOW()
                       WHERE id = $1""",
                    user_id,
                )
                expired_services.append("fatsecret")
            else:
                logger.warning("Failed to fetch FatSecret diary for user_id=%s", user_id)
        except Exception:
            logger.warning("Failed to fetch FatSecret diary for user_id=%s", user_id)

    # Calories burned + strain from WHOOP activities today
    row2 = await pool.fetchrow(
        """SELECT COALESCE(SUM(wa.calories), 0) AS today_calories_out,
                  COALESCE(SUM(wa.strain), 0) AS today_strain,
                  COUNT(*) AS workout_count
           FROM whoop_activities wa
           WHERE wa.user_id = $1
             AND wa.started_at >= CURRENT_DATE
             AND wa.started_at < CURRENT_DATE + INTERVAL '1 day'""",
        user_id,
    )
    workout_calories = float(row2["today_calories_out"]) if row2 else 0
    workout_strain = round(float(row2["today_strain"]) if row2 else 0, 1)
    workout_count = int(row2["workout_count"]) if row2 else 0

    # Fetch WHOOP daily cycle (total daily calories + strain, not just workouts)
    daily_cycle = {"strain": 0, "calories": 0, "avg_hr": 0, "max_hr": 0}
    whoop_user = await pool.fetchrow(
        """SELECT id, whoop_access_token, whoop_refresh_token, whoop_token_expires_at
           FROM users WHERE id = $1 AND whoop_access_token IS NOT NULL""",
        user_id,
    )
    if whoop_user:
        try:
            from app.services.whoop_sync import (
                fetch_daily_cycle, refresh_token_if_needed, TokenExpiredError,
            )
            async with httpx.AsyncClient(timeout=15.0) as client:
                token = await refresh_token_if_needed(dict(whoop_user), client, pool)
                try:
                    daily_cycle = await fetch_daily_cycle(token)
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 401:
                        logger.warning(
                            "WHOOP cycle 401 for user_id=%s, force-refreshing token",
                            user_id,
                        )
                        token = await refresh_token_if_needed(
                            dict(whoop_user), client, pool, force=True,
                        )
                        daily_cycle = await fetch_daily_cycle(token)
                    else:
                        raise
            logger.info(
                "WHOOP daily cycle for user_id=%s: calories=%s strain=%s",
                user_id, daily_cycle["calories"], daily_cycle["strain"],
            )
        except TokenExpiredError:
            expired_services.append("whoop")
        except Exception:
            logger.exception("Failed to fetch WHOOP daily cycle for user_id=%s", user_id)

    # Use cycle data for total daily calories/strain, fall back to workout data
    calories_out = daily_cycle["calories"] or round(workout_calories)
    today_strain = daily_cycle["strain"] or workout_strain

    # Latest WHOOP sleep data
    sleep_row = await pool.fetchrow(
        """SELECT sleep_performance_percentage, sleep_consistency_percentage,
                  sleep_efficiency_percentage,
                  total_sleep_time_milli, total_rem_sleep_milli,
                  total_slow_wave_sleep_milli, total_light_sleep_milli,
                  total_awake_milli, respiratory_rate,
                  disturbance_count, started_at, ended_at
           FROM whoop_sleep
           WHERE user_id = $1
           ORDER BY started_at DESC LIMIT 1""",
        user_id,
    )
    sleep_info = ""
    if sleep_row and sleep_row["total_sleep_time_milli"]:
        total_h = round(sleep_row["total_sleep_time_milli"] / 3600000, 1)
        rem_h = round((sleep_row["total_rem_sleep_milli"] or 0) / 3600000, 1)
        deep_h = round((sleep_row["total_slow_wave_sleep_milli"] or 0) / 3600000, 1)
        light_h = round((sleep_row["total_light_sleep_milli"] or 0) / 3600000, 1)
        awake_min = round((sleep_row["total_awake_milli"] or 0) / 60000)
        perf = sleep_row["sleep_performance_percentage"] or 0
        consistency = sleep_row["sleep_consistency_percentage"] or 0
        efficiency = sleep_row["sleep_efficiency_percentage"] or 0
        resp_rate = round(sleep_row["respiratory_rate"] or 0, 1)
        disturbances = sleep_row["disturbance_count"] or 0
        sleep_info = (
            f"Last sleep: {total_h}h total, performance {perf}%, "
            f"consistency {consistency}%, efficiency {efficiency}%, "
            f"REM {rem_h}h, deep {deep_h}h, light {light_h}h, "
            f"awake {awake_min} min, disturbances {disturbances}, "
            f"respiratory rate {resp_rate} rpm"
        )

    # Latest WHOOP recovery data
    recovery_row = await pool.fetchrow(
        """SELECT recovery_score, resting_heart_rate, hrv_rmssd_milli,
                  spo2_percentage, skin_temp_celsius
           FROM whoop_recovery
           WHERE user_id = $1
           ORDER BY recorded_at DESC LIMIT 1""",
        user_id,
    )
    recovery_info = ""
    if recovery_row and recovery_row["recovery_score"] is not None:
        recovery_info = (
            f"Recovery: {recovery_row['recovery_score']}%, "
            f"resting HR {recovery_row['resting_heart_rate']} bpm, "
            f"HRV {round(recovery_row['hrv_rmssd_milli'] or 0, 1)} ms"
        )
        if recovery_row["spo2_percentage"]:
            recovery_info += f", SpO2 {recovery_row['spo2_percentage']}%"
        if recovery_row["skin_temp_celsius"]:
            recovery_info += f", skin temp {recovery_row['skin_temp_celsius']}°C"

    # Recent WHOOP workouts (last 5)
    activity_rows = await pool.fetch(
        """SELECT sport_name, calories, strain, avg_heart_rate, max_heart_rate,
                  started_at
           FROM whoop_activities
           WHERE user_id = $1
           ORDER BY started_at DESC LIMIT 5""",
        user_id,
    )
    activities_info = ""
    if activity_rows:
        activities_info = "Recent workouts: " + "; ".join(
            f"{r['sport_name']} ({round(r['calories'])} kcal, strain {round(r['strain'] or 0, 1)}, "
            f"avg HR {r['avg_heart_rate']}, max HR {r['max_heart_rate']}, "
            f"{r['started_at'].strftime('%d.%m %H:%M') if hasattr(r['started_at'], 'strftime') else r['started_at']})"
            for r in activity_rows
        )

    # When FatSecret is connected and working, it's the source of truth
    # (bot entries are synced there, so don't double-count).
    # Only use bot_calories as fallback when FatSecret is unavailable.
    if fatsecret_ok:
        total_in = round(fatsecret_calories)
    else:
        total_in = round(bot_calories)

    return {
        "today_calories_in": total_in,
        "today_fatsecret_meals": fatsecret_meals,
        "today_calories_out": round(calories_out),
        "today_strain": today_strain,
        "today_workout_count": workout_count,
        "whoop_sleep": sleep_info,
        "whoop_recovery": recovery_info,
        "whoop_activities": activities_info,
        "expired_services": expired_services,
    }


async def load_conversation_context(user_id: int, hours: int = 24) -> list[dict]:
    """Load recent conversation messages for context window."""
    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT role, content
           FROM conversation_messages
           WHERE user_id = $1
             AND created_at > NOW() - make_interval(hours => $2)
           ORDER BY created_at ASC
           LIMIT 50""",
        user_id,
        hours,
    )
    return [{"role": r["role"], "content": r["content"]} for r in rows]


async def save_conversation_message(
    user_id: int, role: str, content: str, intent: str | None = None,
) -> None:
    """Save a message to conversation history."""
    pool = await get_pool()
    await pool.execute(
        """INSERT INTO conversation_messages (user_id, role, content, intent)
           VALUES ($1, $2, $3, $4)""",
        user_id,
        role,
        content,
        intent,
    )


async def classify_and_respond(
    user_id: int,
    daily_calorie_goal: int,
    message_text: str,
) -> dict:
    """Single GPT call: classify intent + generate response."""
    conversation_history = await load_conversation_context(user_id)
    today_stats = await get_today_stats(user_id)

    user_data = {
        "daily_calorie_goal": daily_calorie_goal,
        **today_stats,
    }

    messages = _build_context_messages(conversation_history, user_data, message_text)

    response = await client.chat.completions.create(
        model=settings.openai_model,
        messages=messages,
        temperature=0.3,
        max_tokens=1024,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content or "{}"
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.error("GPT returned invalid JSON: %s", raw)
        parsed = {
            "intent": "general",
            "food_items": [],
            "calorie_goal": None,
            "response": raw,
        }

    parsed.setdefault("intent", "general")
    parsed.setdefault("food_items", [])
    parsed.setdefault("calorie_goal", None)
    parsed.setdefault("response", "")

    return parsed


async def transcribe_voice(file_bytes: bytes, file_name: str = "voice.ogg") -> str:
    """Transcribe voice audio using OpenAI Whisper. Auto-detects language."""
    transcript = await client.audio.transcriptions.create(
        model="whisper-1",
        file=(file_name, file_bytes),
    )
    return transcript.text
