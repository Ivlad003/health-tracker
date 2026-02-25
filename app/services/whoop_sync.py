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
    resp = await client.post(
        WHOOP_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": user["whoop_refresh_token"],
            "client_id": settings.whoop_client_id,
            "client_secret": settings.whoop_client_secret,
        },
    )
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


def process_workouts(data: dict, user_id: int) -> list[dict]:
    """Transform WHOOP workout API response into DB-ready dicts."""
    records = data.get("records", [])
    if not records:
        return []
    return [
        {
            "user_id": user_id,
            "whoop_workout_id": str(w["id"]),
            "sport_name": w.get("sport_name", "unknown"),
            "score_state": w.get("score_state", "PENDING_SCORE"),
            "kilojoules": w.get("score", {}).get("kilojoule", 0),
            "calories": (w.get("score", {}).get("kilojoule", 0) or 0) / 4.184,
            "strain": w.get("score", {}).get("strain", 0),
            "avg_heart_rate": w.get("score", {}).get("average_heart_rate", 0),
            "max_heart_rate": w.get("score", {}).get("max_heart_rate", 0),
            "started_at": _parse_dt(w["start"]),
            "ended_at": _parse_dt(w["end"]),
        }
        for w in records
    ]


def process_recovery(data: dict, user_id: int) -> list[dict]:
    """Transform WHOOP recovery API response into DB-ready dicts."""
    records = data.get("records", [])
    if not records:
        return []
    return [
        {
            "user_id": user_id,
            "whoop_cycle_id": str(r["cycle_id"]),
            "recovery_score": r.get("score", {}).get("recovery_score", 0),
            "resting_heart_rate": r.get("score", {}).get("resting_heart_rate", 0),
            "hrv_rmssd_milli": r.get("score", {}).get("hrv_rmssd_milli", 0),
            "spo2_percentage": r.get("score", {}).get("spo2_percentage", 0),
            "skin_temp_celsius": r.get("score", {}).get("skin_temp_celsius", 0),
            "recorded_at": _parse_dt(r["created_at"]),
        }
        for r in records
    ]


def process_sleep(data: dict, user_id: int) -> list[dict]:
    """Transform WHOOP sleep API response into DB-ready dicts."""
    records = data.get("records", [])
    if not records:
        return []
    return [
        {
            "user_id": user_id,
            "whoop_sleep_id": str(s["id"]),
            "score_state": s.get("score_state", "PENDING_SCORE"),
            "sleep_performance": s.get("score", {}).get("sleep_performance_percentage", 0),
            "sleep_consistency": s.get("score", {}).get("sleep_consistency_percentage", 0),
            "sleep_efficiency": s.get("score", {}).get("sleep_efficiency_percentage", 0),
            "total_sleep_milli": s.get("score", {}).get("stage_summary", {}).get("total_in_bed_time_milli", 0),
            "total_rem_milli": s.get("score", {}).get("stage_summary", {}).get("total_rem_sleep_time_milli", 0),
            "total_sws_milli": s.get("score", {}).get("stage_summary", {}).get("total_slow_wave_sleep_time_milli", 0),
            "total_light_milli": s.get("score", {}).get("stage_summary", {}).get("total_light_sleep_time_milli", 0),
            "total_awake_milli": s.get("score", {}).get("stage_summary", {}).get("total_awake_time_milli", 0),
            "respiratory_rate": s.get("score", {}).get("respiratory_rate", 0),
            "started_at": _parse_dt(s["start"]),
            "ended_at": _parse_dt(s["end"]),
        }
        for s in records
    ]


