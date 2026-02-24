# AI Telegram Health Bot — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a freeform bilingual AI Telegram bot inside the existing FastAPI app that logs food via natural language, answers health queries, deletes entries, sets calorie goals, and sends proactive morning/evening briefings — all powered by GPT-4o intent classification and FatSecret nutrition lookup.

**Architecture:** 3 new service modules (`telegram_bot.py`, `ai_assistant.py`, `briefings.py`) running inside the same FastAPI process. The Telegram bot uses `python-telegram-bot` with long polling as an asyncio task started/stopped in the FastAPI lifespan. GPT-4o handles intent classification + response generation in a single API call per message. Whisper handles voice-to-text. APScheduler fires morning (8:00 Kyiv) and evening (21:00 Kyiv) briefings plus a daily conversation cleanup job.

**Tech Stack:** Python 3.12 (Docker) / 3.9 (local), FastAPI, python-telegram-bot v21+, openai v1+, asyncpg, APScheduler, httpx

**Reference files:**
- Design doc: `docs/en/ai-bot-design.md`
- DB schema (production): `database/migrations/002_health_tracker_schema.sql`
- FatSecret columns: `database/migrations/003_fatsecret_columns.sql`
- Existing services: `app/services/fatsecret_api.py`, `app/services/whoop_sync.py`
- Config: `app/config.py`
- Scheduler: `app/scheduler.py`
- App lifespan: `app/main.py`

---

## Task 1: Database Migration — `conversation_messages` table

**Files:**
- Create: `database/migrations/004_conversation_messages.sql`

**Step 1: Write the migration SQL**

Create `database/migrations/004_conversation_messages.sql`:

```sql
-- Conversation Messages for AI Bot context
-- Version: 4.0.0
-- Created: 2026-02-24

BEGIN;

CREATE TABLE IF NOT EXISTS conversation_messages (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL,           -- 'user' or 'assistant'
    content TEXT NOT NULL,
    intent VARCHAR(50),                  -- classified intent (log_food, query_data, delete_entry, general)
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conv_messages_user_created
    ON conversation_messages(user_id, created_at);

COMMIT;
```

**Step 2: Apply the migration**

```bash
psql "$DATABASE_URL" -f database/migrations/004_conversation_messages.sql
```

Expected output: `BEGIN`, `CREATE TABLE`, `CREATE INDEX`, `COMMIT`

**Step 3: Commit**

```bash
git add database/migrations/004_conversation_messages.sql
git commit -m "Add conversation_messages table migration for AI bot context"
```

---

## Task 2: Update Dependencies — `requirements.txt`

**Files:**
- Modify: `requirements.txt`

**Step 1: Add python-telegram-bot and openai**

Add before the test deps:

```txt
python-telegram-bot==21.10
openai==1.61.0
```

**Step 2: Install**

```bash
pip install -r requirements.txt
```

**Step 3: Commit**

```bash
git add requirements.txt
git commit -m "Add python-telegram-bot and openai dependencies"
```

---

## Task 3: AI Assistant Service — `app/services/ai_assistant.py`

**Files:**
- Create: `app/services/ai_assistant.py`

GPT brain: intent classification, food parsing, response generation, voice transcription.

**Step 1: Create the module**

```python
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

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
- query_data: User asks about their health data (sleep, recovery, calories, workouts, mood, history, stats).
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
    now = datetime.now(timezone.utc)
    data_context = (
        f"Current UTC time: {now.strftime('%Y-%m-%d %H:%M')}. "
        f"User timezone: Europe/Kyiv (UTC+2). "
        f"Local time approx: {now.hour + 2}:{now.strftime('%M')}. "
        f"User calorie goal: {user_data.get('daily_calorie_goal', 2000)} kcal. "
        f"Today's calories in: {user_data.get('today_calories_in', 0)} kcal. "
        f"Today's calories burned: {user_data.get('today_calories_out', 0)} kcal."
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": f"USER DATA: {data_context}"},
    ]

    for msg in conversation_history:
        messages.append({"role": msg["role"], "content": msg["content"]})

    messages.append({"role": "user", "content": current_message})
    return messages


async def get_today_stats(user_id: int) -> dict:
    """Fetch today's calorie stats for the user."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """SELECT COALESCE(SUM(fe.calories), 0) AS today_calories_in
           FROM food_entries fe
           WHERE fe.user_id = $1
             AND fe.logged_at >= CURRENT_DATE
             AND fe.logged_at < CURRENT_DATE + INTERVAL '1 day'""",
        user_id,
    )
    calories_in = float(row["today_calories_in"]) if row else 0

    row2 = await pool.fetchrow(
        """SELECT COALESCE(SUM(wa.calories), 0) AS today_calories_out
           FROM whoop_activities wa
           WHERE wa.user_id = $1
             AND wa.started_at >= CURRENT_DATE
             AND wa.started_at < CURRENT_DATE + INTERVAL '1 day'""",
        user_id,
    )
    calories_out = float(row2["today_calories_out"]) if row2 else 0

    return {
        "today_calories_in": round(calories_in),
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

    raw = response.choices[0].message.content
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
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
```

