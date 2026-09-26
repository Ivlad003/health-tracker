from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.services.whoop_sync import refresh_whoop_tokens

logger = logging.getLogger(__name__)

# coalesce: run a missed job once, not N times; max_instances=1: a slow run
# (many users, slow APIs) never overlaps with the next tick.
scheduler = AsyncIOScheduler(
    job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300},
)

# Defaults kept for manual/legacy invocations; scheduled briefings use the
# per-user times in user_preferences.
MORNING_BRIEFING_LOCAL_HOUR = 8
EVENING_SUMMARY_LOCAL_HOUR = 21


def start_scheduler() -> None:
    # WHOOP token refresh every 30 minutes (only tokens expiring within 10 min)
    scheduler.add_job(
        refresh_whoop_tokens,
        trigger=IntervalTrigger(minutes=30),
        id="whoop_token_refresh",
        name="WHOOP Token Refresh (30min)",
        replace_existing=True,
    )

    # FatSecret OAuth 1.0 tokens never expire, they can only be revoked, and
    # every chat message already detects revocation. A full diary fetch per
    # user every 30 min only burned API quota, so check every 3 hours.
    from app.services.fatsecret_api import check_fatsecret_tokens
    scheduler.add_job(
        check_fatsecret_tokens,
        trigger=IntervalTrigger(hours=3),
        id="fatsecret_token_check",
        name="FatSecret Token Check (3h)",
        replace_existing=True,
    )

    # Briefings: every 5 minutes, each user at their own configured local
    # time (user_preferences, default 08:00 / 21:00), claimed once per local
    # date in notification_sends (restart / DST / multi-replica safe).
    from app.services.briefings import evening_summary, morning_briefing
    scheduler.add_job(
        morning_briefing,
        trigger=IntervalTrigger(minutes=5),
        kwargs={"use_preferences": True},
        id="morning_briefing",
        name="Morning Briefing (user-local, configurable)",
        replace_existing=True,
    )
    scheduler.add_job(
        evening_summary,
        trigger=IntervalTrigger(minutes=5),
        kwargs={"use_preferences": True},
        id="evening_summary",
        name="Evening Summary (user-local, configurable)",
        replace_existing=True,
    )

    # Food ledger: FatSecret outbox (1 min), reconciliation of ambiguous
    # writes (10 min), history → My Products imports (5 min), daily refresh
    # of recent history (04:00 UTC) and the ≤24 h provider-cache purge (hourly).
    from app.services.catalog_import import run_due_jobs, schedule_daily_refresh
    from app.services.food_sync import run_outbox_job, run_reconcile_job
    scheduler.add_job(
        run_outbox_job, trigger=IntervalTrigger(minutes=1),
        id="food_outbox", name="Food FatSecret outbox (1min)", replace_existing=True,
    )
    scheduler.add_job(
        run_reconcile_job, trigger=IntervalTrigger(minutes=10),
        id="food_reconcile", name="Food outbox reconcile (10min)", replace_existing=True,
    )
    scheduler.add_job(
        run_due_jobs, trigger=IntervalTrigger(minutes=5),
        id="catalog_import", name="FatSecret history import (5min)", replace_existing=True,
    )
    scheduler.add_job(
        schedule_daily_refresh, trigger=CronTrigger(hour=4, minute=0, timezone="UTC"),
        id="catalog_refresh", name="FatSecret history refresh (daily)", replace_existing=True,
    )
    scheduler.add_job(
        purge_food_caches, trigger=IntervalTrigger(hours=1),
        id="food_cache_purge", name="Provider cache / drafts / sessions purge (1h)",
        replace_existing=True,
    )

    # Journal reminders every 10 minutes (checks user-configured times ±5 min)
    from app.services.briefings import journal_reminders
    scheduler.add_job(
        journal_reminders,
        trigger=IntervalTrigger(minutes=10),
        id="journal_reminders",
        name="Journal Reminders (10min check)",
        replace_existing=True,
    )

    # Conversation cleanup daily at 03:00 UTC
    from app.services.briefings import cleanup_old_conversations
    scheduler.add_job(
        cleanup_old_conversations,
        trigger=CronTrigger(hour=3, minute=0, timezone="UTC"),
        id="conversation_cleanup",
        name="Conversation Cleanup (daily)",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        "Scheduler started — WHOOP tokens 30min, FatSecret check 3h, briefings "
        "user-local (5min check), journal 10min, food outbox 1min, reconcile 10min, "
        "history import 5min, cache purge 1h, cleanup 03:00 UTC"
    )


async def purge_food_caches() -> None:
    from app.database import get_pool
    from app.services.food_catalog import purge_expired_provider_data
    from app.services.webapp_auth import purge_sessions

    pool = await get_pool()
    result = await purge_expired_provider_data(pool)
    await purge_sessions(pool)
    logger.info("Food cache purge: %s", result)


def stop_scheduler() -> None:
    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")