async def store_workouts(pool, workouts: list[dict]) -> int:
    """UPSERT workouts into whoop_activities table."""
    if not workouts:
        return 0
    for w in workouts:
        await pool.execute(
            """INSERT INTO whoop_activities
                   (user_id, whoop_workout_id, sport_name, score_state, kilojoules,
                    calories, strain, avg_heart_rate, max_heart_rate, started_at, ended_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
               ON CONFLICT (whoop_workout_id) DO UPDATE SET
                   score_state = EXCLUDED.score_state,
                   calories = EXCLUDED.calories,
                   strain = EXCLUDED.strain,
                   updated_at = NOW()""",
            w["user_id"], w["whoop_workout_id"], w["sport_name"], w["score_state"],
            w["kilojoules"], w["calories"], w["strain"],
            w["avg_heart_rate"], w["max_heart_rate"], w["started_at"], w["ended_at"],
        )
    return len(workouts)


async def store_recovery(pool, records: list[dict]) -> int:
    """UPSERT recovery records into whoop_recovery table."""
    if not records:
        return 0
    for r in records:
        await pool.execute(
            """INSERT INTO whoop_recovery
                   (user_id, whoop_cycle_id, recovery_score, resting_heart_rate,
                    hrv_rmssd_milli, spo2_percentage, skin_temp_celsius, recorded_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
               ON CONFLICT (whoop_cycle_id) DO UPDATE SET
                   recovery_score = EXCLUDED.recovery_score,
                   resting_heart_rate = EXCLUDED.resting_heart_rate,
                   hrv_rmssd_milli = EXCLUDED.hrv_rmssd_milli""",
            r["user_id"], r["whoop_cycle_id"], r["recovery_score"],
            r["resting_heart_rate"], r["hrv_rmssd_milli"],
            r["spo2_percentage"], r["skin_temp_celsius"], r["recorded_at"],
        )
    return len(records)


async def store_sleep(pool, records: list[dict]) -> int:
    """UPSERT sleep records into whoop_sleep table."""
    if not records:
        return 0
    for s in records:
        await pool.execute(
            """INSERT INTO whoop_sleep
                   (user_id, whoop_sleep_id, score_state, sleep_performance_percentage,
                    sleep_consistency_percentage, sleep_efficiency_percentage,
                    total_sleep_time_milli, total_rem_sleep_milli,
                    total_slow_wave_sleep_milli, total_light_sleep_milli,
                    total_awake_milli, respiratory_rate, started_at, ended_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
               ON CONFLICT (whoop_sleep_id) DO UPDATE SET
                   score_state = EXCLUDED.score_state,
                   sleep_performance_percentage = EXCLUDED.sleep_performance_percentage""",
            s["user_id"], s["whoop_sleep_id"], s["score_state"],
            s["sleep_performance"], s["sleep_consistency"], s["sleep_efficiency"],
            s["total_sleep_milli"], s["total_rem_milli"], s["total_sws_milli"],
            s["total_light_milli"], s["total_awake_milli"], s["respiratory_rate"],
            s["started_at"], s["ended_at"],
        )
    return len(records)


