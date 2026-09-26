from __future__ import annotations

import logging
from urllib.parse import quote, urlencode

import re

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    WebAppInfo,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
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
from app.services import food_bot
from app.services import food_logging as ledger
from app.services.gym_service import (
    log_exercises,
    get_last_exercise,
    get_exercise_progress,
)
from app.services.journal_service import (
    save_journal_entry,
    get_journal_history,
    get_journal_summary_data,
)
from app.services.apple_health import ensure_apple_health_sync
from app.security import sign_oauth_state
from app.services.whoop_sync import WHOOP_AUTH_URL, WHOOP_SCOPES
from app.timeutils import resolve_timezone
from app.i18n import SUPPORTED_LANGUAGES, normalize_language, t

logger = logging.getLogger(__name__)

_application: Application | None = None
# telegram_user_id -> "uk" | "en" (filled by _ensure_user and /language).
_language_cache: dict[int, str] = {}


def invalidate_language_cache(telegram_user_id: int) -> None:
    """Forget a cached language (the Web App changed users.language)."""
    _language_cache.pop(telegram_user_id, None)


def _lang(update: Update) -> str:
    """Language for replies: stored users.language, else Telegram language_code."""
    user = update.effective_user
    cached = _language_cache.get(user.id)
    if cached:
        return cached
    return normalize_language(getattr(user, "language_code", None))


async def user_language(telegram_user_id: int) -> str:
    """Language for proactive messages (callbacks, jobs). Never raises."""
    cached = _language_cache.get(telegram_user_id)
    if cached:
        return cached
    try:
        pool = await get_pool()
        value = await pool.fetchval(
            "SELECT language FROM users WHERE telegram_user_id = $1", telegram_user_id,
        )
    except Exception:
        logger.debug("Language lookup failed for %s", telegram_user_id, exc_info=True)
        return normalize_language(None)
    lang = normalize_language(value if isinstance(value, str) else None)
    _language_cache[telegram_user_id] = lang
    return lang


async def send_message(telegram_user_id: int, text: str) -> None:
    """Send a message to a user via the bot. Used by OAuth callbacks."""
    if _application is None:
        logger.warning("Bot not started, cannot send message to %s", telegram_user_id)
        return
    await _application.bot.send_message(
        chat_id=telegram_user_id, text=text, disable_web_page_preview=True,
    )


async def _ensure_user(
    telegram_user_id: int,
    username: str | None,
    language_code: str | None = None,
) -> dict:
    """Get or create user by telegram_user_id (new users inherit Telegram language)."""
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT id, daily_calorie_goal, language FROM users WHERE telegram_user_id = $1",
        telegram_user_id,
    )
    if not row:
        row = await pool.fetchrow(
            """INSERT INTO users (telegram_user_id, telegram_username, language)
               VALUES ($1, $2, $3)
               RETURNING id, daily_calorie_goal, language""",
            telegram_user_id,
            username or "",
            normalize_language(language_code),
        )
        logger.info("Created new user: telegram_user_id=%s, db_id=%s", telegram_user_id, row["id"])
    lang = normalize_language(row["language"])
    _language_cache[telegram_user_id] = lang
    return {"id": row["id"], "daily_calorie_goal": row["daily_calorie_goal"], "language": lang}


def _reply_markup(reply: food_bot.BotReply, lang: str) -> InlineKeyboardMarkup | None:
    rows = [
        [InlineKeyboardButton(label, callback_data=data) for label, data in row]
        for row in reply.buttons
    ]
    if reply.webapp_url:
        rows.append([InlineKeyboardButton(
            t("food_btn_open_app", lang), web_app=WebAppInfo(url=reply.webapp_url),
        )])
    return InlineKeyboardMarkup(rows) if rows else None


async def _send_food_reply(message, reply: food_bot.BotReply, lang: str, user_id: int) -> None:
    sent = await message.reply_text(reply.text, reply_markup=_reply_markup(reply, lang))
    if reply.draft_id and sent is not None:
        pool = await get_pool()
        await ledger.set_reply_message(pool, reply.draft_id, user_id, sent.message_id)


_QUANTITY_ONLY_RE = re.compile(
    r"^\s*\d{1,5}(?:[.,]\d{1,2})?\s*(?:г|гр|грам\w*|g|gr|grams?|кг|kg)\s*\.?\s*$", re.IGNORECASE,
)


