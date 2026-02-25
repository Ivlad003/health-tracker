from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from app.services.whoop_sync import refresh_whoop_tokens

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


def start_scheduler():
    # WHOOP token refresh every 30 minutes
    scheduler.add_job(
        refresh_whoop_tokens,
        trigger=IntervalTrigger(minutes=30),
        id="whoop_token_refresh",
        name="WHOOP Token Refresh (30min)",
        replace_existing=True,
    )

    # FatSecret token health check every 30 minutes
    from app.services.fatsecret_api import check_fatsecret_tokens
    scheduler.add_job(
        check_fatsecret_tokens,
        trigger=IntervalTrigger(minutes=30),
        id="fatsecret_token_check",
        name="FatSecret Token Check (30min)",
        replace_existing=True,
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
        "Scheduler started — tokens 30min (WHOOP+FatSecret), "
        "morning 08:00 Kyiv, evening 21:00 Kyiv, cleanup 03:00 UTC"
    )


def stop_scheduler():
    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")
