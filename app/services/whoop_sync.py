from __future__ import annotations

import asyncio
import hashlib
import httpx
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from app.config import settings
from app.database import get_pool
from app.timeutils import resolve_timezone

logger = logging.getLogger(__name__)

WHOOP_TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
WHOOP_API_BASE = "https://api.prod.whoop.com/developer/v2"
WHOOP_AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
# Confirmed working scopes (see docs/en/session-knowledge.md). `read:cycles`
# returns invalid_scope; `offline` is required to receive a refresh token.
WHOOP_SCOPES = "offline read:workout read:recovery read:sleep read:body_measurement"

# Live WHOOP context is fetched on every message/briefing/reminder. A short
# TTL cache keeps us well below WHOOP rate limits without serving stale data.
WHOOP_CONTEXT_CACHE_TTL_SECONDS = 120
_context_cache: dict[tuple[str, str, str], tuple[float, dict]] = {}

# Per-user refresh locks: concurrent refreshes with the same refresh_token make
# WHOOP revoke the older one, which logs the user out.
_refresh_locks: dict[int, asyncio.Lock] = {}
# user_id -> (access_token, expires_at) of the most recent successful refresh.
_last_refreshed: dict[int, tuple[str, datetime]] = {}


class TokenExpiredError(Exception):
    """Raised when an OAuth token is expired and refresh failed — user must re-authorize."""

    def __init__(self, service: str):
        self.service = service
        super().__init__(f"{service} token expired, re-authorization required")


def _parse_dt(s: str) -> datetime:
    """Parse ISO 8601 datetime string from WHOOP API into datetime object."""
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _http_timeout() -> httpx.Timeout:
    return httpx.Timeout(settings.http_timeout_seconds)


async def clear_whoop_tokens(pool: Any, user_id: int) -> None:
    """Forget WHOOP credentials for a user (forces /connect_whoop)."""
    await pool.execute(
        """UPDATE users
           SET whoop_access_token = NULL,
               whoop_refresh_token = NULL,
               whoop_token_expires_at = NULL,
               updated_at = NOW()
           WHERE id = $1""",
        user_id,
    )
    _last_refreshed.pop(user_id, None)
    logger.warning("Cleared WHOOP tokens for user_id=%s — re-auth required", user_id)


async def refresh_token_if_needed(
    user: dict, client: httpx.AsyncClient, pool: Any, *, force: bool = False,
) -> str:
    """Check if token is expired, refresh if needed, return valid access_token.

    Args:
        force: If True, refresh even if token hasn't expired (e.g. after 401).
    """
    expires_at = user["whoop_token_expires_at"]
    if not force and expires_at and expires_at > datetime.now(timezone.utc):
        return user["whoop_access_token"]

    user_id = user["id"]
    lock = _refresh_locks.setdefault(user_id, asyncio.Lock())
    async with lock:
        # Another coroutine may have refreshed while we waited: reuse its token
        # instead of spending (and invalidating) the refresh token again.
        latest = _last_refreshed.get(user_id)
        if (
            latest
            and latest[0] != user["whoop_access_token"]
            and latest[1] > datetime.now(timezone.utc)
        ):
            return latest[0]
        return await _do_refresh(user, client, pool)