async def _try_draft_reply(update: Update, user_id: int, lang: str, text: str) -> bool:
    """Route a quantity reply to its draft. Returns True when handled."""
    message = update.message
    pool = await get_pool()
    ctx = await ledger.load_user_context(pool, user_id)
    ctx.language = lang
    reply_to = message.reply_to_message
    if reply_to is not None:
        draft = await ledger.find_reply_draft(pool, user_id, message.chat_id, reply_to.message_id)
        if draft is not None:
            reply = await food_bot.apply_reply_text(pool, ctx, draft, text)
            if reply is not None:
                await _send_food_reply(message, reply, lang, user_id)
                return True
        return False
    if not _QUANTITY_ONLY_RE.match(text):
        return False
    draft, count = await food_bot.pending_weight_draft(pool, ctx)
    if count == 0:
        return False
    if draft is None:
        await message.reply_text(t("food_which_draft", lang))
        return True
    reply = await food_bot.apply_reply_text(pool, ctx, draft, text)
    if reply is None:
        return False
    await _send_food_reply(message, reply, lang, user_id)
    return True


async def _handle_gym(user_id: int, gpt_result: dict, lang: str = "uk") -> str | None:
    """Handle gym intent: log exercises, show last workout, show progress."""
    action = gpt_result.get("gym_action", "log")

    if action == "log" and gpt_result.get("exercises"):
        logged = await log_exercises(user_id, gpt_result["exercises"])
        lines = []
        for ex in logged:
            line = f"  {ex['name']}"
            parts = []
            if ex.get("weight_kg"):
                parts.append(f"{ex['weight_kg']}{t('kg', lang)}")
            if ex.get("sets") and ex.get("reps"):
                parts.append(f"{ex['sets']}×{ex['reps']}")
            if parts:
                line += f" — {', '.join(parts)}"
            if ex.get("prev"):
                p = ex["prev"]
                prev_parts = []
                if p.get("weight_kg"):
                    prev_parts.append(f"{p['weight_kg']}{t('kg', lang)}")
                if p.get("sets") and p.get("reps"):
                    prev_parts.append(f"{p['sets']}×{p['reps']}")
                if prev_parts:
                    line += t("gym_prev", lang, date=p["date"], parts=", ".join(prev_parts))
            lines.append(line)
        return t("gym_logged", lang) + "\n".join(lines)

    elif action == "last":
        key = gpt_result.get("exercise_key", "")
        if not key:
            return t("gym_need_exercise_last", lang)
        ex = await get_last_exercise(user_id, key)
        if not ex:
            return t("gym_no_records", lang, key=key)
        # GPT already has recent gym context and generated a response
        return None

    elif action == "progress":
        key = gpt_result.get("exercise_key", "")
        if not key:
            return t("gym_need_exercise_progress", lang)
        history = await get_exercise_progress(user_id, key)
        if not history:
            return t("gym_no_progress", lang, key=key)
        lines = []
        for entry in history:
            date_str = entry["created_at"].strftime("%d.%m")
            parts = []
            if entry.get("weight_kg"):
                parts.append(f"{entry['weight_kg']}{t('kg', lang)}")
            if entry.get("sets") and entry.get("reps"):
                parts.append(f"{entry['sets']}×{entry['reps']}")
            lines.append(f"  {date_str} — {', '.join(parts)}")
        if len(history) >= 2 and history[0].get("weight_kg") and history[-1].get("weight_kg"):
            first_w = history[0]["weight_kg"]
            last_w = history[-1]["weight_kg"]
            diff = last_w - first_w
            pct = round(diff / first_w * 100, 1) if first_w else 0
            sign = "+" if diff >= 0 else ""
            lines.append(f"\n  📈 {sign}{diff}{t('kg', lang)} ({sign}{pct}%)")
        return t("gym_progress", lang) + "\n".join(lines)

    return None


async def _handle_calorie_goal(user_id: int, calorie_goal: int) -> None:
    """Update the daily calorie goal (date-effective from the user's today)."""
    from datetime import datetime as _dt

    from app.services.preferences import set_goal

    pool = await get_pool()
    tz_name = await pool.fetchval("SELECT timezone FROM users WHERE id = $1", user_id)
    today = _dt.now(resolve_timezone(tz_name)).date()
    try:
        await set_goal(pool, user_id, calories=calorie_goal, effective_date=today)
    except Exception:
        logger.warning("Goal history write failed; updating current goal only", exc_info=True)
        await pool.execute(
            "UPDATE users SET daily_calorie_goal = $1 WHERE id = $2", calorie_goal, user_id,
        )


