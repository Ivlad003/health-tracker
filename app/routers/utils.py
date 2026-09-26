"""Operator-only diagnostics. Every route requires ADMIN_API_TOKEN."""
import httpx
from fastapi import APIRouter, Depends, Query

from app.config import settings
from app.security import require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


def _mask(token: str | None) -> str | None:
    if not token:
        return None
    return f"…{token[-4:]}" if len(token) > 8 else "…"


@router.get("/ip-check")
async def ip_check() -> dict:
    """Egress IP of the server (needed for FatSecret IP whitelisting)."""
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        resp = await client.get("https://api.ipify.org?format=json")
        resp.raise_for_status()
        return resp.json()


@router.get("/debug/stats", summary="Get today's stats for a user (live API)")
async def debug_stats(
    telegram_user_id: int = Query(..., description="Telegram user ID"),
) -> dict:
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
) -> dict:
    """Check WHOOP token validity — does NOT clear tokens on failure."""
    from app.database import get_pool
    from app.services.whoop_sync import WHOOP_API_BASE

    pool = await get_pool()
    user = await pool.fetchrow(
        """SELECT id, whoop_access_token, whoop_refresh_token, whoop_token_expires_at
           FROM users WHERE telegram_user_id = $1""",
        telegram_user_id,
    )
    if not user:
        return {"error": "User not found"}

    has_access = bool(user["whoop_access_token"])
    expires = user["whoop_token_expires_at"]
    result: dict = {
        "user_id": user["id"],
        "has_access_token": has_access,
        "has_refresh_token": bool(user["whoop_refresh_token"]),
        "token_expires_at": str(expires) if expires else None,
        # Never expose a usable token prefix; the last 4 chars are enough to
        # correlate with provider logs.
        "access_token_hint": _mask(user["whoop_access_token"]),
    }

    if not has_access:
        result["status"] = "NO_TOKEN"
        return result

    endpoints = {
        "cycle": "cycle?limit=1",
        "body": "body_measurement?limit=1",
        "workout": "activity/workout?limit=1",
        "recovery": "recovery?limit=1",
        "sleep": "activity/sleep?limit=1",
    }
    headers = {"Authorization": f"Bearer {user['whoop_access_token']}"}

    try:
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
            api_results = {}
            for name, path in endpoints.items():
                resp = await client.get(f"{WHOOP_API_BASE}/{path}", headers=headers)
                if resp.status_code == 200:
                    records = resp.json().get("records", [])
                    api_results[name] = {"status": 200, "records_count": len(records)}
                else:
                    api_results[name] = {"status": resp.status_code, "body": resp.text[:200]}
            result["endpoints"] = api_results
            all_ok = all(r["status"] == 200 for r in api_results.values())
            result["status"] = "OK" if all_ok else "PARTIAL_ERROR"
    except httpx.HTTPError as e:
        result["status"] = "NETWORK_ERROR"
        result["error"] = e.__class__.__name__

    return result


@router.get("/debug/whoop-raw", summary="Raw WHOOP API response for a user")
async def debug_whoop_raw(
    telegram_user_id: int = Query(..., description="Telegram user ID"),
) -> dict:
    """Fetch WHOOP context directly from the API (bypasses the short TTL cache)."""
    from app.database import get_pool
    from app.services.whoop_sync import TokenExpiredError, get_whoop_context_for_user

    pool = await get_pool()
    user = await pool.fetchrow(
        "SELECT id, timezone FROM users WHERE telegram_user_id = $1",
        telegram_user_id,
    )
    if not user:
        return {"error": "User not found"}

    from app.timeutils import resolve_timezone

    try:
        whoop = await get_whoop_context_for_user(
            pool, user["id"], tz=resolve_timezone(user["timezone"]), use_cache=False,
        )
    except TokenExpiredError:
        return {"error": "WHOOP token expired, reconnect via /connect_whoop"}
    except httpx.HTTPStatusError as e:
        return {"error": f"WHOOP API error: {e.response.status_code}"}
    except httpx.HTTPError as e:
        return {"error": f"WHOOP network error: {e.__class__.__name__}"}
    if whoop is None:
        return {"error": "WHOOP not connected for this user"}
    return {"user_id": user["id"], **whoop}
