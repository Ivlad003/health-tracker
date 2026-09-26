from __future__ import annotations

import json
import logging
from datetime import datetime

from openai import AsyncOpenAI

from app.config import settings
from app.database import get_pool
from app.timeutils import local_day_bounds_utc, resolve_timezone

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=settings.openai_api_key)

SYSTEM_PROMPT = """You are a personal health assistant Telegram bot. You help users track food, monitor activity, and stay healthy.

RULES:
1. Classify every user message into exactly one intent: log_food, query_data, delete_entry, gym, journal, or general.
2. Respond in the SAME language the user writes in (Ukrainian, English, or mixed).
3. Be concise, friendly, and use emoji sparingly.

INTENT DEFINITIONS:
- log_food: User describes food they ate/drank. Extract each food item with English name (for database lookup), the user's original wording, the weight in grams ONLY if the user stated it, and meal_type (breakfast if before 11:00, lunch if 11:00-16:00, dinner if 16:00-21:00, snack otherwise — use current_time provided). The system matches the user's previously chosen products first and syncs to FatSecret when connected.
- query_data: User asks about their health data (sleep, recovery, calories, workouts, steps, heart rate, mood, history, stats). You have access to WHOOP data, Apple Health samples, and FatSecret diary — use all available data when answering.
  WHOOP data available: sleep (duration, stages, performance), recovery (score, HRV, resting HR, SpO2, skin temp), strain, calories burned, workouts, weight, height, max HR.
  Apple Health data available when synced: steps, active energy, heart rate, sleep, HRV (SDNN), resting heart rate, walking+running distance, exercise minutes, and body mass (latest weigh-in, up to 30 days old).
  Stress: there is no direct stress metric. Use Apple Health HRV as a stress proxy (lower HRV than the user's usual level suggests higher stress/fatigue; higher HRV suggests better recovery) together with WHOOP recovery when available, and say it is an HRV-based estimate.
  WHOOP data NOT available via API (app-only): HR zones, VO₂ max, stress monitor, steps. If user asks about these and Apple Health did not sync them, explain they're only visible in the source app directly.
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
- food_items: array of objects with name_en (English), name_original (user's language, keep their exact words incl. preparation, brand and fat %), quantity_g, quantity_explicit, brand, fat_pct, preparation, meal_type.
- quantity_g: grams ONLY when the user stated a weight (convert kg→g, e.g. "0,2 кг" → 200). If the user gave no weight, set quantity_g to null and quantity_explicit to false. NEVER estimate or invent grams; pieces/spoons/cups are not grams → null. Millilitres are not grams → null.
- quantity_explicit: true only when quantity_g comes from the user's own words.
- brand: product brand if mentioned, else null. fat_pct: number if a fat percentage is mentioned (e.g. "молоко 2.5%" → 2.5), else null.
- preparation: "raw" | "cooked" | null — "cooked" for boiled/baked/fried/cooked (варена, відварна, запечена, смажена, готова), "raw" for raw/dry/uncooked (сира, суха крупа), null when not stated.
- CRITICAL for name_en: This field is used to search FatSecret database. Use the simplest, most generic English food name. Translate the INGREDIENT, not the dish name or cooking method.
  Examples of CORRECT translations:
  - "рання картопля" / "піра картоплі" → "potato" (NOT "mashed potato" or "early potato")
  - "варена курка" → "chicken breast" (NOT "boiled chicken")
  - "гречка" → "buckwheat" (NOT "buckwheat groats")
  - "сирники" → "cottage cheese pancakes"
  - "борщ" → "borscht"
  - "вівсянка" → "oatmeal"
  When in doubt, use the base ingredient name (potato, rice, chicken, egg, etc.)
- IMPORTANT: For log_food the system writes the confirmation itself (it may need to ask the user which product or how many grams). Keep "response" very short and do NOT include calories or totals.

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
  "food_items": [{"name_en": "...", "name_original": "...", "quantity_g": 100, "quantity_explicit": true, "brand": null, "fat_pct": null, "preparation": null, "meal_type": "lunch"}],
  "calorie_goal": null,
  "gym_action": null,
  "exercises": [],
  "exercise_key": null,
  "journal_action": null,
  "journal_entry": null,
  "response": "Your friendly response text here"
}"""


def _row_get(row, key: str, default=None):
    """Read a column from an asyncpg Record or dict, tolerating absence."""
    if row is None:
        return default
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