async def _handle_journal(
    user_id: int, gpt_result: dict, message_text: str, lang: str = "uk",
) -> str | None:
    """Handle journal intent: save entry, show history, show summary."""
    action = gpt_result.get("journal_action", "entry")

    if action == "entry":
        je = gpt_result.get("journal_entry") or {}
        await save_journal_entry(
            user_id=user_id,
            content=message_text,
            mood_score=je.get("mood_score"),
            energy_level=je.get("energy_level"),
            tags=je.get("tags"),
        )
        # GPT already generated a context-aware empathetic response
        return None

    elif action == "history":
        entries = await get_journal_history(user_id, days=7)
        if not entries:
            return t("journal_empty", lang)
        lines = []
        for e in entries:
            date_str = e["created_at"].strftime("%d.%m %H:%M")
            text = e["content"][:100]
            mood = f" 😊{e['mood_score']}" if e["mood_score"] else ""
            energy = f" ⚡{e['energy_level']}" if e["energy_level"] else ""
            lines.append(f"  {date_str}{mood}{energy}\n    {text}")
        return t("journal_title", lang) + "\n\n".join(lines)

    elif action == "summary":
        data = await get_journal_summary_data(user_id, days=7)
        if data["entries_count"] == 0:
            return t("journal_no_week", lang)
        # GPT has recent journal context and will generate a natural summary
        return None

    return None


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Main handler for all incoming Telegram messages."""
    if not update.message or not update.effective_user:
        return

    telegram_user_id = update.effective_user.id
    username = update.effective_user.username

    user = await _ensure_user(
        telegram_user_id, username, getattr(update.effective_user, "language_code", None),
    )
    user_id = user["id"]
    lang = _lang(update)
    daily_calorie_goal = user["daily_calorie_goal"] or 2000

    logger.info("Incoming message from user_id=%s (tg=%s), type=%s",
                user_id, telegram_user_id,
                "voice" if update.message.voice else "text")

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
            await update.message.reply_text(t("voice_failed", lang))
            return
    elif update.message.text:
        message_text = update.message.text
    else:
        return

    # Ignore empty or meaningless messages (single chars, dashes, dots)
    cleaned = message_text.strip().strip(".-–—…_ ")
    if not cleaned:
        return

    try:
        if await _try_draft_reply(update, user_id, lang, message_text):
            return
    except Exception:
        logger.exception("Draft reply handling failed for user %s", telegram_user_id)

    await save_conversation_message(user_id, "user", message_text)

    logger.info("Processing message for user_id=%s: '%s'",
                user_id, message_text[:100])

    try:
        gpt_result = await classify_and_respond(user_id, daily_calorie_goal, message_text)
    except Exception:
        logger.exception("GPT call failed for user %s", telegram_user_id)
        await update.message.reply_text(t("gpt_failed", lang))
        return

    intent = gpt_result["intent"]
    response_text = gpt_result["response"]

    logger.info("GPT result for user_id=%s: intent=%s, food_items=%d",
                user_id, intent, len(gpt_result.get("food_items", [])))

    expired_services = []

    try:
        if intent == "log_food" and gpt_result["food_items"]:
            pool = await get_pool()
            ctx = await ledger.load_user_context(pool, user_id)
            ctx.language = lang
            reply = await food_bot.handle_food_items(
                pool, ctx, gpt_result["food_items"],
                chat_id=update.message.chat_id,
                message_id=update.message.message_id,
                origin="bot_voice" if update.message.voice else "bot_text",
            )
            await save_conversation_message(user_id, "assistant", reply.text, intent)
            await _send_food_reply(update.message, reply, lang, user_id)
            logger.info("Food reply sent to user_id=%s", user_id)
            return

        elif intent == "delete_entry":
            pool = await get_pool()
            ctx = await ledger.load_user_context(pool, user_id)
            ctx.language = lang
            deleted = await food_bot.undo_last(pool, ctx)
            if not deleted:
                response_text = t("nothing_to_delete", lang)
            else:
                response_text = t("food_undone", lang, name=deleted)

        elif intent == "gym":
            gym_response = await _handle_gym(user_id, gpt_result, lang)
            if gym_response:
                response_text = gym_response

        elif intent == "journal":
            journal_response = await _handle_journal(user_id, gpt_result, message_text, lang)
            if journal_response:
                response_text = journal_response

        elif intent == "general" and gpt_result.get("calorie_goal"):
            try:
                goal = int(float(gpt_result["calorie_goal"]))
            except (ValueError, TypeError):
                goal = 0
            if 500 <= goal <= 10000:
                await _handle_calorie_goal(user_id, goal)

    except Exception:
        logger.exception("Intent handler failed for user %s, intent=%s", telegram_user_id, intent)
        # Never echo GPT's "added ..." when nothing was recorded.
        response_text = (
            t("intent_failed", lang) if intent in ("log_food", "delete_entry")
            else (response_text or t("intent_failed", lang))
        )

    # Append reconnect hints for expired tokens
    if expired_services:
        reconnect_lines = []
        if "whoop" in expired_services:
            reconnect_lines.append("  ⌚ WHOOP → /connect_whoop")
        if "fatsecret" in expired_services:
            reconnect_lines.append("  🥗 FatSecret → /connect_fatsecret")
        response_text += t("reconnect_header", lang) + "\n".join(reconnect_lines)

    await save_conversation_message(user_id, "assistant", response_text, intent)
    await update.message.reply_text(response_text)
    logger.info("Reply sent to user_id=%s, intent=%s, len=%d",
                user_id, intent, len(response_text))


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Food photos: barcode, nutrition label, packaging or plate (+ caption grams)."""
    message = update.message
    if not message or not update.effective_user:
        return
    user = await _ensure_user(
        update.effective_user.id, update.effective_user.username,
        getattr(update.effective_user, "language_code", None),
    )
    lang = _lang(update)
    file_obj = None
    unique_id = None
    size = 0
    if message.photo:
        photo = message.photo[-1]  # largest size keeps barcode resolution
        file_obj, unique_id, size = photo, photo.file_unique_id, photo.file_size or 0
    elif message.document and (message.document.mime_type or "").lower() in (
        "image/jpeg", "image/png", "image/webp",
    ):
        doc = message.document
        file_obj, unique_id, size = doc, doc.file_unique_id, doc.file_size or 0
    if file_obj is None:
        return
    if size and size > settings.media_max_bytes:
        await message.reply_text(t("food_image_too_large", lang))
        return
    try:
        tg_file = await file_obj.get_file()
        data = bytes(await tg_file.download_as_bytearray())
    except Exception:
        logger.exception("Photo download failed for user %s", update.effective_user.id)
        await message.reply_text(t("food_photo_failed", lang))
        return
    pool = await get_pool()
    ctx = await ledger.load_user_context(pool, user["id"])
    ctx.language = lang
    try:
        reply = await food_bot.handle_photo(
            pool, ctx, data,
            caption=message.caption or "",
            chat_id=message.chat_id,
            message_id=message.message_id,
            media_group_id=message.media_group_id,
            reply_to=message.reply_to_message.message_id if message.reply_to_message else None,
            file_unique_id=unique_id,
        )
    except Exception:
        logger.exception("Photo handling failed for user %s", update.effective_user.id)
        await message.reply_text(t("food_photo_failed", lang))
        return
    if reply is not None:
        await _send_food_reply(message, reply, lang, user["id"])


