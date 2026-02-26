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
1. Classify every user message into exactly one intent: log_food, query_data, delete_entry, gym, journal, or general.
2. Respond in the SAME language the user writes in (Ukrainian, English, or mixed).
3. Be concise, friendly, and use emoji sparingly.

INTENT DEFINITIONS:
- log_food: User describes food they ate/drank. Extract each food item with English name (for database lookup), original name, estimated weight in grams, and meal_type (breakfast if before 11:00, lunch if 11:00-16:00, dinner if 16:00-21:00, snack otherwise — use current_time provided). The bot automatically syncs entries to FatSecret if connected, so always log food when user asks.
- query_data: User asks about their health data (sleep, recovery, calories, workouts, mood, history, stats). You have access to WHOOP data (sleep, recovery, strain, activities, weight, heart rate) and FatSecret diary — use all available data when answering.
  WHOOP data available: sleep (duration, stages, performance), recovery (score, HRV, resting HR, SpO2, skin temp), strain, calories burned, workouts, weight, height, max HR.
  WHOOP data NOT available via API (app-only): steps, HR zones, VO₂ max, stress monitor. If user asks about these, explain they're only visible in the WHOOP app directly.
- delete_entry: User wants to remove/undo the last food entry or a specific entry.
- gym: User describes gym exercises, asks about previous workouts, or asks for exercise progression.
  gym_action values:
  - log: User describes exercises done (e.g. "жим лежачи 80кг 3 по 8", "bench press 100kg 4x6 felt heavy")
  - last: User asks what they did last time for an exercise (e.g. "що робив на жимі?", "last bench press?")
  - progress: User asks for progression history (e.g. "прогрес присідань", "show deadlift progress")
- journal: User describes their emotional state, mood, how their day is going, or asks to see journal history/summary.
  journal_action values:
  - entry: User writes about their state/mood/day (e.g. "втомився після зустрічей", "чудовий день, все вдалось")
  - history: User asks to see recent entries (e.g. "покажи щоденник", "що я писав вчора?")
  - summary: User asks for patterns/analysis (e.g. "як я себе почував цього тижня?", "аналіз настрою")
- general: Everything else — greetings, setting calorie goal (extract number), health tips, questions about the bot.