def _build_context_messages(
    conversation_history: list[dict],
    user_data: dict,
    current_message: str,
) -> list[dict]:
    """Build the messages array for the GPT API call."""
    user_tz = resolve_timezone(user_data.get("timezone"))
    local_now = datetime.now(user_tz)
    calorie_goal = user_data.get("daily_calorie_goal") or 2000
    fs_meals = user_data.get("today_fatsecret_meals", "")
    calories_in = user_data.get("today_calories_in", 0)
    calories_out = user_data.get("today_calories_out", 0)
    calories_source = user_data.get("calories_source", "bot")
    burned_source = user_data.get("calories_burned_source", "none")
    cycle_state = user_data.get("cycle_score_state", "no_data")

    # Eaten calories label with source
    source_label = {
        "fatsecret": "FatSecret diary + bot ledger",
        "ledger": "bot ledger (FatSecret not available)",
    }.get(calories_source, "bot ledger")
    eaten_label = f"Today's calories eaten (source: {source_label}): {calories_in} kcal. "
    if user_data.get("calories_partial"):
        eaten_label += "This total is PARTIAL (some entries are still syncing or lack nutrition data) — say so. "

    # Burned calories label
    if burned_source == "apple_health" and calories_out > 0:
        burned_label = f"Today's active calories burned (Apple Health): {calories_out} kcal. "
    elif burned_source == "apple_health_bmr" and calories_out > 0:
        burned_label = (
            f"Estimated total calories burned today so far: {calories_out} kcal "
            f"(Apple Health active energy + basal metabolism elapsed today). "
        )
    elif burned_source == "bmr" and calories_out > 0:
        burned_label = (
            f"Estimated basal calories burned today so far: {calories_out} kcal "
            f"(no activity data). "
        )
    elif cycle_state == "ESTIMATED" and calories_out > 0:
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
        f"Current local time ({user_tz.key}): {local_now.strftime('%Y-%m-%d %H:%M')}. "
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
        data_context += f" Meals today: {fs_meals}."
    if user_data.get("whoop_sleep"):
        data_context += f" {user_data['whoop_sleep']}."
    if user_data.get("whoop_recovery"):
        data_context += f" {user_data['whoop_recovery']}."
    if user_data.get("whoop_activities"):
        data_context += f" {user_data['whoop_activities']}."
    if user_data.get("whoop_body"):
        data_context += f" {user_data['whoop_body']}."
    if user_data.get("apple_health_summary"):
        data_context += f" {user_data['apple_health_summary']}."
    if user_data.get("bmr_kcal"):
        data_context += (
            f" Estimated basal metabolic rate (Mifflin-St Jeor): {user_data['bmr_kcal']} kcal/day."
        )
    else:
        data_context += (
            " BMR unknown: suggest /profile (birth year, sex, height) if the user asks about"
            " total daily expenditure."
        )
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


EMPTY_WHOOP_CONTEXT = {
    "calories_out": 0, "strain": 0, "workout_count": 0,
    "cycle_score_state": "no_data",
    "sleep_info": "", "recovery_info": "", "activities_info": "", "body_info": "",
}


async def _food_today(pool, user_id: int, user_row, user_tz) -> dict:
    """Today's intake from the ledger/FatSecret union (never double counted)."""
    from app.services.food_logging import UserContext, daily_view

    ctx = UserContext(
        user_id=user_id,
        tz=user_tz,
        fs_token=_row_get(user_row, "fatsecret_access_token") or "",
        fs_secret=_row_get(user_row, "fatsecret_access_secret") or "",
    )
    try:
        view = await daily_view(pool, ctx)
    except Exception:
        logger.warning("Daily food view failed for user_id=%s", user_id, exc_info=True)
        return {"total": 0, "source": "none", "partial": True, "reasons": ["unavailable"],
                "meals_text": "", "expired": False}
    if view.remote_connected and view.remote_ok:
        source = "fatsecret"
    elif view.entries:
        source = "ledger"
    else:
        source = "none"
    meals_text = "; ".join(
        f"{e['name']} ({round(e['energy_kcal']) if e['energy_kcal'] is not None else '?'} kcal)"
        for e in view.entries[:10]
    )
    return {"total": view.total_rounded, "source": source, "partial": view.partial,
            "reasons": view.reasons, "meals_text": meals_text, "expired": view.expired}