async def handle_food_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline buttons on food drafts/entries. Ownership + version checked server-side."""
    query = update.callback_query
    if query is None or not update.effective_user:
        return
    await query.answer()
    user = await _ensure_user(
        update.effective_user.id, update.effective_user.username,
        getattr(update.effective_user, "language_code", None),
    )
    lang = _lang(update)
    pool = await get_pool()
    ctx = await ledger.load_user_context(pool, user["id"])
    ctx.language = lang
    try:
        reply = await food_bot.handle_callback(pool, ctx, query.data or "")
    except Exception:
        logger.exception("Food callback failed for user %s", update.effective_user.id)
        reply = food_bot.BotReply(t("intent_failed", lang))
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass  # message too old / already edited
    if query.message is not None:
        await _send_food_reply(query.message, reply, lang, user["id"])


async def handle_app(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/app — open the Telegram Web App."""
    if not update.message or not update.effective_user:
        return
    lang = _lang(update)
    url = food_bot.webapp_link()
    if not url:
        await update.message.reply_text(t("app_unavailable", lang))
        return
    markup = InlineKeyboardMarkup([[InlineKeyboardButton(t("app_button", lang), web_app=WebAppInfo(url=url))]])
    await update.message.reply_text(t("app_open", lang), reply_markup=markup)


HELP_TEXT = t("help", "uk")
APPLE_HEALTH_HELP_TEXT = t("apple_help", "uk")


async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start and /help commands."""
    if not update.message or not update.effective_user:
        return

    await update.message.reply_text(t("help", _lang(update)), disable_web_page_preview=True)


async def handle_apple_health_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /apple_health_help command."""
    if not update.message or not update.effective_user:
        return

    await update.message.reply_text(t("apple_help", _lang(update)), disable_web_page_preview=True)


