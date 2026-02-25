import json
import logging
import platform
import queue
import sys
import threading
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.config import settings


class JSONFormatter(logging.Formatter):
    """JSON log formatter for stdout."""

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


class NewRelicLogHandler(logging.Handler):
    """Send logs to New Relic Log API in batches via background thread."""

    ENDPOINT = "https://log-api.eu.newrelic.com/log/v1"
    BATCH_SIZE = 50
    FLUSH_INTERVAL = 5.0  # seconds

    def __init__(self, api_key: str, app_name: str):
        super().__init__()
        self.api_key = api_key
        self.app_name = app_name
        self.hostname = platform.node()
        self._queue: queue.Queue = queue.Queue(maxsize=5000)
        self._shutdown = threading.Event()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "timestamp": int(record.created * 1000),
                "message": record.getMessage(),
                "attributes": {
                    "level": record.levelname,
                    "logger": record.name,
                },
            }
            if record.exc_info and record.exc_info[0]:
                entry["attributes"]["error.class"] = record.exc_info[0].__name__
                entry["attributes"]["error.message"] = str(record.exc_info[1])
            self._queue.put_nowait(entry)
        except queue.Full:
            pass

    def _worker(self) -> None:
        while not self._shutdown.is_set():
            batch: list[dict] = []
            deadline = time.monotonic() + self.FLUSH_INTERVAL
            while len(batch) < self.BATCH_SIZE:
                remaining = max(0, deadline - time.monotonic())
                if remaining <= 0:
                    break
                try:
                    batch.append(self._queue.get(timeout=remaining))
                except queue.Empty:
                    break
            if batch:
                self._send(batch)

    def _send(self, batch: list[dict]) -> None:
        payload = [
            {
                "common": {
                    "attributes": {
                        "service": self.app_name,
                        "hostname": self.hostname,
                    }
                },
                "logs": batch,
            }
        ]
        try:
            httpx.post(
                self.ENDPOINT,
                json=payload,
                headers={
                    "Api-Key": self.api_key,
                    "Content-Type": "application/json",
                },
                timeout=5.0,
            )
        except Exception:
            pass  # never let logging crash the app

    def close(self) -> None:
        self._shutdown.set()
        self._thread.join(timeout=3.0)
        super().close()


# --- Logging setup ---
handlers: list[logging.Handler] = []

stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.setFormatter(JSONFormatter())
handlers.append(stdout_handler)

if settings.new_relic_license_key:
    nr_handler = NewRelicLogHandler(
        api_key=settings.new_relic_license_key,
        app_name="app_bot_health",
    )
    handlers.append(nr_handler)

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    handlers=handlers,
)
for noisy in ("httpx", "httpcore", "uvicorn.access", "telegram",
               "apscheduler", "asyncpg", "openai", "newrelic"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
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
