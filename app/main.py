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

    await get_pool()
    logger.info("App started")
    yield
    await close_pool()
    logger.info("App stopped")


app = FastAPI(title="Health Tracker API", lifespan=lifespan)

from app.routers.utils import router as utils_router

app.include_router(utils_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
