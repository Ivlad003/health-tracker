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
- query_data: User asks about their health data (sleep, recovery, calories, workouts, mood, history, stats). You have access to WHOOP data (sleep, recovery, strain, activities, weight, heart rate) and FatSecret diary — use all available data when answering.
  WHOOP data available: sleep (duration, stages, performance), recovery (score, HRV, resting HR, SpO2, skin temp), strain, calories burned, workouts, weight, height, max HR.
  WHOOP data NOT available via API (app-only): steps, HR zones, VO₂ max, stress monitor. If user asks about these, explain they're only visible in the WHOOP app directly.
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
    calories_source = user_data.get("calories_source", "bot")
    cycle_state = user_data.get("cycle_score_state", "no_data")

    # Eaten calories label with source
    source_label = "FatSecret" if calories_source == "fatsecret" else "bot entries"
    eaten_label = f"Today's calories eaten (source: {source_label}): {calories_in} kcal. "

    # Burned calories label
    if cycle_state == "ESTIMATED" and calories_out > 0:
        burned_label = (
            f"Estimated calories burned today so far (WHOOP): ~{calories_out} kcal "
            f"(real-time estimate based on metabolism + workouts). "
        )
    elif cycle_state == "PENDING_SCORE" and calories_out > 0:
        burned_label = (
            f"Last completed WHOOP cycle calories burned: {calories_out} kcal "
            f"(today's cycle still in progress). "
        )
    elif calories_out > 0:
        burned_label = f"Today's calories burned (WHOOP): {calories_out} kcal. "
    else:
        burned_label = (
            "WHOOP calorie burn data: today's cycle still in progress, "
            "no completed data yet. "
        )

    # Calorie balance
    balance = calories_in - calories_out
    balance_label = f"Calorie balance: {calories_in} eaten - {calories_out} burned = {balance} net. "

    data_context = (
        f"Current local time (Europe/Kyiv): {local_now.strftime('%Y-%m-%d %H:%M')}. "
        f"User calorie goal: {calorie_goal} kcal. "
        f"{eaten_label}"
        f"{burned_label}"
        f"{balance_label}"
        f"Daily strain: {user_data.get('today_strain', 0)}, "
        f"{user_data.get('today_workout_count', 0)} tracked workouts. "
        f"IMPORTANT: Use ONLY these exact numbers when answering about calories. "
        f"Do NOT add or recalculate — these are already the correct totals. "
        f"When user asks about calories, ALWAYS mention both eaten AND burned."
    )
    if fs_meals:
        data_context += f" FatSecret meals today: {fs_meals}."
    if user_data.get("whoop_sleep"):
        data_context += f" {user_data['whoop_sleep']}."
    if user_data.get("whoop_recovery"):
        data_context += f" {user_data['whoop_recovery']}."
    if user_data.get("whoop_activities"):
        data_context += f" {user_data['whoop_activities']}."
    if user_data.get("whoop_body"):
        data_context += f" {user_data['whoop_body']}."

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": f"USER DATA: {data_context}"},
    ]

    for msg in conversation_history:
        messages.append({"role": msg["role"], "content": msg["content"]})

    messages.append({"role": "user", "content": current_message})
    return messages


