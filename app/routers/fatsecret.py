import asyncio
from typing import Optional
from urllib.parse import quote
import logging

from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from app.database import get_pool
from app.security import InvalidStateError, require_admin, verify_oauth_state
from app.services.fatsecret_api import search_food, fetch_food_diary
from app.services.fatsecret_auth import (
    get_request_token,
    exchange_access_token,
    FATSECRET_AUTHORIZE_URL,
)
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()
# Strong references to fire-and-forget tasks (asyncio keeps only weak ones).
_background_tasks: set = set()

FATSECRET_SUCCESS_HTML = """<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FatSecret Connected</title><style>*{margin:0;padding:0;box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0a0a0a;color:#e4e4e7;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px}.card{background:#1a1a1a;border:1px solid rgba(255,255,255,0.05);border-radius:16px;padding:48px;text-align:center;max-width:400px}.icon{width:64px;height:64px;background:rgba(16,185,129,0.1);border-radius:16px;display:flex;align-items:center;justify-content:center;margin:0 auto 24px}h1{font-size:24px;font-weight:700;margin-bottom:8px}p{color:#a1a1aa;line-height:1.6}</style></head><body><div class="card"><div class="icon"><svg width="32" height="32" fill="none" viewBox="0 0 24 24" stroke="#10B981" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg></div><h1>FatSecret Connected!</h1><p>Your FatSecret account has been linked. Your food diary will now sync automatically.</p></div></body></html>"""


def _telegram_id_from_state(state: str) -> int:
    try:
        return verify_oauth_state(state, "fatsecret")
    except InvalidStateError as exc:
        logger.warning("FatSecret OAuth state rejected: %s", exc)
        raise HTTPException(
            status_code=400,
            detail="Link expired or invalid. Run /connect_fatsecret in Telegram again.",
        ) from exc


@router.get("/food/search", dependencies=[Depends(require_admin)])
async def food_search(q: str = Query(..., min_length=1, max_length=200)):
    logger.info("Food search request: q='%s'", q)
    try:
        return await search_food(q)
    except Exception:
        logger.exception("FatSecret food search failed")
        raise HTTPException(status_code=502, detail="FatSecret API is currently unavailable")


@router.get("/fatsecret/connect")
async def fatsecret_connect(state: str = Query(..., max_length=128)):
    """OAuth 1.0 Step 1: Get request token, store secret, redirect user to FatSecret."""
    telegram_user_id = _telegram_id_from_state(state)
    logger.info("FatSecret OAuth connect: telegram_user_id=%s", telegram_user_id)
    callback_url = f"{settings.app_base_url}/fatsecret/callback?state={quote(state, safe='')}"
    try:
        tokens = await get_request_token(callback_url)
    except Exception:
        logger.exception("FatSecret request token failed")
        raise HTTPException(status_code=502, detail="FatSecret authorization is currently unavailable")

    # Store request token secret in user settings for step 3
    pool = await get_pool()
    await pool.execute(
        """UPDATE users
           SET settings = jsonb_set(COALESCE(settings, '{}'),
                                    '{fatsecret_request_token_secret}',
                                    to_jsonb($1::text)),
               updated_at = NOW()
           WHERE telegram_user_id = $2""",
        tokens["oauth_token_secret"],
        telegram_user_id,
    )

    authorize_url = f"{FATSECRET_AUTHORIZE_URL}?oauth_token={tokens['oauth_token']}"
    return RedirectResponse(url=authorize_url)


@router.get("/fatsecret/callback")
async def fatsecret_callback(
    oauth_token: str = Query(...),
    oauth_verifier: str = Query(...),
    state: str = Query(..., max_length=128),
):
    """OAuth 1.0 Step 3: Exchange request token for access token, store in DB."""
    telegram_user_id = _telegram_id_from_state(state)
    pool = await get_pool()

    # Retrieve stored request token secret
    row = await pool.fetchrow(
        """SELECT id, settings->>'fatsecret_request_token_secret' as request_secret
           FROM users WHERE telegram_user_id = $1""",
        telegram_user_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    request_secret = row["request_secret"] or ""
    if not request_secret:
        raise HTTPException(
            status_code=400,
            detail="Authorization session not found. Run /connect_fatsecret again.",
        )
    logger.info("FatSecret callback for telegram_user_id=%s", telegram_user_id)

    try:
        tokens = await exchange_access_token(
            oauth_token=oauth_token,
            oauth_verifier=oauth_verifier,
            token_secret=request_secret,
        )
    except Exception:
        logger.exception("FatSecret access token exchange failed")
        raise HTTPException(status_code=502, detail="FatSecret authorization failed, try again")

    logger.info("FatSecret storing tokens for user_id=%s", row["id"])

    # Store access token and clear temp secret
    await pool.execute(
        """UPDATE users
           SET fatsecret_access_token = $1,
               fatsecret_access_secret = $2,
               settings = settings - 'fatsecret_request_token_secret',
               updated_at = NOW()
           WHERE id = $3""",
        tokens["access_token"],
        tokens["access_secret"],
        row["id"],
    )

    # FatSecret diary history → My Products (required P1 flow): start the
    # resumable background import; the scheduler finishes larger ranges.
    import_days = None
    try:
        from app.services import feature_flags
        from app.services.catalog_import import run_job, start_import
        from app.services.preferences import get_preferences

        if await feature_flags.is_enabled(pool, "food_history"):
            prefs, prefs_version = await get_preferences(pool, row["id"])
            days = prefs.history_import_days if prefs_version else settings.fatsecret_history_import_days
            job = await start_import(pool, row["id"], days=days, mode=prefs.history_import_mode)
            import_days = days
            if not job.get("already_active"):
                task = asyncio.create_task(run_job(pool, job["id"]))
                _background_tasks.add(task)
                task.add_done_callback(_background_tasks.discard)
    except Exception:
        logger.exception("Failed to start FatSecret history import for user_id=%s", row["id"])

    # Notify user in Telegram
    try:
        from app.i18n import t
        from app.services.telegram_bot import send_message, user_language

        lang = await user_language(telegram_user_id)
        text = t("fatsecret_connected", lang)
        if import_days:
            text += "\n\n" + t("food_import_started", lang, days=import_days)
        await send_message(telegram_user_id, text)
    except Exception:
        logger.exception("Failed to send FatSecret notification to %s", telegram_user_id)

    return HTMLResponse(content=FATSECRET_SUCCESS_HTML)


@router.get("/fatsecret/diary", dependencies=[Depends(require_admin)])
async def fatsecret_diary(
    user_id: int = Query(..., description="Telegram user ID"),
    date: Optional[int] = Query(default=None, description="Days since epoch"),
):
    """Fetch user's FatSecret food diary. Requires OAuth 1.0 connection."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """SELECT fatsecret_access_token, fatsecret_access_secret
           FROM users WHERE telegram_user_id = $1""",
        user_id,
    )
    if not row or not row["fatsecret_access_token"]:
        raise HTTPException(
            status_code=400,
            detail="FatSecret not connected. Use /fatsecret/connect first.",
        )

    try:
        return await fetch_food_diary(
            access_token=row["fatsecret_access_token"],
            access_secret=row["fatsecret_access_secret"],
            date=date,
        )
    except Exception:
        logger.exception("FatSecret diary fetch failed")
        raise HTTPException(status_code=502, detail="FatSecret API is currently unavailable")
