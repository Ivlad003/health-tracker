from __future__ import annotations

import httpx
import logging
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

from app.config import settings
from app.database import get_pool

logger = logging.getLogger(__name__)

router = APIRouter()

WHOOP_TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
WHOOP_RECOVERY_URL = "https://api.prod.whoop.com/developer/v2/recovery"

SUCCESS_HTML = """<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>WHOOP Connected</title><style>*{margin:0;padding:0;box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0a0a0a;color:#e4e4e7;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px}.card{background:#1a1a1a;border:1px solid rgba(255,255,255,0.05);border-radius:16px;padding:48px;text-align:center;max-width:400px}.icon{width:64px;height:64px;background:rgba(16,185,129,0.1);border-radius:16px;display:flex;align-items:center;justify-content:center;margin:0 auto 24px}h1{font-size:24px;font-weight:700;margin-bottom:8px}p{color:#a1a1aa;line-height:1.6;margin-bottom:24px}</style></head><body><div class="card"><div class="icon"><svg width="32" height="32" fill="none" viewBox="0 0 24 24" stroke="#10B981" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg></div><h1>WHOOP Connected!</h1><p>Your WHOOP account has been successfully linked to Health Tracker. You can close this window.</p></div></body></html>"""

ERROR_HTML = """<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Authorization Failed</title><style>*{margin:0;padding:0;box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0a0a0a;color:#e4e4e7;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px}.card{background:#1a1a1a;border:1px solid rgba(255,255,255,0.05);border-radius:16px;padding:48px;text-align:center;max-width:400px}.icon{width:64px;height:64px;background:rgba(244,63,94,0.1);border-radius:16px;display:flex;align-items:center;justify-content:center;margin:0 auto 24px}h1{font-size:24px;font-weight:700;margin-bottom:8px}p{color:#a1a1aa;line-height:1.6}</style></head><body><div class="card"><div class="icon"><svg width="32" height="32" fill="none" viewBox="0 0 24 24" stroke="#F43F5E" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg></div><h1>Authorization Failed</h1><p>Something went wrong during WHOOP authorization. Please try again.</p></div></body></html>"""


@router.get("/whoop/callback")
async def whoop_callback(
    code: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
):
    logger.info("WHOOP OAuth callback: code=%s state=%s", bool(code), state)
    if not code or not state:
        return HTMLResponse(content=ERROR_HTML, status_code=400)

    try:
        telegram_user_id = int(state)
    except (ValueError, TypeError):
        return HTMLResponse(content=ERROR_HTML, status_code=400)

    try:
        async with httpx.AsyncClient() as client:
            # Exchange authorization code for tokens
            token_resp = await client.post(
                WHOOP_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": settings.whoop_client_id,
                    "client_secret": settings.whoop_client_secret,
                    "redirect_uri": settings.whoop_redirect_uri,
                },
            )
            token_resp.raise_for_status()
            tokens = token_resp.json()
            logger.info("WHOOP token response keys: %s", list(tokens.keys()))

            # Fetch recovery to get whoop_user_id (profile endpoint unavailable)
            recovery_resp = await client.get(
                WHOOP_RECOVERY_URL,
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
                params={"limit": "1"},
            )
            recovery_resp.raise_for_status()
            whoop_user_id = recovery_resp.json()["records"][0]["user_id"]

        # Store tokens in DB using parameterized queries
        access_token = tokens.get("access_token", "")
        refresh_token = tokens.get("refresh_token", "")
        expires_in = tokens.get("expires_in", 3600)

        pool = await get_pool()
        await pool.execute(
            """UPDATE users
               SET whoop_access_token = $1,
                   whoop_refresh_token = $2,
                   whoop_token_expires_at = NOW() + make_interval(secs => $3),
                   whoop_user_id = $4,
                   updated_at = NOW()
               WHERE telegram_user_id = $5""",
            access_token,
            refresh_token,
            expires_in,
            str(whoop_user_id),
            telegram_user_id,
        )

        logger.info("WHOOP connected for telegram_user_id=%s", state)

        # Trigger initial data sync (7-day lookback to get recent sleep/recovery)
        sync_ok = False
        try:
            from app.services.whoop_sync import sync_whoop_for_telegram_user
            await sync_whoop_for_telegram_user(telegram_user_id, lookback_hours=168)
            logger.info("Initial WHOOP sync completed for telegram_user_id=%s", state)
            sync_ok = True
        except Exception:
            logger.exception("Initial WHOOP sync failed for telegram_user_id=%s", state)

        # Notify user in Telegram
        try:
            from app.services.telegram_bot import send_message
            if sync_ok:
                await send_message(
                    telegram_user_id,
                    "⌚ WHOOP підключено!\n"
                    "\n"
                    "✅ Дані за останній тиждень синхронізовано.\n"
                    "Тепер можеш питати про сон, відновлення та тренування 💪",
                )
            else:
                await send_message(
                    telegram_user_id,
                    "⌚ WHOOP підключено!\n"
                    "\n"
                    "🔄 Дані синхронізуються протягом години.",
                )
        except Exception:
            logger.exception("Failed to send WHOOP notification to %s", state)

        return HTMLResponse(content=SUCCESS_HTML)

    except Exception:
        logger.exception("WHOOP OAuth callback failed")
        return HTMLResponse(content=ERROR_HTML, status_code=500)
