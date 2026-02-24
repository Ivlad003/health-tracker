from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

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
- log_food: User describes food they ate/drank. Extract each food item with English name (for database lookup), original name, estimated weight in grams, and meal_type (breakfast if before 11:00, lunch if 11:00-16:00, dinner if 16:00-21:00, snack otherwise — use current_time provided).
- query_data: User asks about their health data (sleep, recovery, calories, workouts, mood, history, stats). You have access to FatSecret diary data and bot-logged food — use both when answering calorie questions.
- delete_entry: User wants to remove/undo the last food entry or a specific entry.
- general: Everything else — greetings, setting calorie goal (extract number), health tips, questions about the bot.

For log_food, also extract:
- food_items: array of objects with name_en (English), name_original (user's language), quantity_g (grams, estimate if not specified), meal_type.

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
    data_context = (
        f"Current local time (Europe/Kyiv): {local_now.strftime('%Y-%m-%d %H:%M')}. "
        f"User calorie goal: {calorie_goal} kcal. "
        f"Today's total calories in: {user_data.get('today_calories_in', 0)} kcal "
        f"(bot-logged: {user_data.get('today_calories_in_bot', 0)}, "
        f"FatSecret diary: {user_data.get('today_calories_in_fatsecret', 0)}). "
        f"Today's calories burned: {user_data.get('today_calories_out', 0)} kcal."
    )
    if fs_meals:
        data_context += f" FatSecret meals today: {fs_meals}."

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
    user_row = await pool.fetchrow(
        "SELECT fatsecret_access_token, fatsecret_access_secret FROM users WHERE id = $1",
        user_id,
    )
    if user_row and user_row["fatsecret_access_token"]:
        try:
            from app.services.fatsecret_api import fetch_food_diary
            diary = await fetch_food_diary(
                access_token=user_row["fatsecret_access_token"],
                access_secret=user_row["fatsecret_access_secret"],
            )
            fatsecret_calories = float(diary.get("total_calories", 0))
            meals = diary.get("meals", [])
            if meals:
                fatsecret_meals = "; ".join(
                    f"{m['food']} ({m['calories']} kcal)" for m in meals[:10]
                )
        except Exception:
            logger.warning("Failed to fetch FatSecret diary for user_id=%s", user_id)

    # Calories burned from WHOOP
    row2 = await pool.fetchrow(
        """SELECT COALESCE(SUM(wa.calories), 0) AS today_calories_out
           FROM whoop_activities wa
           WHERE wa.user_id = $1
             AND wa.started_at >= CURRENT_DATE
             AND wa.started_at < CURRENT_DATE + INTERVAL '1 day'""",
        user_id,
    )
    calories_out = float(row2["today_calories_out"]) if row2 else 0

    total_in = round(bot_calories + fatsecret_calories)

    return {
        "today_calories_in": total_in,
        "today_calories_in_bot": round(bot_calories),
        "today_calories_in_fatsecret": round(fatsecret_calories),
        "today_fatsecret_meals": fatsecret_meals,
        "today_calories_out": round(calories_out),
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
