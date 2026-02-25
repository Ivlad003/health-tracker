import json
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings


class JSONFormatter(logging.Formatter):
    """JSON log formatter for Loki/Grafana ingestion."""

    def format(self, record: logging.LogRecord) -> str:
        log = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            log["exc"] = self.formatException(record.exc_info)
        return json.dumps(log, ensure_ascii=False)


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JSONFormatter())
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    handlers=[handler],
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.database import get_pool, close_pool
    from app.scheduler import start_scheduler, stop_scheduler
    from app.services.telegram_bot import start_bot, stop_bot

    await get_pool()
    start_scheduler()
    await start_bot()
    logger.info("App started")
    yield
    await stop_bot()
    stop_scheduler()
    await close_pool()
    logger.info("App stopped")


app = FastAPI(title="Health Tracker API", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


from app.routers.utils import router as utils_router
from app.routers.fatsecret import router as fatsecret_router
from app.routers.whoop import router as whoop_router

app.include_router(utils_router)
app.include_router(fatsecret_router)
app.include_router(whoop_router)