async def _do_refresh(user: dict, client: httpx.AsyncClient, pool: Any) -> str:
    logger.info("Refreshing WHOOP token for user_id=%s", user["id"])
    resp = None
    last_err: Optional[Exception] = None
    for attempt in range(3):
        try:
            resp = await client.post(
                WHOOP_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": user["whoop_refresh_token"],
                    "client_id": settings.whoop_client_id,
                    "client_secret": settings.whoop_client_secret,
                },
            )
            break
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            last_err = e
            if attempt < 2:
                logger.warning("WHOOP token refresh retry %d for user_id=%s: %s",
                               attempt + 1, user["id"], e)
                await asyncio.sleep(1 * (attempt + 1))
    if resp is None:
        logger.error("WHOOP token refresh failed after 3 retries for user_id=%s: %s",
                     user["id"], last_err)
        assert last_err is not None
        raise last_err
    if resp.status_code in (400, 401, 403):
        logger.error(
            "WHOOP token refresh rejected for user_id=%s: status=%s body=%s",
            user["id"], resp.status_code, resp.text[:200],
        )
        await clear_whoop_tokens(pool, user["id"])
        raise TokenExpiredError("whoop")
    if resp.status_code != 200:
        logger.error(
            "WHOOP token refresh failed for user_id=%s: status=%s body=%s",
            user["id"], resp.status_code, resp.text[:200],
        )
        resp.raise_for_status()
    tokens = resp.json()
    expires_in = int(tokens.get("expires_in") or 3600)
    logger.info("WHOOP token refreshed for user_id=%s, expires_in=%s", user["id"], expires_in)

    await pool.execute(
        """UPDATE users
           SET whoop_access_token = $1,
               whoop_refresh_token = $2,
               whoop_token_expires_at = NOW() + make_interval(secs => $3),
               updated_at = NOW()
           WHERE id = $4""",
        tokens["access_token"],
        # WHOOP may omit refresh_token on refresh; keep the old one then.
        tokens.get("refresh_token") or user["whoop_refresh_token"],
        expires_in,
        user["id"],
    )
    _last_refreshed[user["id"]] = (
        tokens["access_token"],
        datetime.now(timezone.utc) + timedelta(seconds=max(expires_in - 60, 0)),
    )
    return tokens["access_token"]


_WHOOP_USER_SQL = """SELECT id, whoop_access_token, whoop_refresh_token, whoop_token_expires_at
                     FROM users WHERE id = $1 AND whoop_access_token IS NOT NULL"""


async def get_whoop_context_for_user(
    pool: Any,
    user_id: int,
    *,
    tz: Optional[ZoneInfo] = None,
    whoop_user: Optional[dict] = None,
    use_cache: bool = True,
) -> Optional[dict]:
    """Return live WHOOP context for a user, handling refresh + 401 retry.

    Returns None when WHOOP is not connected. Raises TokenExpiredError when
    the user must reconnect (tokens are cleared in that case).
    """
    if whoop_user is None:
        row = await pool.fetchrow(_WHOOP_USER_SQL, user_id)
        if not row:
            return None
        whoop_user = dict(row)

    async with httpx.AsyncClient(timeout=_http_timeout()) as client:
        token = await refresh_token_if_needed(whoop_user, client, pool)
        try:
            return await fetch_whoop_context(token, tz=tz, use_cache=use_cache)
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 401:
                raise
        logger.warning("WHOOP API 401 for user_id=%s, re-reading tokens and forcing refresh", user_id)
        # Tokens may have been refreshed by the background job meanwhile.
        fresh_user = await pool.fetchrow(_WHOOP_USER_SQL, user_id)
        if not fresh_user:
            raise TokenExpiredError("whoop")
        token = await refresh_token_if_needed(dict(fresh_user), client, pool, force=True)
        try:
            return await fetch_whoop_context(token, tz=tz, use_cache=False)
        except httpx.HTTPStatusError as e2:
            if e2.response.status_code == 401:
                await clear_whoop_tokens(pool, user_id)
                raise TokenExpiredError("whoop") from e2
            raise


async def fetch_whoop_context(
    access_token: str, *, tz: Optional[ZoneInfo] = None, use_cache: bool = True,
) -> dict:
    """Fetch ALL WHOOP data directly from API for real-time GPT context.

    Fetches cycle, body measurement, workouts, recovery, and sleep in parallel.
    Uses timezone-aware "today" filtering so data matches user's current day.
    """
    user_tz = tz or resolve_timezone(None)
    today_local = datetime.now(user_tz).replace(hour=0, minute=0, second=0, microsecond=0)

    cache_key = (
        hashlib.sha256(access_token.encode()).hexdigest(),
        str(user_tz),
        today_local.date().isoformat(),
    )
    if use_cache:
        cached = _context_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < WHOOP_CONTEXT_CACHE_TTL_SECONDS:
            return dict(cached[1])

    result = await _fetch_whoop_context_uncached(access_token, today_local)
    # Drop expired entries so the cache cannot grow without bound.
    now_mono = time.monotonic()
    for key in [k for k, (ts, _) in _context_cache.items()
                if now_mono - ts >= WHOOP_CONTEXT_CACHE_TTL_SECONDS]:
        _context_cache.pop(key, None)
    _context_cache[cache_key] = (now_mono, dict(result))
    return result


