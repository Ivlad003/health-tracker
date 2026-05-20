from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request

from app.database import get_pool
from app.services.apple_health import (
    AppleHealthIngestionError,
    _get_active_sync,
    ingest_apple_health_payload,
    verify_apple_health_token,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/health/apple-health", tags=["apple-health"])


@router.post("/sync")
async def sync_apple_health(
    request: Request,
    x_apple_health_token: Optional[str] = Header(default=None),
):
    body = await request.body()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    try:
        telegram_user_id = int(payload["userId"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="userId must be a Telegram user id") from exc

    pool = await get_pool()
    try:
        sync = await _get_active_sync(pool, telegram_user_id)
    except AppleHealthIngestionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if not verify_apple_health_token(x_apple_health_token, sync["secret_key"]):
        raise HTTPException(status_code=401, detail="Invalid Apple Health token")

    try:
        return await ingest_apple_health_payload(pool, payload)
    except AppleHealthIngestionError as exc:
        logger.warning("Apple Health payload rejected for user_id=%s: %s", sync["user_id"], exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