async def fetch_whoop_context(access_token: str) -> dict:
    """Fetch ALL WHOOP data directly from API for real-time GPT context.

    Fetches cycle, body measurement, workouts, recovery, and sleep in parallel.
    Returns pre-formatted context strings ready for GPT.
    """
    from datetime import timedelta

    headers = {"Authorization": f"Bearer {access_token}"}
    start_48h = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()

    async with httpx.AsyncClient(timeout=15.0) as client:
        cycle_resp, body_resp, workout_resp, recovery_resp, sleep_resp = (
            await asyncio.gather(
                client.get(f"{WHOOP_API_BASE}/cycle", headers=headers,
                           params={"limit": "5", "start": start_48h}),
                client.get(f"{WHOOP_API_BASE}/body_measurement", headers=headers,
                           params={"limit": "1"}),
                client.get(f"{WHOOP_API_BASE}/activity/workout", headers=headers,
                           params={"limit": "5", "start": start_48h}),
                client.get(f"{WHOOP_API_BASE}/recovery", headers=headers,
                           params={"limit": "5", "start": start_48h}),
                client.get(f"{WHOOP_API_BASE}/activity/sleep", headers=headers,
                           params={"limit": "5", "start": start_48h}),
            )
        )

    for resp in (cycle_resp, body_resp, workout_resp, recovery_resp, sleep_resp):
        resp.raise_for_status()

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

    # --- Workouts ---
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
            started = w.get("start", "")[:16].replace("T", " ")
            parts.append(
                f"{sport} ({cal} kcal, strain {s}, "
                f"avg HR {avg_hr}, max HR {max_hr_w}, {started})"
            )
        activities_info = "Recent workouts: " + "; ".join(parts)

    # --- Recovery ---
    recovery_records = recovery_resp.json().get("records", [])
    recovery_info = ""
    for r in recovery_records:
        rs = r.get("score", {})
        if rs and rs.get("recovery_score") is not None:
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

    # --- Sleep ---
    sleep_records = sleep_resp.json().get("records", [])
    sleep_info = ""
    for s in sleep_records:
        ss = s.get("score", {})
        stages = (ss or {}).get("stage_summary", {})
        if stages and stages.get("total_in_bed_time_milli"):
            total_h = round(stages["total_in_bed_time_milli"] / 3600000, 1)
            rem_h = round((stages.get("total_rem_sleep_time_milli", 0) or 0) / 3600000, 1)
            deep_h = round((stages.get("total_slow_wave_sleep_time_milli", 0) or 0) / 3600000, 1)
            light_h = round((stages.get("total_light_sleep_time_milli", 0) or 0) / 3600000, 1)
            awake_min = round((stages.get("total_awake_time_milli", 0) or 0) / 60000)
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
            break

    # --- Real-time calorie estimate for in-progress cycles ---
    # When today's cycle is PENDING_SCORE (not yet completed), estimate calories
    # using the last scored cycle's hourly burn rate + today's actual workouts.
    if cycle_score_state == "PENDING_SCORE" and scored_cycle and calories_out > 0:
        cycle_start = _parse_dt(scored_cycle["start"])
        cycle_end = _parse_dt(scored_cycle["end"])
        cycle_hours = max((cycle_end - cycle_start).total_seconds() / 3600, 1)

        # Separate base metabolism from workout calories in the scored cycle
        scored_total = calories_out
        scored_workout_cals = 0
        for w in workout_records:
            w_start = _parse_dt(w["start"])
            if cycle_start <= w_start <= cycle_end:
                ws = w.get("score", {}) or {}
                scored_workout_cals += round((ws.get("kilojoule", 0) or 0) / 4.184)

        base_rate_per_hour = max(0, (scored_total - scored_workout_cals) / cycle_hours)

        # Today's workout calories (after the scored cycle ended)
        today_workout_cals = 0
        for w in workout_records:
            w_start = _parse_dt(w["start"])
            if w_start > cycle_end:
                ws = w.get("score", {}) or {}
                today_workout_cals += round((ws.get("kilojoule", 0) or 0) / 4.184)

        # Wake-up time from latest sleep
        wake_time = None
        for s in sleep_records:
            if s.get("end"):
                wake_time = _parse_dt(s["end"])
                break

        now = datetime.now(timezone.utc)
        if wake_time and wake_time < now:
            hours_since_wake = (now - wake_time).total_seconds() / 3600
            calories_out = round(base_rate_per_hour * hours_since_wake) + today_workout_cals
            cycle_score_state = "ESTIMATED"

            logger.info(
                "WHOOP estimated calories: base_rate=%.1f/h, hours_awake=%.1f, "
                "workout_cals=%d, total=%d",
                base_rate_per_hour, hours_since_wake, today_workout_cals, calories_out,
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


async def _fetch_whoop_data(client: httpx.AsyncClient, access_token: str, lookback_hours: int):
    """Fetch workout, recovery, and sleep data from WHOOP API."""
    from datetime import timedelta

    headers = {"Authorization": f"Bearer {access_token}"}
    start = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat()

    workout_resp, recovery_resp, sleep_resp = await asyncio.gather(
        client.get(f"{WHOOP_API_BASE}/activity/workout",
                   headers=headers, params={"limit": "25", "start": start}),
        client.get(f"{WHOOP_API_BASE}/recovery",
                   headers=headers, params={"limit": "10", "start": start}),
        client.get(f"{WHOOP_API_BASE}/activity/sleep",
                   headers=headers, params={"limit": "10", "start": start}),
    )

    for resp in (workout_resp, recovery_resp, sleep_resp):
        resp.raise_for_status()

    return workout_resp, recovery_resp, sleep_resp


async def sync_whoop_user(user: dict, pool, lookback_hours: int = 2) -> None:
    """Sync WHOOP data for a single user with configurable lookback window."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        access_token = await refresh_token_if_needed(user, client, pool)

        try:
            workout_resp, recovery_resp, sleep_resp = await _fetch_whoop_data(
                client, access_token, lookback_hours,
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                logger.warning(
                    "WHOOP sync 401 for user_id=%s, force-refreshing token", user["id"],
                )
                access_token = await refresh_token_if_needed(
                    user, client, pool, force=True,
                )
                workout_resp, recovery_resp, sleep_resp = await _fetch_whoop_data(
                    client, access_token, lookback_hours,
                )
            else:
                raise

        workouts = process_workouts(workout_resp.json(), user["id"])
        recovery = process_recovery(recovery_resp.json(), user["id"])
        sleep = process_sleep(sleep_resp.json(), user["id"])

        w_count = await store_workouts(pool, workouts)
        r_count = await store_recovery(pool, recovery)
        s_count = await store_sleep(pool, sleep)

        logger.info(
            "Synced user_id=%s: %d workouts, %d recovery, %d sleep",
            user["id"], w_count, r_count, s_count,
        )


async def sync_whoop_for_telegram_user(telegram_user_id: int, lookback_hours: int = 168) -> None:
    """Sync WHOOP data for a single user by telegram_user_id. Used after OAuth callback."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """SELECT id, telegram_user_id, whoop_user_id, whoop_access_token,
                  whoop_refresh_token, whoop_token_expires_at
           FROM users
           WHERE telegram_user_id = $1
                 AND whoop_access_token IS NOT NULL
                 AND whoop_user_id IS NOT NULL""",
        telegram_user_id,
    )
    if not row:
        logger.warning("No WHOOP user found for telegram_user_id=%s", telegram_user_id)
        return

    await sync_whoop_user(dict(row), pool, lookback_hours=lookback_hours)


async def sync_whoop_data():
    """Main sync job: runs hourly. Fetches all WHOOP users, refreshes tokens, syncs data."""
    logger.info("Starting WHOOP data sync")

    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT id, telegram_user_id, whoop_user_id, whoop_access_token,
                  whoop_refresh_token, whoop_token_expires_at
           FROM users
           WHERE whoop_access_token IS NOT NULL
                 AND whoop_refresh_token IS NOT NULL
                 AND whoop_refresh_token != ''
                 AND whoop_user_id IS NOT NULL"""
    )

    if not rows:
        logger.info("No WHOOP users to sync")
        return

    for row in rows:
        user = dict(row)
        try:
            await sync_whoop_user(user, pool, lookback_hours=2)
        except TokenExpiredError:
            logger.warning("WHOOP token expired for user_id=%s during hourly sync", user["id"])
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
            logger.exception("Failed to sync WHOOP data for user_id=%s", user["id"])
            continue

    logger.info("WHOOP data sync complete")


async def refresh_whoop_tokens():
    """Proactively refresh WHOOP tokens that expire within 10 minutes.

    Only refreshes tokens close to expiry to avoid race conditions with
    other jobs (sync_whoop_data, get_today_stats) that also refresh tokens.
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