async def _fetch_whoop_context_uncached(access_token: str, today_local: datetime) -> dict:
    today_utc = today_local.astimezone(timezone.utc).isoformat()
    # 48h window: cycle needs it for estimation, recovery/sleep need it because
    # today's recovery is linked to yesterday's cycle (which started yesterday).
    start_48h = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()

    headers = {"Authorization": f"Bearer {access_token}"}

    logger.info("WHOOP API: fetching 5 endpoints (cycle, body, workout, recovery, sleep)")
    async with httpx.AsyncClient(timeout=_http_timeout()) as client:
        cycle_resp, body_resp, workout_resp, recovery_resp, sleep_resp = (
            await asyncio.gather(
                client.get(f"{WHOOP_API_BASE}/cycle", headers=headers,
                           params={"limit": "5", "start": start_48h}),
                client.get(f"{WHOOP_API_BASE}/body_measurement", headers=headers,
                           params={"limit": "1"}),
                client.get(f"{WHOOP_API_BASE}/activity/workout", headers=headers,
                           params={"limit": "10", "start": today_utc}),
                client.get(f"{WHOOP_API_BASE}/recovery", headers=headers,
                           params={"limit": "5", "start": start_48h}),
                client.get(f"{WHOOP_API_BASE}/activity/sleep", headers=headers,
                           params={"limit": "5", "start": start_48h}),
            )
        )

    logger.info("WHOOP API responses: cycle=%d, body=%d, workout=%d, recovery=%d, sleep=%d",
                cycle_resp.status_code, body_resp.status_code,
                workout_resp.status_code, recovery_resp.status_code, sleep_resp.status_code)

    # Check if ALL critical endpoints fail with 401 — token is truly invalid
    critical_statuses = [workout_resp.status_code, recovery_resp.status_code, sleep_resp.status_code]
    if all(s == 401 for s in critical_statuses):
        # All endpoints 401 — raise so caller can handle token refresh
        workout_resp.raise_for_status()

    # Handle individual endpoint failures gracefully
    for name, resp in [("cycle", cycle_resp), ("body", body_resp)]:
        if resp.status_code != 200:
            logger.warning("WHOOP %s endpoint returned %d, skipping", name, resp.status_code)

    # --- Cycle (calories + strain) ---
    cycle_records = cycle_resp.json().get("records", []) if cycle_resp.status_code == 200 else []
    calories_out = 0
    strain = 0.0
    cycle_score_state = "no_data"
    scored_cycle = None
    if cycle_records:
        cycle_score_state = cycle_records[0].get("score_state", "PENDING_SCORE")
        for c in cycle_records:
            if c.get("score_state") == "SCORED":
                scored_cycle = c
                score = c.get("score", {}) or {}
                kj = score.get("kilojoule", 0) or 0
                calories_out = round(kj / 4.184)
                strain = round(score.get("strain", 0) or 0, 1)
                break

    # --- Body measurement ---
    body_records = body_resp.json().get("records", []) if body_resp.status_code == 200 else []
    body_info = ""
    body_weight_kg = 0.0
    body_height_m = 0.0
    if body_records:
        b = body_records[0]
        weight = round(b.get("weight_kilogram", 0) or 0, 1)
        height = round(b.get("height_meter", 0) or 0, 2)
        max_hr = round(b.get("max_heart_rate", 0) or 0)
        body_weight_kg = weight
        body_height_m = height
        if weight:
            body_info = f"Weight: {weight} kg"
            if height:
                body_info += f", height {height} m"
            if max_hr:
                body_info += f", max HR {max_hr} bpm"

    # --- Workouts (today only — filtered by API start=today_utc) ---
    workout_records = workout_resp.json().get("records", []) if workout_resp.status_code == 200 else []
    workout_count = len(workout_records)
    activities_info = ""
    if workout_records:
        parts = []
        for w in workout_records[:5]:
            sport = w.get("sport_name", "unknown")
            ws = w.get("score", {}) or {}
            cal = round((ws.get("kilojoule", 0) or 0) / 4.184)
            s = round(ws.get("strain", 0) or 0, 1)
            avg_hr = round(ws.get("average_heart_rate", 0) or 0)
            max_hr_w = round(ws.get("max_heart_rate", 0) or 0)
            parts.append(
                f"{sport} ({cal} kcal, strain {s}, "
                f"avg HR {avg_hr}, max HR {max_hr_w})"
            )
        activities_info = "Today's workouts: " + "; ".join(parts)

    # --- Recovery (most recent scored, API returns newest first) ---
    recovery_records = recovery_resp.json().get("records", []) if recovery_resp.status_code == 200 else []
    recovery_info = ""
    for i, r in enumerate(recovery_records):
        rs = r.get("score")
        score_state = r.get("score_state", "?")
        rec_score = rs.get("recovery_score") if rs else None
        logger.info(
            "WHOOP recovery[%d]: cycle_id=%s score_state=%s recovery=%s created=%s",
            i, r.get("cycle_id", "?"), score_state, rec_score,
            r.get("created_at", "?")[:19],
        )
        if rs and rec_score is not None:
            logger.info("WHOOP recovery selected: [%d] recovery=%s%%", i, rec_score)
            recovery_info = (
                f"Recovery: {rs['recovery_score']}%, "
                f"resting HR {rs.get('resting_heart_rate', 0)} bpm, "
                f"HRV {round(rs.get('hrv_rmssd_milli', 0) or 0, 1)} ms"
            )
            if rs.get("spo2_percentage"):
                recovery_info += f", SpO2 {rs['spo2_percentage']}%"
            if rs.get("skin_temp_celsius"):
                recovery_info += f", skin temp {rs['skin_temp_celsius']}°C"
            break

    # --- Sleep (pick the sleep that ended today = woke up today) ---
    sleep_records = sleep_resp.json().get("records", []) if sleep_resp.status_code == 200 else []
    sleep_info = ""
    today_start_utc = datetime.fromisoformat(today_utc)
    for i, s in enumerate(sleep_records):
        ss_state = s.get("score_state", "?")
        ss = s.get("score", {}) or {}
        perf = ss.get("sleep_performance_percentage", "?")
        stages = ss.get("stage_summary", {}) or {}
        in_bed_ms = stages.get("total_in_bed_time_milli", 0) or 0
        awake_ms_dbg = stages.get("total_awake_time_milli", 0) or 0
        total_h = round((in_bed_ms - awake_ms_dbg) / 3600000, 1) if in_bed_ms else 0
        logger.info(
            "WHOOP sleep[%d]: id=%s state=%s perf=%s total=%.1fh start=%s end=%s",
            i, s.get("id", "?"), ss_state, perf, total_h,
            s.get("start", "?")[:19], s.get("end", "?")[:19],
        )
    for s in sleep_records:
        # Only use sleep that ended today (user woke up today)
        sleep_end = s.get("end")
        if sleep_end and _parse_dt(sleep_end) >= today_start_utc:
            ss = s.get("score", {})
            stages = (ss or {}).get("stage_summary", {})
            if stages and stages.get("total_in_bed_time_milli"):
                in_bed_ms = stages["total_in_bed_time_milli"]
                awake_ms = stages.get("total_awake_time_milli", 0) or 0
                sleep_ms = in_bed_ms - awake_ms
                total_h = round(sleep_ms / 3600000, 1)
                rem_h = round((stages.get("total_rem_sleep_time_milli", 0) or 0) / 3600000, 1)
                deep_h = round((stages.get("total_slow_wave_sleep_time_milli", 0) or 0) / 3600000, 1)
                light_h = round((stages.get("total_light_sleep_time_milli", 0) or 0) / 3600000, 1)
                awake_min = round(awake_ms / 60000)
                perf = (ss.get("sleep_performance_percentage", 0) or 0)
                consistency = (ss.get("sleep_consistency_percentage", 0) or 0)
                efficiency = (ss.get("sleep_efficiency_percentage", 0) or 0)
                resp_rate = round((ss.get("respiratory_rate", 0) or 0), 1)
                sleep_info = (
                    f"Last sleep: {total_h}h total, performance {perf}%, "
                    f"consistency {consistency}%, efficiency {efficiency}%, "
                    f"REM {rem_h}h, deep {deep_h}h, light {light_h}h, "
                    f"awake {awake_min} min, respiratory rate {resp_rate} rpm"
                )
                logger.info("WHOOP sleep selected: id=%s %.1fh perf=%s%% end=%s",
                            s.get("id", "?"), total_h, perf, sleep_end[:19])
                break

    # --- Real-time calorie estimate for in-progress cycles ---
    # When today's cycle is PENDING_SCORE, estimate calories using the last
    # scored cycle's hourly burn rate * hours since wake-up.
    if cycle_score_state == "PENDING_SCORE" and scored_cycle and calories_out > 0:
        cycle_start = _parse_dt(scored_cycle["start"])
        cycle_end = _parse_dt(scored_cycle["end"])
        cycle_hours = max((cycle_end - cycle_start).total_seconds() / 3600, 1)
        hourly_rate = calories_out / cycle_hours

        # Wake-up time from today's sleep (ended today)
        wake_time = None
        for s in sleep_records:
            sleep_end = s.get("end")
            if sleep_end and _parse_dt(sleep_end) >= today_start_utc:
                wake_time = _parse_dt(sleep_end)
                break

        now = datetime.now(timezone.utc)
        if wake_time and wake_time < now:
            hours_since_wake = (now - wake_time).total_seconds() / 3600
            calories_out = round(hourly_rate * hours_since_wake)
            cycle_score_state = "ESTIMATED"

            logger.info(
                "WHOOP estimated calories: rate=%.1f/h, hours_awake=%.1f, total=%d",
                hourly_rate, hours_since_wake, calories_out,
            )

    logger.info(
        "WHOOP context: cycle_state=%s calories=%s strain=%s workouts=%d",
        cycle_score_state, calories_out, strain, workout_count,
    )

    return {
        "calories_out": calories_out,
        "strain": strain,
        "workout_count": workout_count,
        "cycle_score_state": cycle_score_state,
        "sleep_info": sleep_info,
        "recovery_info": recovery_info,
        "activities_info": activities_info,
        "body_info": body_info,
        "body_weight_kg": body_weight_kg,
        "body_height_m": body_height_m,
    }


