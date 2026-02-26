import httpx
from fastapi import APIRouter, Query

router = APIRouter()


@router.get("/ip-check")
async def ip_check():
    async with httpx.AsyncClient() as client:
        resp = await client.get("https://api.ipify.org?format=json")
        resp.raise_for_status()
        return resp.json()


@router.get("/debug/stats", summary="Get today's stats for a user (live API)")
async def debug_stats(
    telegram_user_id: int = Query(..., description="Telegram user ID"),
):
    """Fetch live WHOOP + FatSecret data for debugging. Same as what GPT receives."""
    from app.database import get_pool
    from app.services.ai_assistant import get_today_stats

    pool = await get_pool()
    user = await pool.fetchrow(
        "SELECT id, daily_calorie_goal FROM users WHERE telegram_user_id = $1",
        telegram_user_id,
    )
    if not user:
        return {"error": f"User with telegram_user_id={telegram_user_id} not found"}

    stats = await get_today_stats(user["id"])
    return {
        "user_id": user["id"],
        "telegram_user_id": telegram_user_id,
        "daily_calorie_goal": user["daily_calorie_goal"],
        **stats,
    }


@router.get("/debug/whoop-raw", summary="Raw WHOOP API response for a user")
async def debug_whoop_raw(
    telegram_user_id: int = Query(..., description="Telegram user ID"),
):
    """Fetch raw WHOOP context data directly from API."""
    from app.database import get_pool
    from app.services.whoop_sync import (
        fetch_whoop_context, refresh_token_if_needed, TokenExpiredError,
    )

    pool = await get_pool()
    user = await pool.fetchrow(
        """SELECT id, whoop_access_token, whoop_refresh_token, whoop_token_expires_at
           FROM users WHERE telegram_user_id = $1 AND whoop_access_token IS NOT NULL""",
        telegram_user_id,
    )
    if not user:
        return {"error": "WHOOP not connected for this user"}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            token = await refresh_token_if_needed(dict(user), client, pool)
            try:
                whoop = await fetch_whoop_context(token)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401:
                    fresh_user = await pool.fetchrow(
                        """SELECT id, whoop_access_token, whoop_refresh_token,
                                  whoop_token_expires_at
                           FROM users WHERE id = $1
                                 AND whoop_access_token IS NOT NULL""",
                        user["id"],
                    )
                    if not fresh_user:
                        return {"error": "WHOOP token expired, reconnect required"}
                    token = await refresh_token_if_needed(
                        dict(fresh_user), client, pool, force=True,
                    )
                    whoop = await fetch_whoop_context(token)
                else:
                    return {"error": f"WHOOP API error: {e.response.status_code}"}
        return {"user_id": user["id"], **whoop}
    except TokenExpiredError:
        return {"error": "WHOOP token expired, reconnect via /connect_whoop"}
    except Exception as e:
        return {"error": str(e)}
