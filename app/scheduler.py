import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.services.whoop_sync import sync_whoop_data

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


def start_scheduler():
    scheduler.add_job(
        sync_whoop_data,
        trigger=IntervalTrigger(hours=1),
        id="whoop_data_sync",
        name="WHOOP Data Sync (hourly)",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started — WHOOP sync every 1 hour")


def stop_scheduler():
    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")