async def get_today_stats(user_id: int) -> dict:
    """Fetch today's stats: FatSecret diary (live) + WHOOP data (live). No DB reads."""
    logger.info("Fetching today stats for user_id=%s", user_id)
    pool = await get_pool()

    # Calories eaten from FatSecret diary (live API, source of truth)
    fatsecret_calories = 0.0
    fatsecret_meals = ""
    fatsecret_ok = False
    expired_services = []
    user_row = await pool.fetchrow(
        "SELECT fatsecret_access_token, fatsecret_access_secret FROM users WHERE id = $1",
        user_id,
    )
    if user_row and user_row["fatsecret_access_token"]:
        logger.info("Fetching FatSecret diary for user_id=%s", user_id)
        try:
            from app.services.fatsecret_api import fetch_food_diary, FatSecretAuthError
            diary = await fetch_food_diary(
                access_token=user_row["fatsecret_access_token"],
                access_secret=user_row["fatsecret_access_secret"],
            )
            fatsecret_calories = float(diary.get("total_calories", 0))
            fatsecret_ok = True
            logger.info("FatSecret diary: %.0f kcal, %d entries",
                        fatsecret_calories, len(diary.get("meals", [])))
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

    # Fetch ALL WHOOP data directly from API (real-time, not from DB)
    whoop = {
        "calories_out": 0, "strain": 0, "workout_count": 0,
        "cycle_score_state": "no_data",
        "sleep_info": "", "recovery_info": "", "activities_info": "", "body_info": "",
    }
    whoop_user = await pool.fetchrow(
        """SELECT id, whoop_access_token, whoop_refresh_token, whoop_token_expires_at
           FROM users WHERE id = $1 AND whoop_access_token IS NOT NULL""",
        user_id,
    )
    if whoop_user:
        logger.info("Fetching WHOOP data for user_id=%s", user_id)
        try:
            from app.services.whoop_sync import (
                fetch_whoop_context, refresh_token_if_needed, TokenExpiredError,
            )
            async with httpx.AsyncClient(timeout=15.0) as client:
                token = await refresh_token_if_needed(dict(whoop_user), client, pool)
                try:
                    whoop = await fetch_whoop_context(token)
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 401:
                        logger.warning(
                            "WHOOP API 401 for user_id=%s, force-refreshing token",
                            user_id,
                        )
                        token = await refresh_token_if_needed(
                            dict(whoop_user), client, pool, force=True,
                        )
                        whoop = await fetch_whoop_context(token)
                    else:
                        raise
        except TokenExpiredError:
            expired_services.append("whoop")
        except Exception:
            logger.exception("Failed to fetch WHOOP data for user_id=%s", user_id)

    # FatSecret is the sole source of truth for eaten calories (live API).
    total_in = round(fatsecret_calories) if fatsecret_ok else 0
    calories_source = "fatsecret" if fatsecret_ok else "none"

    logger.info("Stats for user_id=%s: in=%d kcal (src=%s), out=%d kcal, strain=%.1f, workouts=%d",
                user_id, total_in, calories_source,
                whoop["calories_out"], whoop["strain"], whoop["workout_count"])

    return {
        "today_calories_in": total_in,
        "calories_source": calories_source,
        "today_fatsecret_meals": fatsecret_meals,
        "today_calories_out": whoop["calories_out"],
        "today_strain": whoop["strain"],
        "today_workout_count": whoop["workout_count"],
        "cycle_score_state": whoop["cycle_score_state"],
        "whoop_sleep": whoop["sleep_info"],
        "whoop_recovery": whoop["recovery_info"],
        "whoop_activities": whoop["activities_info"],
        "whoop_body": whoop["body_info"],
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
    logger.info("GPT classify_and_respond for user_id=%s", user_id)
    conversation_history = await load_conversation_context(user_id)
    logger.info("Loaded %d conversation messages for user_id=%s", len(conversation_history), user_id)
    today_stats = await get_today_stats(user_id)

    user_data = {
        "daily_calorie_goal": daily_calorie_goal,
        **today_stats,
    }

    messages = _build_context_messages(conversation_history, user_data, message_text)
    logger.info("Calling GPT model=%s, messages=%d", settings.openai_model, len(messages))

    response = await client.chat.completions.create(
        model=settings.openai_model,
        messages=messages,
        temperature=0.3,
        max_tokens=1024,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content or "{}"
    tokens_used = response.usage.total_tokens if response.usage else 0
    logger.info("GPT response: %d tokens, %d chars", tokens_used, len(raw))
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
    logger.info("Whisper transcription: %d bytes", len(file_bytes))
    transcript = await client.audio.transcriptions.create(
        model="whisper-1",
        file=(file_name, file_bytes),
        prompt=(
            "Їжа, калорії, грам, грамів, сніданок, обід, вечеря. "
            "Food logging: grams, breakfast, lunch, dinner, chicken, rice, salad."
        ),
    )
    return transcript.text
