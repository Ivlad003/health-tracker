from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from app.services.whoop_sync import sync_whoop_data

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


def start_scheduler():
    # WHOOP data sync every hour (existing)
    scheduler.add_job(
        sync_whoop_data,
        trigger=IntervalTrigger(hours=1),
        id="whoop_data_sync",
        name="WHOOP Data Sync (hourly)",
        replace_existing=True,
        next_run_time=datetime.now(),
    )

    # FatSecret diary sync every hour
    from app.services.fatsecret_api import sync_fatsecret_data
    scheduler.add_job(
        sync_fatsecret_data,
        trigger=IntervalTrigger(hours=1),
        id="fatsecret_data_sync",
        name="FatSecret Data Sync (hourly)",
        replace_existing=True,
        next_run_time=datetime.now(),
    )

    # Morning briefing at 08:00 Kyiv (handles DST automatically)
    from app.services.briefings import morning_briefing
    scheduler.add_job(
        morning_briefing,
        trigger=CronTrigger(hour=8, minute=0, timezone="Europe/Kyiv"),
        id="morning_briefing",
        name="Morning Briefing (08:00 Kyiv)",
        replace_existing=True,
    )

    # Evening summary at 21:00 Kyiv (handles DST automatically)
    from app.services.briefings import evening_summary
    scheduler.add_job(
        evening_summary,
        trigger=CronTrigger(hour=21, minute=0, timezone="Europe/Kyiv"),
        id="evening_summary",
        name="Evening Summary (21:00 Kyiv)",
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
        "Scheduler started — WHOOP sync 1h, FatSecret sync 1h, "
        "morning 08:00 Kyiv, evening 21:00 Kyiv, cleanup 03:00 UTC"
    )


def stop_scheduler():
    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")
