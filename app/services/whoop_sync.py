from __future__ import annotations

import asyncio
import httpx
import logging
from datetime import datetime, timezone

from app.config import settings
from app.database import get_pool

logger = logging.getLogger(__name__)

WHOOP_TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
WHOOP_API_BASE = "https://api.prod.whoop.com/developer/v2"


class TokenExpiredError(Exception):
    """Raised when an OAuth token is expired and refresh failed — user must re-authorize."""

    def __init__(self, service: str):
        self.service = service
        super().__init__(f"{service} token expired, re-authorization required")


def _parse_dt(s: str) -> datetime:
    """Parse ISO 8601 datetime string from WHOOP API into datetime object."""
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


async def refresh_token_if_needed(
    user: dict, client: httpx.AsyncClient, pool, *, force: bool = False,
) -> str:
    """Check if token is expired, refresh if needed, return valid access_token.

    Args:
        force: If True, refresh even if token hasn't expired (e.g. after 401).
    """
    expires_at = user["whoop_token_expires_at"]
    if not force and expires_at and expires_at > datetime.now(timezone.utc):
        return user["whoop_access_token"]

    logger.info("Refreshing WHOOP token for user_id=%s", user["id"])
    resp = None
    last_err = None
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
        except (httpx.ConnectError, httpx.ReadTimeout) as e:
            last_err = e
            if attempt < 2:
                await asyncio.sleep(1 * (attempt + 1))
                logger.warning("WHOOP token refresh retry %d for user_id=%s: %s",
                               attempt + 1, user["id"], e)
    if resp is None:
        logger.error("WHOOP token refresh failed after 3 retries for user_id=%s: %s",
                      user["id"], last_err)
        raise last_err
    if resp.status_code in (400, 401, 403):
        logger.error(
            "WHOOP token refresh failed for user_id=%s: status=%s body=%s",
            user["id"], resp.status_code, resp.text,
        )
        await pool.execute(
            """UPDATE users
               SET whoop_access_token = NULL,
                   whoop_refresh_token = NULL,
                   whoop_token_expires_at = NULL,
                   updated_at = NOW()
               WHERE id = $1""",
            user["id"],
        )
        logger.warning("Cleared WHOOP tokens for user_id=%s — re-auth required", user["id"])
        raise TokenExpiredError("whoop")
    if resp.status_code != 200:
        logger.error(
            "WHOOP token refresh failed for user_id=%s: status=%s body=%s",
            user["id"], resp.status_code, resp.text,
        )
        resp.raise_for_status()
    tokens = resp.json()
    logger.info("WHOOP token refreshed for user_id=%s, expires_in=%s",
                user["id"], tokens.get("expires_in"))

    await pool.execute(
        """UPDATE users
           SET whoop_access_token = $1,
               whoop_refresh_token = $2,
               whoop_token_expires_at = NOW() + make_interval(secs => $3),
               updated_at = NOW()
           WHERE id = $4""",
        tokens["access_token"],
        tokens["refresh_token"],
        tokens["expires_in"],
        user["id"],
    )

    return tokens["access_token"]


async def fetch_whoop_context(access_token: str) -> dict:
    """Fetch ALL WHOOP data directly from API for real-time GPT context.

    Fetches cycle, body measurement, workouts, recovery, and sleep in parallel.
    Uses timezone-aware "today" filtering so data matches user's current day.
    """
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    user_tz = ZoneInfo("Europe/Kyiv")
    today_local = datetime.now(user_tz).replace(hour=0, minute=0, second=0, microsecond=0)
    today_utc = today_local.astimezone(timezone.utc).isoformat()
    # 48h window: cycle needs it for estimation, recovery/sleep need it because
    # today's recovery is linked to yesterday's cycle (which started yesterday).
    start_48h = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()

    headers = {"Authorization": f"Bearer {access_token}"}

    logger.info("WHOOP API: fetching 5 endpoints (cycle, body, workout, recovery, sleep)")
    async with httpx.AsyncClient(timeout=15.0) as client:
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

    for resp in (cycle_resp, body_resp, workout_resp, recovery_resp, sleep_resp):
        resp.raise_for_status()

    logger.info("WHOOP API responses: cycle=%d, body=%d, workout=%d, recovery=%d, sleep=%d",
                cycle_resp.status_code, body_resp.status_code,
                workout_resp.status_code, recovery_resp.status_code, sleep_resp.status_code)

    # --- Cycle (calories + strain) ---
    cycle_records = cycle_resp.json().get("records", [])
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
    body_records = body_resp.json().get("records", [])
    body_info = ""
    if body_records:
        b = body_records[0]
        weight = round(b.get("weight_kilogram", 0) or 0, 1)
        height = round(b.get("height_meter", 0) or 0, 2)
        max_hr = round(b.get("max_heart_rate", 0) or 0)
        if weight:
            body_info = f"Weight: {weight} kg"
            if height:
                body_info += f", height {height} m"
            if max_hr:
                body_info += f", max HR {max_hr} bpm"

    # --- Workouts (today only — filtered by API start=today_utc) ---
    workout_records = workout_resp.json().get("records", [])
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
    recovery_records = recovery_resp.json().get("records", [])
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
    sleep_records = sleep_resp.json().get("records", [])
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
    }


async def refresh_whoop_tokens():
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
                  whoop_refresh_token, whoop_token_expires_at
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
    for row in rows:
        user = dict(row)
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                await refresh_token_if_needed(user, client, pool, force=True)
            refreshed += 1
        except TokenExpiredError:
            logger.warning("WHOOP token expired for user_id=%s during refresh", user["id"])
            try:
                from app.services.telegram_bot import send_message
                await send_message(
                    user["telegram_user_id"],
                    "⌚ WHOOP сесія закінчилась.\n"
                    "\n"
                    "🔑 Потрібно перепідключити → /connect_whoop",
                )
            except Exception:
                logger.warning("Failed to notify user_id=%s about WHOOP expiry", user["id"])
        except Exception:
            logger.exception("Failed to refresh WHOOP token for user_id=%s", user["id"])

    logger.info("WHOOP token refresh complete: %d/%d refreshed", refreshed, len(rows))