async def get_today_stats(user_id: int) -> dict:
    """Fetch today's stats from FatSecret, WHOOP, and stored Apple Health samples."""
    logger.info("Fetching today stats for user_id=%s", user_id)
    pool = await get_pool()

    expired_services: list[str] = []
    user_row = await pool.fetchrow(
        """SELECT fatsecret_access_token, fatsecret_access_secret, timezone,
                  birth_year, sex, height_cm
           FROM users WHERE id = $1""",
        user_id,
    )
    user_tz = resolve_timezone(_row_get(user_row, "timezone"))

    # Calories eaten: local ledger ∪ live FatSecret diary, linked entries
    # counted once (app/services/food_logging.py).
    food = await _food_today(pool, user_id, user_row, user_tz)
    if food["expired"]:
        expired_services.append("fatsecret")

    # Fetch ALL WHOOP data directly from API (real-time, not from DB)
    from app.services import whoop_sync

    whoop = dict(EMPTY_WHOOP_CONTEXT)
    try:
        context = await whoop_sync.get_whoop_context_for_user(pool, user_id, tz=user_tz)
        if context is not None:
            whoop = context
    except whoop_sync.TokenExpiredError:
        expired_services.append("whoop")
    except Exception:
        logger.exception("Failed to fetch WHOOP data for user_id=%s", user_id)

    from app.services.apple_health import get_apple_health_summary, get_latest_body_mass

    today_start_utc, tomorrow_start_utc = local_day_bounds_utc(user_tz)
    apple_health = await get_apple_health_summary(
        pool,
        user_id,
        start_at=today_start_utc,
        end_at=tomorrow_start_utc,
    )

    # Weight is rarely measured daily: fall back to the latest weigh-in.
    latest_body_mass = None
    if not apple_health.get("body_mass_kg"):
        latest_body_mass = await get_latest_body_mass(
            pool, user_id, today=today_start_utc.astimezone(user_tz).date(),
        )
    if latest_body_mass:
        apple_health["body_mass_kg"] = latest_body_mass["kg"]
        note = (
            f"Apple Health latest body mass: {latest_body_mass['kg']} kg "
            f"(measured {latest_body_mass['metric_date']:%Y-%m-%d})"
        )
        apple_health["summary"] = ". ".join(p for p in (apple_health["summary"], note) if p)

    from app.services.health_workouts import get_workouts_summary

    workouts = await get_workouts_summary(
        pool, user_id, start_at=today_start_utc, end_at=tomorrow_start_utc,
    )
    if workouts["summary"]:
        apple_health["summary"] = ". ".join(
            p for p in (apple_health["summary"], workouts["summary"]) if p
        )

    # BMR (Mifflin-St Jeor): profile from /profile, weight from Apple Health
    # or WHOOP, height from the profile or WHOOP.
    from app.services.bmr import compute_bmr, prorated_bmr

    weight_kg = apple_health.get("body_mass_kg") or whoop.get("body_weight_kg") or None
    height_cm = _row_get(user_row, "height_cm") or (
        (whoop.get("body_height_m") or 0) * 100 or None
    )
    local_now = datetime.now(user_tz)
    bmr_kcal = compute_bmr(
        birth_year=_row_get(user_row, "birth_year"),
        sex=_row_get(user_row, "sex"),
        height_cm=float(height_cm) if height_cm else None,
        weight_kg=float(weight_kg) if weight_kg else None,
        today=local_now.date(),
    )

    total_in = food["total"]
    calories_source = food["source"]
    # WHOOP cycle calories already include basal burn -> used as-is.
    calories_out = whoop["calories_out"]
    calories_burned_source = "whoop" if calories_out > 0 else "none"
    if calories_out <= 0:
        # Apple Health "active energy" excludes basal burn; add the share of
        # today's BMR elapsed so far when the profile allows it.
        active = apple_health["active_energy_kcal"]
        basal_so_far = prorated_bmr(bmr_kcal, local_now) if bmr_kcal else 0
        if active > 0 and basal_so_far:
            calories_out = active + basal_so_far
            calories_burned_source = "apple_health_bmr"
        elif active > 0:
            calories_out = active
            calories_burned_source = "apple_health"
        elif basal_so_far:
            calories_out = basal_so_far
            calories_burned_source = "bmr"

    logger.info("Stats for user_id=%s: in=%d kcal (src=%s), out=%d kcal, strain=%.1f, workouts=%d",
                user_id, total_in, calories_source,
                calories_out, whoop["strain"], whoop["workout_count"])

    return {
        "today_calories_in": total_in,
        "calories_source": calories_source,
        "calories_partial": food["partial"],
        "calories_partial_reasons": food["reasons"],
        "today_fatsecret_meals": food["meals_text"],
        "today_calories_out": calories_out,
        "calories_burned_source": calories_burned_source,
        "today_strain": whoop["strain"],
        "today_workout_count": whoop["workout_count"],
        "cycle_score_state": whoop["cycle_score_state"],
        "whoop_sleep": whoop["sleep_info"],
        "whoop_recovery": whoop["recovery_info"],
        "whoop_activities": whoop["activities_info"],
        "whoop_body": whoop["body_info"],
        "apple_health_steps": apple_health["steps"],
        "apple_health_active_energy_kcal": apple_health["active_energy_kcal"],
        "apple_health_avg_heart_rate": apple_health["avg_heart_rate"],
        "apple_health_avg_hrv_ms": apple_health["avg_hrv_ms"],
        "apple_health_sleep_hours": apple_health["sleep_hours"],
        "apple_health_resting_heart_rate": apple_health.get("resting_heart_rate", 0),
        "apple_health_distance_km": apple_health.get("distance_km", 0),
        "apple_health_exercise_minutes": apple_health.get("exercise_minutes", 0),
        "apple_health_body_mass_kg": apple_health.get("body_mass_kg", 0),
        "apple_health_workout_count": workouts["count"],
        "apple_health_workouts": workouts["summary"],
        "bmr_kcal": bmr_kcal,
        "apple_health_metric_counts": apple_health["metric_counts"],
        "apple_health_latest_metric_at": apple_health["latest_metric_at"],
        "apple_health_summary": apple_health["summary"],
        "expired_services": expired_services,
        "timezone": user_tz.key,
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
