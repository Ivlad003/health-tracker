from __future__ import annotations

import httpx
import logging
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

from app.config import settings
from app.database import get_pool
from app.security import InvalidStateError, verify_oauth_state
from app.services.whoop_sync import WHOOP_API_BASE, WHOOP_TOKEN_URL

logger = logging.getLogger(__name__)

router = APIRouter()

WHOOP_RECOVERY_URL = f"{WHOOP_API_BASE}/recovery"
WHOOP_SLEEP_URL = f"{WHOOP_API_BASE}/activity/sleep"
WHOOP_WORKOUT_URL = f"{WHOOP_API_BASE}/activity/workout"

SUCCESS_HTML = """<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>WHOOP Connected</title><style>*{margin:0;padding:0;box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0a0a0a;color:#e4e4e7;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px}.card{background:#1a1a1a;border:1px solid rgba(255,255,255,0.05);border-radius:16px;padding:48px;text-align:center;max-width:400px}.icon{width:64px;height:64px;background:rgba(16,185,129,0.1);border-radius:16px;display:flex;align-items:center;justify-content:center;margin:0 auto 24px}h1{font-size:24px;font-weight:700;margin-bottom:8px}p{color:#a1a1aa;line-height:1.6;margin-bottom:24px}</style></head><body><div class="card"><div class="icon"><svg width="32" height="32" fill="none" viewBox="0 0 24 24" stroke="#10B981" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg></div><h1>WHOOP Connected!</h1><p>Your WHOOP account has been successfully linked to Health Tracker. You can close this window.</p></div></body></html>"""

ERROR_HTML = """<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Authorization Failed</title><style>*{margin:0;padding:0;box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0a0a0a;color:#e4e4e7;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px}.card{background:#1a1a1a;border:1px solid rgba(255,255,255,0.05);border-radius:16px;padding:48px;text-align:center;max-width:400px}.icon{width:64px;height:64px;background:rgba(244,63,94,0.1);border-radius:16px;display:flex;align-items:center;justify-content:center;margin:0 auto 24px}h1{font-size:24px;font-weight:700;margin-bottom:8px}p{color:#a1a1aa;line-height:1.6}</style></head><body><div class="card"><div class="icon"><svg width="32" height="32" fill="none" viewBox="0 0 24 24" stroke="#F43F5E" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg></div><h1>Authorization Failed</h1><p>Something went wrong during WHOOP authorization. Please try again.</p></div></body></html>"""


async def _discover_whoop_user_id(client: httpx.AsyncClient, access_token: str) -> Optional[str]:
    """Find the WHOOP user id without `read:profile`.

    A brand-new WHOOP member may have no scored recovery yet, so fall back to
    sleep and workouts. Returns None instead of failing the whole OAuth flow —
    whoop_user_id is informational only.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    for url in (WHOOP_RECOVERY_URL, WHOOP_SLEEP_URL, WHOOP_WORKOUT_URL):
        resp = await client.get(url, headers=headers, params={"limit": "1"})
        resp.raise_for_status()
        records = (resp.json() or {}).get("records") or []
        if records and records[0].get("user_id") is not None:
            return str(records[0]["user_id"])
    logger.info("WHOOP user id not discoverable yet (no records)")
    return None


@router.get("/whoop/callback")
async def whoop_callback(
    code: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
):
    logger.info("WHOOP OAuth callback: code=%s state_present=%s", bool(code), bool(state))
    if not code or not state:
        return HTMLResponse(content=ERROR_HTML, status_code=400)

    try:
        telegram_user_id = verify_oauth_state(state, "whoop")
    except InvalidStateError as exc:
        logger.warning("WHOOP OAuth callback rejected: %s", exc)
        return HTMLResponse(content=ERROR_HTML, status_code=400)

    try:
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
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
            logger.info(
                "WHOOP token response: keys=%s expires_in=%s has_refresh=%s",
                list(tokens.keys()), tokens.get("expires_in"),
                bool(tokens.get("refresh_token")),
            )

            whoop_user_id = await _discover_whoop_user_id(client, tokens["access_token"])

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
            str(whoop_user_id) if whoop_user_id is not None else None,
            telegram_user_id,
        )

        logger.info("WHOOP connected for telegram_user_id=%s", telegram_user_id)

        # Notify user in Telegram
        try:
            from app.i18n import t
            from app.services.telegram_bot import send_message, user_language

            lang = await user_language(telegram_user_id)
            await send_message(telegram_user_id, t("whoop_connected", lang))
        except Exception:
            logger.exception("Failed to send WHOOP notification to %s", telegram_user_id)

        return HTMLResponse(content=SUCCESS_HTML)

    except Exception:
        logger.exception("WHOOP OAuth callback failed")
        return HTMLResponse(content=ERROR_HTML, status_code=500)