For log_food, also extract:
- food_items: array of objects with name_en (English), name_original (user's language), quantity_g (grams, estimate if not specified), meal_type.
- CRITICAL for name_en: This field is used to search FatSecret database. Use the simplest, most generic English food name. Translate the INGREDIENT, not the dish name or cooking method.
  Examples of CORRECT translations:
  - "рання картопля" / "піра картоплі" → "potato" (NOT "mashed potato" or "early potato")
  - "варена курка" → "chicken breast" (NOT "boiled chicken")
  - "гречка" → "buckwheat" (NOT "buckwheat groats")
  - "сирники" → "cottage cheese pancakes"
  - "борщ" → "borscht"
  - "вівсянка" → "oatmeal"
  When in doubt, use the base ingredient name (potato, rice, chicken, egg, etc.)
- IMPORTANT: For log_food response, just confirm what was added (e.g. "Додано 100г рису"). Do NOT include calorie totals or daily summary — the system appends an accurate balance line automatically.

For gym with log action, extract:
- exercises: array of objects with name_original (user's language), name_en (English), exercise_key (snake_case canonical, e.g. "bench_press", "squat", "deadlift"), weight_kg (number or null), sets (number or null), reps (number or null), rpe (1-10 or null), notes (string or null), set_details (array of {"set": 1, "weight_kg": 80, "reps": 8, "rpe": 8} if user gave per-set detail, else null)
- IMPORTANT: For gym log response, just confirm what was recorded. The system appends previous workout comparison automatically.

For gym with last/progress action, extract:
- exercise_key: the canonical snake_case name to look up. MUST match the same key used when logging.

CRITICAL: exercise_key must be CONSISTENT. Always map to these canonical forms:
- "жим" / "жим лежачи" / "bench" → bench_press
- "жим на похилій" / "incline bench" → incline_bench_press
- "присідання" / "squat" / "присід" → squat
- "станова тяга" / "тяга" / "deadlift" → deadlift
- "жим стоячи" / "армійський жим" / "overhead press" → overhead_press
- "тяга в нахилі" / "barbell row" → barbell_row
- "підтягування" / "pull-up" → pull_up
- "біцепс" / "curls" → bicep_curl
- "трицепс" / "dips" → tricep_dips
Use snake_case English. If exercise not in this list, create a logical snake_case key.

For journal with entry action, extract:
- journal_entry: object with mood_score (1-10, 10=best), energy_level (1-10, 10=highest), tags (array from: stress, energy, social, work, health, gratitude, achievement)
- IMPORTANT: Respond with empathy. If WHOOP recovery/sleep data is available and relevant, weave it into your response naturally. Keep it short if everything is fine, more detailed if there's a problem.

For journal with history/summary action:
- No extra fields needed, the system handles data retrieval.

For general, if user wants to set calorie goal, extract:
- calorie_goal: integer (e.g., 2500)

ALWAYS respond with valid JSON (no markdown fences):
{
  "intent": "log_food|query_data|delete_entry|general|gym|journal",
  "food_items": [{"name_en": "...", "name_original": "...", "quantity_g": 100, "meal_type": "lunch"}],
  "calorie_goal": null,
  "gym_action": null,
  "exercises": [],
  "exercise_key": null,
  "journal_action": null,
  "journal_entry": null,
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
    if user_data.get("gym_prompt"):
        data_context += f" User gym profile: {user_data['gym_prompt']}."
    if user_data.get("recent_gym_exercises"):
        data_context += f" Recent gym exercises: {user_data['recent_gym_exercises']}."
    if user_data.get("recent_journal"):
        data_context += f" Recent journal entries: {user_data['recent_journal']}."

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
                            "WHOOP API 401 for user_id=%s, re-reading tokens from DB",
                            user_id,
                        )
                        # Re-fetch fresh tokens from DB (may have been refreshed
                        # by background job since our initial query)
                        fresh_user = await pool.fetchrow(
                            """SELECT id, whoop_access_token, whoop_refresh_token,
                                      whoop_token_expires_at
                               FROM users WHERE id = $1
                                     AND whoop_access_token IS NOT NULL""",
                            user_id,
                        )
                        if not fresh_user:
                            raise TokenExpiredError("whoop")
                        token = await refresh_token_if_needed(
                            dict(fresh_user), client, pool, force=True,
                        )
                        try:
                            whoop = await fetch_whoop_context(token)
                        except httpx.HTTPStatusError as e2:
                            if e2.response.status_code == 401:
                                logger.warning(
                                    "WHOOP API 401 after refresh for user_id=%s, "
                                    "clearing tokens", user_id,
                                )
                                await pool.execute(
                                    """UPDATE users
                                       SET whoop_access_token = NULL,
                                           whoop_refresh_token = NULL,
                                           whoop_token_expires_at = NULL,
                                           updated_at = NOW()
                                       WHERE id = $1""",
                                    user_id,
                                )
                                raise TokenExpiredError("whoop")
                            raise
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

    # Fetch gym context: user prompt + recent exercises
    pool = await get_pool()
    gym_row = await pool.fetchrow("SELECT gym_prompt FROM users WHERE id = $1", user_id)
    gym_prompt = gym_row["gym_prompt"] if gym_row and gym_row["gym_prompt"] else ""

    gym_rows = await pool.fetch(
        """SELECT exercise_name, exercise_key, weight_kg, sets, reps, rpe, created_at
           FROM gym_exercises WHERE user_id = $1
           ORDER BY created_at DESC LIMIT 5""",
        user_id,
    )
    recent_gym = ""
    if gym_rows:
        parts = []
        for r in gym_rows:
            p = f"{r['exercise_name']}"
            if r["weight_kg"]:
                p += f" {r['weight_kg']}kg"
            if r["sets"] and r["reps"]:
                p += f" {r['sets']}x{r['reps']}"
            p += f" ({r['created_at'].strftime('%d.%m')})"
            parts.append(p)
        recent_gym = "; ".join(parts)

    # Fetch recent journal entries for context
    journal_rows = await pool.fetch(
        """SELECT content, mood_score, energy_level, created_at
           FROM journal_entries WHERE user_id = $1
           ORDER BY created_at DESC LIMIT 3""",
        user_id,
    )
    recent_journal = ""
    if journal_rows:
        parts = []
        for r in journal_rows:
            p = f"\"{r['content'][:80]}\""
            if r["mood_score"]:
                p += f" mood:{r['mood_score']}/10"
            if r["energy_level"]:
                p += f" energy:{r['energy_level']}/10"
            p += f" ({r['created_at'].strftime('%d.%m %H:%M')})"
            parts.append(p)
        recent_journal = "; ".join(parts)

    user_data = {
        "daily_calorie_goal": daily_calorie_goal,
        "gym_prompt": gym_prompt,
        "recent_gym_exercises": recent_gym,
        "recent_journal": recent_journal,
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
        logger.warning("GPT returned invalid JSON, retrying: %s", raw[:200])
        # Retry once — ask GPT to fix its own output
        try:
            fix_response = await client.chat.completions.create(
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": "Fix the following into valid JSON. Return ONLY valid JSON, no explanation."},
                    {"role": "user", "content": raw},
                ],
                temperature=0,
                max_tokens=1024,
                response_format={"type": "json_object"},
            )
            parsed = json.loads(fix_response.choices[0].message.content or "{}")
            logger.info("GPT JSON retry succeeded")
        except Exception:
            logger.error("GPT JSON retry also failed: %s", raw[:200])
            parsed = {
                "intent": "general",
                "food_items": [],
                "calorie_goal": None,
                "response": "Щось пішло не так з обробкою. Спробуй ще раз.",
            }

    parsed.setdefault("intent", "general")
    parsed.setdefault("food_items", [])
    parsed.setdefault("calorie_goal", None)
    parsed.setdefault("gym_action", None)
    parsed.setdefault("exercises", [])
    parsed.setdefault("exercise_key", None)
    parsed.setdefault("journal_action", None)
    parsed.setdefault("journal_entry", None)
    parsed.setdefault("response", "")

    return parsed


async def transcribe_voice(file_bytes: bytes, file_name: str = "voice.ogg") -> str:
    """Transcribe voice audio using OpenAI Whisper. Auto-detects language."""
    logger.info("Whisper transcription: %d bytes", len(file_bytes))
    transcript = await client.audio.transcriptions.create(
        model="whisper-1",
        file=(file_name, file_bytes),
        prompt=(
            "Їжа: картопля, курка, м'ясо, рис, гречка, вівсянка, яйця, молоко, хліб, "
            "сирники, борщ, салат, макарони, каша, сир, масло, риба, овочі, фрукти. "
            "Калорії, грам, грамів, кілограм, сніданок, обід, вечеря, перекус. "
            "Food: chicken, rice, potato, oatmeal, eggs, bread, pasta, salad, fish. "
            "Gym: жим лежачи, присідання, станова тяга, підтягування, "
            "підходи, повторення, кілограм, розминка, тренування. "
            "Journal: настрій, самопочуття, енергія, втома, стрес, вдячність, сон."
        ),
    )
    return transcript.text