async def refresh_whoop_tokens() -> None:
    """Proactively refresh WHOOP tokens that expire within 10 minutes.

    Only refreshes tokens close to expiry to avoid race conditions with
    get_today_stats which also refreshes tokens on demand.
    Force-refreshing all tokens every 30min was causing the old refresh_token
    to be invalidated before other jobs could use it — resulting in disconnects.
    """
    logger.info("Starting WHOOP token refresh")

    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT id, telegram_user_id, whoop_access_token,
                  whoop_refresh_token, whoop_token_expires_at, language
           FROM users
           WHERE whoop_access_token IS NOT NULL
                 AND whoop_refresh_token IS NOT NULL
                 AND whoop_refresh_token != ''
                 AND whoop_token_expires_at < NOW() + INTERVAL '10 minutes'"""
    )

    if not rows:
        logger.info("WHOOP token refresh: no tokens expiring soon")
        return

    refreshed = 0
    async with httpx.AsyncClient(timeout=_http_timeout()) as client:
        for row in rows:
            refreshed += await _refresh_one_scheduled(dict(row), client, pool)

    logger.info("WHOOP token refresh complete: %d/%d refreshed", refreshed, len(rows))


async def _refresh_one_scheduled(user: dict, client: httpx.AsyncClient, pool: Any) -> int:
    try:
        await refresh_token_if_needed(user, client, pool, force=True)
        return 1
    except TokenExpiredError:
        logger.warning("WHOOP token expired for user_id=%s during refresh", user["id"])
        try:
            from app.i18n import t
            from app.services.telegram_bot import send_message

            await send_message(user["telegram_user_id"], t("whoop_expired", user.get("language")))
        except Exception:
            logger.warning("Failed to notify user_id=%s about WHOOP expiry", user["id"])
    except Exception:
        logger.exception("Failed to refresh WHOOP token for user_id=%s", user["id"])
    return 0
