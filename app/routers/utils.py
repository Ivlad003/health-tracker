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


@router.get("/debug/whoop-token", summary="Check WHOOP token state without clearing")
async def debug_whoop_token(
    telegram_user_id: int = Query(..., description="Telegram user ID"),
):
    """Check WHOOP token validity — does NOT clear tokens on failure."""
    from app.database import get_pool

    pool = await get_pool()
    user = await pool.fetchrow(
        """SELECT id, whoop_access_token, whoop_refresh_token, whoop_token_expires_at
           FROM users WHERE telegram_user_id = $1""",
        telegram_user_id,
    )
    if not user:
        return {"error": "User not found"}

    has_access = bool(user["whoop_access_token"])
    has_refresh = bool(user["whoop_refresh_token"])
    expires = str(user["whoop_token_expires_at"]) if user["whoop_token_expires_at"] else None

    result = {
        "user_id": user["id"],
        "has_access_token": has_access,
        "has_refresh_token": has_refresh,
        "token_expires_at": expires,
        "access_token_prefix": user["whoop_access_token"][:20] + "..." if has_access else None,
    }

    if not has_access:
        result["status"] = "NO_TOKEN"
        return result

    # Try all WHOOP API endpoints without clearing tokens
    endpoints = {
        "cycle": "cycle?limit=1",
        "body": "body_measurement?limit=1",
        "workout": "activity/workout?limit=1",
        "recovery": "recovery?limit=1",
        "sleep": "activity/sleep?limit=1",
    }
    api_base = "https://api.prod.whoop.com/developer/v2"
    headers = {"Authorization": f"Bearer {user['whoop_access_token']}"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            api_results = {}
            for name, path in endpoints.items():
                resp = await client.get(f"{api_base}/{path}", headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    records = data.get("records", [])
                    api_results[name] = {
                        "status": 200,
                        "records_count": len(records),
                    }
                else:
                    api_results[name] = {
                        "status": resp.status_code,
                        "body": resp.text[:200],
                    }
            result["endpoints"] = api_results
            all_ok = all(r["status"] == 200 for r in api_results.values())
            result["status"] = "OK" if all_ok else "PARTIAL_ERROR"
    except Exception as e:
        result["status"] = "NETWORK_ERROR"
        result["error"] = str(e)

    return result


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
                        return {"error": "WHOOP token expired, reconnect via /connect_whoop"}
                    token = await refresh_token_if_needed(
                        dict(fresh_user), client, pool, force=True,
                    )
                    try:
                        whoop = await fetch_whoop_context(token)
                    except httpx.HTTPStatusError as e2:
                        if e2.response.status_code == 401:
                            await pool.execute(
                                """UPDATE users
                                   SET whoop_access_token = NULL,
                                       whoop_refresh_token = NULL,
                                       whoop_token_expires_at = NULL,
                                       updated_at = NOW()
                                   WHERE id = $1""",
                                user["id"],
                            )
                            return {"error": "WHOOP token expired after refresh, reconnect via /connect_whoop"}
                        return {"error": f"WHOOP API error: {e2.response.status_code}"}
                else:
                    return {"error": f"WHOOP API error: {e.response.status_code}"}
        return {"user_id": user["id"], **whoop}
    except TokenExpiredError:
        return {"error": "WHOOP token expired, reconnect via /connect_whoop"}
    except Exception as e:
        return {"error": str(e)}