async def handle_connect_whoop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /connect_whoop command."""
    if not update.message or not update.effective_user:
        return

    telegram_id = update.effective_user.id
    url = f"{WHOOP_AUTH_URL}?" + urlencode(
        {
            "client_id": settings.whoop_client_id,
            "redirect_uri": settings.whoop_redirect_uri,
            "response_type": "code",
            "scope": WHOOP_SCOPES,
            # Signed + expiring: a bare Telegram id let anyone bind their own
            # WHOOP account to someone else's chat.
            "state": sign_oauth_state(telegram_id, "whoop"),
        },
        quote_via=quote,
    )
    await update.message.reply_text(
        t("connect_whoop", _lang(update), url=url), disable_web_page_preview=True,
    )


async def handle_connect_fatsecret(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /connect_fatsecret command."""
    if not update.message or not update.effective_user:
        return

    telegram_id = update.effective_user.id
    state = quote(sign_oauth_state(telegram_id, "fatsecret"), safe="")
    url = f"{settings.app_base_url}/fatsecret/connect?state={state}"
    await update.message.reply_text(
        t("connect_fatsecret", _lang(update), url=url), disable_web_page_preview=True,
    )


async def handle_connect_apple_health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /connect_apple_health command."""
    if not update.message or not update.effective_user:
        return

    user = await _ensure_user(update.effective_user.id, update.effective_user.username, getattr(update.effective_user, "language_code", None))
    pool = await get_pool()
    sync = await ensure_apple_health_sync(
        pool,
        user_id=user["id"],
        sync_frequency_hours=settings.apple_health_sync_hours,
    )
    webhook_url = f"{settings.app_base_url}/api/v1/health/apple-health/sync"
    shortcut_params = urlencode({
        'userId': update.effective_user.id,
        'token': sync['secret_key'],
    })
    shortcut_url = f"{webhook_url}?{shortcut_params}"
    shortcut_import_url = f"{settings.app_base_url}/api/v1/health/apple-health/shortcut"
    await update.message.reply_text(
        t(
            "connect_apple",
            _lang(update),
            shortcut_import_url=shortcut_import_url,
            shortcut_url=shortcut_url,
        ),
        disable_web_page_preview=True,
    )


async def handle_journal_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /journal_time command — set reminder times."""
    if not update.message or not update.effective_user:
        return

    user = await _ensure_user(update.effective_user.id, update.effective_user.username, getattr(update.effective_user, "language_code", None))
    text = (update.message.text or "").replace("/journal_time", "", 1).strip()

    if not text:
        pool = await get_pool()
        row = await pool.fetchrow(
            "SELECT journal_time_1, journal_time_2, journal_enabled FROM users WHERE id = $1",
            user["id"],
        )
        t1 = row["journal_time_1"].strftime("%H:%M") if row and row["journal_time_1"] else "10:00"
        t2 = row["journal_time_2"].strftime("%H:%M") if row and row["journal_time_2"] else "20:00"
        enabled = row["journal_enabled"] if row else True
        lang = _lang(update)
        status = t("enabled", lang) if enabled else t("disabled", lang)
        await update.message.reply_text(
            t("journal_status", lang, status=status, t1=t1, t2=t2)
        )
        return

    import re
    times = re.findall(r'\d{1,2}:\d{2}', text)
    if len(times) < 2:
        await update.message.reply_text(t("journal_need_two", _lang(update)))
        return

    from datetime import time as dt_time
    try:
        h1, m1 = map(int, times[0].split(":"))
        h2, m2 = map(int, times[1].split(":"))
        time_1 = dt_time(h1, m1)
        time_2 = dt_time(h2, m2)
    except (ValueError, IndexError):
        await update.message.reply_text(t("journal_bad_format", _lang(update)))
        return

    pool = await get_pool()
    await pool.execute(
        "UPDATE users SET journal_time_1 = $1, journal_time_2 = $2, journal_enabled = true WHERE id = $3",
        time_1, time_2, user["id"],
    )
    await update.message.reply_text(t("journal_set", _lang(update), t1=times[0], t2=times[1]))


