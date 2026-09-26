import json
import logging
import platform
import queue
import re
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

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


_SECRET_QUERY_RE = re.compile(
    r"(?i)\b(token|oauth_token|oauth_verifier|code|state|access_token|refresh_token)=([^&\s\"']+)"
)


def redact_secrets(text: str) -> str:
    """Mask credential-bearing query parameters (Apple Health token, OAuth codes)."""
    return _SECRET_QUERY_RE.sub(lambda m: f"{m.group(1)}=***", text)


class SecretRedactingFilter(logging.Filter):
    """Redact secrets from log records before any handler formats them.

    The Apple Health Shortcut authenticates with `?token=` in the URL, and
    uvicorn's access log prints full request paths.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_secrets(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                redact_secrets(arg) if isinstance(arg, str) else arg for arg in record.args
            )
        return True


# --- Logging setup ---
handlers: list[logging.Handler] = []
_redactor = SecretRedactingFilter()

stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.setFormatter(JSONFormatter())
stdout_handler.addFilter(_redactor)
handlers.append(stdout_handler)

if settings.new_relic_license_key:
    nr_handler = NewRelicLogHandler(
        api_key=settings.new_relic_license_key,
        app_name="app_bot_health",
    )
    nr_handler.addFilter(_redactor)
    handlers.append(nr_handler)

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    handlers=handlers,
)
# uvicorn's access/error loggers use their own handlers (propagate=False), so
# attach the redactor at the logger level as well.
for _uvicorn_logger in ("uvicorn.access", "uvicorn.error"):
    logging.getLogger(_uvicorn_logger).addFilter(_redactor)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.database import get_pool, close_pool
    from app.db_preflight import verify_apple_health_schema
    from app.scheduler import start_scheduler, stop_scheduler
    from app.services.telegram_bot import start_bot, stop_bot

    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            await verify_apple_health_schema(conn)
    except Exception:
        await close_pool()
        raise
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
async def health() -> dict:
    return {"status": "ok"}


# Routers import services that read settings; keep them after logging setup.
from app.routers.utils import router as utils_router  # noqa: E402
from app.routers.fatsecret import router as fatsecret_router  # noqa: E402
from app.routers.whoop import router as whoop_router  # noqa: E402
from app.routers.apple_health import router as apple_health_router  # noqa: E402
from app.routers.webapp import router as webapp_router  # noqa: E402
from app.routers.admin import router as admin_router  # noqa: E402

app.include_router(utils_router)
app.include_router(fatsecret_router)
app.include_router(whoop_router)
app.include_router(apple_health_router)
app.include_router(webapp_router)
app.include_router(admin_router)


# Telegram Web App. Docker builds web/ into web/dist and this serves it at /app/.
# Without a bundle, /app/ tells the visitor to open the bot. The API still
# requires a validated initData session; the static page is not an auth bypass.
_WEB_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"
_WEBAPP_PLACEHOLDER = """<!DOCTYPE html><html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Health Tracker</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
background:var(--tg-theme-bg-color,#0a0a0a);color:var(--tg-theme-text-color,#e4e4e7);
display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;padding:24px;
text-align:center}</style></head><body><div><h1>Health Tracker</h1>
<p>Open this app from the Telegram bot (/app). / Відкрийте застосунок з Telegram-бота (/app).</p>
</div></body></html>"""

if _WEB_DIST.is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/app", StaticFiles(directory=_WEB_DIST, html=True), name="webapp")
else:
    from fastapi.responses import HTMLResponse

    @app.get("/app/", include_in_schema=False)
    @app.get("/app", include_in_schema=False)
    async def webapp_placeholder() -> HTMLResponse:
        return HTMLResponse(_WEBAPP_PLACEHOLDER)
