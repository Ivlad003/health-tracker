import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.database import get_pool, close_pool
    from app.scheduler import start_scheduler, stop_scheduler

    await get_pool()
    start_scheduler()
    logger.info("App started")
    yield
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