async def handle_journal_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /journal_off — disable journal reminders."""
    if not update.message or not update.effective_user:
        return
    user = await _ensure_user(update.effective_user.id, update.effective_user.username, getattr(update.effective_user, "language_code", None))
    pool = await get_pool()
    await pool.execute("UPDATE users SET journal_enabled = false WHERE id = $1", user["id"])
    await update.message.reply_text(t("journal_off", _lang(update)))


async def handle_journal_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /journal_on — enable journal reminders."""
    if not update.message or not update.effective_user:
        return
    user = await _ensure_user(update.effective_user.id, update.effective_user.username, getattr(update.effective_user, "language_code", None))
    pool = await get_pool()
    await pool.execute("UPDATE users SET journal_enabled = true WHERE id = $1", user["id"])
    row = await pool.fetchrow(
        "SELECT journal_time_1, journal_time_2 FROM users WHERE id = $1", user["id"],
    )
    t1 = row["journal_time_1"].strftime("%H:%M") if row and row["journal_time_1"] else "10:00"
    t2 = row["journal_time_2"].strftime("%H:%M") if row and row["journal_time_2"] else "20:00"
    await update.message.reply_text(t("journal_on", _lang(update), t1=t1, t2=t2))


async def handle_journal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /journal command — show recent journal entries."""
    if not update.message or not update.effective_user:
        return

    user = await _ensure_user(update.effective_user.id, update.effective_user.username, getattr(update.effective_user, "language_code", None))
    entries = await get_journal_history(user["id"], days=7)

    if not entries:
        await update.message.reply_text(t("journal_empty", _lang(update)))
        return

    lines = []
    for e in entries:
        date_str = e["created_at"].strftime("%d.%m %H:%M")
        text = e["content"][:100]
        mood = f" 😊{e['mood_score']}" if e["mood_score"] else ""
        energy = f" ⚡{e['energy_level']}" if e["energy_level"] else ""
        tags = ""
        if e.get("tags"):
            tags = " " + " ".join(f"#{t}" for t in e["tags"])
        lines.append(f"  {date_str}{mood}{energy}{tags}\n    {text}")

    await update.message.reply_text(t("journal_title", _lang(update)) + "\n\n".join(lines))


async def handle_gym_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /gym_prompt command — set persistent gym coaching profile."""
    if not update.message or not update.effective_user:
        return

    telegram_user_id = update.effective_user.id
    user = await _ensure_user(
        telegram_user_id, update.effective_user.username,
        getattr(update.effective_user, "language_code", None),
    )
    lang = _lang(update)

    text = update.message.text or ""
    prompt_text = text.replace("/gym_prompt", "", 1).strip()

    pool = await get_pool()
    if not prompt_text:
        row = await pool.fetchrow("SELECT gym_prompt FROM users WHERE id = $1", user["id"])
        current = row["gym_prompt"] if row and row["gym_prompt"] else t("gym_prompt_unset", lang)
        await update.message.reply_text(t("gym_prompt_current", lang, current=current))
        return

    await pool.execute("UPDATE users SET gym_prompt = $1 WHERE id = $2", prompt_text, user["id"])
    await update.message.reply_text(t("gym_prompt_set", lang, text=prompt_text))


async def handle_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /timezone [IANA name] — show or set the user's timezone."""
    if not update.message or not update.effective_user:
        return

    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    user = await _ensure_user(update.effective_user.id, update.effective_user.username, getattr(update.effective_user, "language_code", None))
    pool = await get_pool()
    args = (update.message.text or "").split(maxsplit=1)
    if len(args) < 2:
        row = await pool.fetchrow("SELECT timezone FROM users WHERE id = $1", user["id"])
        current = resolve_timezone(row["timezone"] if row else None).key
        await update.message.reply_text(t("tz_current", _lang(update), tz=current))
        return

    name = args[1].strip()
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        await update.message.reply_text(t("tz_unknown", _lang(update)))
        return
    await pool.execute(
        "UPDATE users SET timezone = $1, updated_at = NOW() WHERE id = $2",
        name,
        user["id"],
    )
    await update.message.reply_text(t("tz_set", _lang(update), tz=name))