**Step 2: Commit**

```bash
git add app/services/ai_assistant.py
git commit -m "Add AI assistant service with GPT intent classification and Whisper STT"
```

---

## Task 4: Telegram Bot Service — `app/services/telegram_bot.py`

**Files:**
- Create: `app/services/telegram_bot.py`

Bot setup, long polling, message handler dispatch, food logging, entry deletion.

**Step 1: Create the module**

```python
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
    daily_calorie_goal = user["daily_calorie_goal"]

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
            goal = int(gpt_result["calorie_goal"])
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
```

**Step 2: Commit**

```bash
git add app/services/telegram_bot.py
git commit -m "Add Telegram bot service with message handling, food logging, and voice support"
```

---

## Task 5: Briefings Service — `app/services/briefings.py`

**Files:**
- Create: `app/services/briefings.py`

Morning/evening briefings and conversation cleanup.

**Step 1: Create the module**

```python
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
            goal = user["daily_calorie_goal"]

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
            goal = user["daily_calorie_goal"]

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
```

**Step 2: Commit**

```bash
git add app/services/briefings.py
git commit -m "Add briefings service with morning/evening summaries and conversation cleanup"
```

---

## Task 6: Update Scheduler — Add Briefing and Cleanup Jobs

**Files:**
- Modify: `app/scheduler.py`

**Step 1: Update scheduler with new cron jobs**

```python
from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from app.services.whoop_sync import sync_whoop_data

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


def start_scheduler():
    # WHOOP data sync every hour (existing)
    scheduler.add_job(
        sync_whoop_data,
        trigger=IntervalTrigger(hours=1),
        id="whoop_data_sync",
        name="WHOOP Data Sync (hourly)",
        replace_existing=True,
        next_run_time=datetime.now(),
    )

    # Morning briefing at 06:00 UTC (08:00 Kyiv)
    from app.services.briefings import morning_briefing
    scheduler.add_job(
        morning_briefing,
        trigger=CronTrigger(hour=6, minute=0, timezone="UTC"),
        id="morning_briefing",
        name="Morning Briefing (08:00 Kyiv)",
        replace_existing=True,
    )

    # Evening summary at 19:00 UTC (21:00 Kyiv)
    from app.services.briefings import evening_summary
    scheduler.add_job(
        evening_summary,
        trigger=CronTrigger(hour=19, minute=0, timezone="UTC"),
        id="evening_summary",
        name="Evening Summary (21:00 Kyiv)",
        replace_existing=True,
    )

    # Conversation cleanup daily at 03:00 UTC
    from app.services.briefings import cleanup_old_conversations
    scheduler.add_job(
        cleanup_old_conversations,
        trigger=CronTrigger(hour=3, minute=0, timezone="UTC"),
        id="conversation_cleanup",
        name="Conversation Cleanup (daily)",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        "Scheduler started — WHOOP sync 1h, "
        "morning 08:00 Kyiv, evening 21:00 Kyiv, cleanup 03:00 UTC"
    )


def stop_scheduler():
    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")
```

**Step 2: Commit**

```bash
git add app/scheduler.py
git commit -m "Add morning briefing, evening summary, and cleanup to scheduler"
```

---

## Task 7: Update App Lifespan and Config

**Files:**
- Modify: `app/main.py`
- Modify: `app/config.py`

**Step 1: Add `openai_model` to config**

In `app/config.py`, add after `openai_api_key`:

```python
    openai_model: str = "gpt-4o"
```

**Step 2: Update main.py lifespan to start/stop Telegram bot**

Add to the lifespan in `app/main.py`:

```python
from app.services.telegram_bot import start_bot, stop_bot

# In the lifespan, after start_scheduler():
await start_bot()

# Before close_pool():
await stop_bot()
```

**Step 3: Commit**

```bash
git add app/main.py app/config.py
git commit -m "Integrate Telegram bot into app lifespan, add openai_model config"
```

---

## Task 8: Smoke Test and Deploy

**Step 1: Verify imports**

```bash
python -c "from app.services.ai_assistant import classify_and_respond; print('OK')"
python -c "from app.services.telegram_bot import start_bot; print('OK')"
python -c "from app.services.briefings import morning_briefing; print('OK')"
```

**Step 2: Start locally and test**

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Send "Hello" to the bot in Telegram. Verify it responds.

**Step 3: Build Docker and deploy**

```bash
docker build -t health-tracker .
git push origin main
```

---

## Summary

| Task | File(s) | Purpose |
|------|---------|---------|
| 1 | `database/migrations/004_conversation_messages.sql` | Conversation history table |
| 2 | `requirements.txt` | Add python-telegram-bot, openai |
| 3 | `app/services/ai_assistant.py` | GPT intent + Whisper STT |
| 4 | `app/services/telegram_bot.py` | Bot handlers, food logging |
| 5 | `app/services/briefings.py` | Morning/evening briefings + cleanup |
| 6 | `app/scheduler.py` | Cron jobs for briefings + cleanup |
| 7 | `app/main.py`, `app/config.py` | Lifespan integration + config |
| 8 | Smoke test + deploy | Verify and ship |