async def handle_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /language [uk|en] — show or set the reply language."""
    if not update.message or not update.effective_user:
        return
    user = await _ensure_user(
        update.effective_user.id, update.effective_user.username,
        getattr(update.effective_user, "language_code", None),
    )
    args = (update.message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await update.message.reply_text(t("lang_current", _lang(update)))
        return
    choice = args[1].strip().lower()
    if choice not in SUPPORTED_LANGUAGES:
        await update.message.reply_text(t("lang_unknown", _lang(update)))
        return
    pool = await get_pool()
    await pool.execute(
        "UPDATE users SET language = $1, updated_at = NOW() WHERE id = $2", choice, user["id"],
    )
    _language_cache[update.effective_user.id] = choice
    await update.message.reply_text(t("lang_set", choice))


async def handle_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /profile [birth_year m|f height_cm] — BMR inputs."""
    if not update.message or not update.effective_user:
        return
    from app.services.apple_health import get_latest_body_mass
    from app.services.bmr import compute_bmr, parse_profile_args

    user = await _ensure_user(
        update.effective_user.id, update.effective_user.username,
        getattr(update.effective_user, "language_code", None),
    )
    lang = _lang(update)
    pool = await get_pool()
    args = (update.message.text or "").split(maxsplit=1)

    if len(args) == 2:
        profile = parse_profile_args(args[1])
        if profile is None:
            await update.message.reply_text(t("profile_bad", lang))
            return
        await pool.execute(
            """UPDATE users SET birth_year = $1, sex = $2, height_cm = $3, updated_at = NOW()
               WHERE id = $4""",
            profile["birth_year"], profile["sex"], profile["height_cm"], user["id"],
        )

    row = await pool.fetchrow(
        "SELECT birth_year, sex, height_cm FROM users WHERE id = $1", user["id"],
    )
    latest = await get_latest_body_mass(pool, user["id"])
    weight = latest["kg"] if latest else None
    bmr = compute_bmr(
        birth_year=row["birth_year"] if row else None,
        sex=row["sex"] if row else None,
        height_cm=float(row["height_cm"]) if row and row["height_cm"] else None,
        weight_kg=weight,
    )
    bmr_text = t("bmr_value", lang, kcal=bmr) if bmr else t("bmr_need_weight", lang)
    if len(args) == 2:
        await update.message.reply_text(t("profile_set", lang, bmr=bmr_text))
        return
    not_set = t("not_set", lang)
    await update.message.reply_text(
        t(
            "profile_current",
            lang,
            birth_year=(row["birth_year"] if row and row["birth_year"] else not_set),
            sex=(t(row["sex"], lang) if row and row["sex"] else not_set),
            height=(f"{row['height_cm']} cm" if row and row["height_cm"] else not_set),
            weight=(f"{weight} kg" if weight else not_set),
            bmr=bmr_text,
        )
    )


async def handle_sync(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /sync command — verify WHOOP and FatSecret connections by fetching live data."""
    if not update.message or not update.effective_user:
        return

    telegram_user_id = update.effective_user.id
    user = await _ensure_user(
        telegram_user_id, update.effective_user.username,
        getattr(update.effective_user, "language_code", None),
    )
    user_id = user["id"]
    lang = _lang(update)

    await update.message.reply_text(t("sync_checking", lang))

    stats = await get_today_stats(user_id)
    results = []

    # WHOOP status
    pool = await get_pool()
    whoop_row = await pool.fetchrow(
        "SELECT whoop_access_token FROM users WHERE id = $1 AND whoop_access_token IS NOT NULL",
        user_id,
    )
    if whoop_row:
        if "whoop" in stats.get("expired_services", []):
            results.append(t("sync_whoop_expired", lang))
        elif stats["today_calories_out"] > 0 or stats["whoop_sleep"] or stats["whoop_recovery"]:
            parts = []
            if stats.get("calories_burned_source") == "whoop" and stats["today_calories_out"] > 0:
                parts.append(t("sync_whoop_burned", lang, kcal=stats["today_calories_out"]))
            if stats["whoop_recovery"]:
                parts.append("recovery ✓")
            if stats["whoop_sleep"]:
                parts.append("sleep ✓")
            if parts:
                results.append(t("sync_whoop_ok", lang, parts=", ".join(parts)))
            else:
                results.append(t("sync_whoop_pending", lang))
        else:
            results.append(t("sync_whoop_pending", lang))
    else:
        results.append(t("sync_whoop_off", lang))

    apple_health_row = await pool.fetchrow(
        """SELECT last_sync_at
           FROM apple_health_sync
           WHERE user_id = $1 AND is_active = TRUE""",
        user_id,
    )
    if apple_health_row:
        metric_counts = stats.get("apple_health_metric_counts") or {}
        latest_metric_at = stats.get("apple_health_latest_metric_at")
        if metric_counts:
            count_text = ", ".join(
                f"{metric}: {count}" for metric, count in sorted(metric_counts.items())
            )
            line = t("sync_apple_counts", lang, counts=count_text)
            if latest_metric_at:
                line += t("sync_apple_latest", lang, latest=f"{latest_metric_at:%d.%m %H:%M}")
            if stats.get("apple_health_workout_count"):
                line += t("sync_apple_workouts", lang, count=stats["apple_health_workout_count"])
            results.append(line)
        elif apple_health_row["last_sync_at"]:
            last_sync = apple_health_row["last_sync_at"]
            results.append(t("sync_apple_last_sync", lang, last=f"{last_sync:%d.%m %H:%M}"))
        else:
            results.append(t("sync_apple_waiting", lang))
    else:
        results.append(t("sync_apple_off", lang))

    # FatSecret status
    fs_row = await pool.fetchrow(
        "SELECT fatsecret_access_token FROM users WHERE id = $1 AND fatsecret_access_token IS NOT NULL",
        user_id,
    )
    if fs_row:
        if "fatsecret" in stats.get("expired_services", []):
            results.append(t("sync_fs_expired", lang))
        else:
            results.append(t("sync_fs_ok", lang, kcal=stats["today_calories_in"]))
    else:
        results.append(t("sync_fs_off", lang))

    await update.message.reply_text(t("sync_done", lang) + "\n".join(results))


BOT_COMMANDS: dict[str | None, list[tuple[str, str]]] = {
    "uk": [
        ("start", "Почати / Інструкція"),
        ("help", "Допомога"),
        ("connect_whoop", "Підключити WHOOP"),
        ("connect_apple_health", "Підключити Apple Health"),
        ("apple_health_help", "Інструкція Apple Health"),
        ("connect_fatsecret", "Підключити FatSecret"),
        ("sync", "Синхронізувати дані"),
        ("timezone", "Часовий пояс"),
        ("profile", "Профіль для BMR"),
        ("language", "Мова / Language"),
        ("gym_prompt", "Налаштувати gym профіль"),
        ("journal", "Записи щоденника"),
        ("journal_time", "Час нагадувань щоденника"),
        ("journal_off", "Вимкнути нагадування"),
        ("journal_on", "Увімкнути нагадування"),
        ("app", "Застосунок"),
    ],
    # None = default for every other Telegram language.
    None: [
        ("start", "Start / guide"),
        ("help", "Help"),
        ("connect_whoop", "Connect WHOOP"),
        ("connect_apple_health", "Connect Apple Health"),
        ("apple_health_help", "Apple Health guide"),
        ("connect_fatsecret", "Connect FatSecret"),
        ("sync", "Check connections"),
        ("timezone", "Timezone"),
        ("profile", "BMR profile"),
        ("language", "Language / Мова"),
        ("gym_prompt", "Gym profile"),
        ("journal", "Journal entries"),
        ("journal_time", "Journal reminder times"),
        ("journal_off", "Disable reminders"),
        ("journal_on", "Enable reminders"),
        ("app", "Web App"),
    ],
}


async def start_bot() -> None:
    """Initialize and start the Telegram bot with long polling."""
    global _application

    if not settings.telegram_bot_token:
        logger.warning("TELEGRAM_BOT_TOKEN not set, skipping bot startup")
        return

    _application = Application.builder().token(settings.telegram_bot_token).build()

    _application.add_handler(CommandHandler("start", handle_help))
    _application.add_handler(CommandHandler("help", handle_help))
    _application.add_handler(CommandHandler("connect_whoop", handle_connect_whoop))
    _application.add_handler(CommandHandler("connect_apple_health", handle_connect_apple_health))
    _application.add_handler(CommandHandler("apple_health_help", handle_apple_health_help))
    _application.add_handler(CommandHandler("connect_fatsecret", handle_connect_fatsecret))
    _application.add_handler(CommandHandler("sync", handle_sync))
    _application.add_handler(CommandHandler("timezone", handle_timezone))
    _application.add_handler(CommandHandler("language", handle_language))
    _application.add_handler(CommandHandler("profile", handle_profile))
    _application.add_handler(CommandHandler("gym_prompt", handle_gym_prompt))
    _application.add_handler(CommandHandler("journal", handle_journal))
    _application.add_handler(CommandHandler("journal_time", handle_journal_time))
    _application.add_handler(CommandHandler("journal_off", handle_journal_off))
    _application.add_handler(CommandHandler("journal_on", handle_journal_on))
    _application.add_handler(CommandHandler("app", handle_app))
    _application.add_handler(CallbackQueryHandler(handle_food_callback, pattern=r"^f[de]:"))
    _application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    _application.add_handler(MessageHandler(filters.Document.IMAGE, handle_photo))
    _application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    _application.add_handler(
        MessageHandler(filters.VOICE, handle_message)
    )

    await _application.initialize()

    for language_code, commands in BOT_COMMANDS.items():
        await _application.bot.set_my_commands(
            [BotCommand(name, description) for name, description in commands],
            language_code=language_code,
        )

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
