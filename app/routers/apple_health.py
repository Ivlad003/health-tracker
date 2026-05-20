from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request

from app.database import get_pool
from app.services.apple_health import (
    AppleHealthIngestionError,
    _get_active_sync,
    convert_health_auto_export,
    ingest_apple_health_payload,
    is_health_auto_export_payload,
    verify_apple_health_token,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/health/apple-health", tags=["apple-health"])


@router.post("/sync")
async def sync_apple_health(
    request: Request,
    x_apple_health_token: Optional[str] = Header(default=None),
    query_user_id: Optional[int] = Query(default=None, alias="userId"),
    token: Optional[str] = Query(default=None),
):
    body = await request.body()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    # Health Auto Export iOS app uses {"data": {"metrics": [...]}}; it has no
    # userId in the body, so URL userId is mandatory for that shape.
    is_hae = is_health_auto_export_payload(payload)

    payload_user_id = None if is_hae else payload.get("userId") if isinstance(payload, dict) else None
    try:
        telegram_user_id = int(payload_user_id if payload_user_id is not None else query_user_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="userId must be a Telegram user id") from exc
    if payload_user_id is not None and query_user_id is not None and int(payload_user_id) != query_user_id:
        raise HTTPException(status_code=400, detail="Payload userId does not match URL userId")

    pool = await get_pool()
    try:
        sync = await _get_active_sync(pool, telegram_user_id)
    except AppleHealthIngestionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    provided_token = x_apple_health_token or token
    if not verify_apple_health_token(provided_token, sync["secret_key"]):
        raise HTTPException(status_code=401, detail="Invalid Apple Health token")

    if is_hae:
        payload = convert_health_auto_export(payload, telegram_user_id=telegram_user_id)
    else:
        payload["userId"] = telegram_user_id

    try:
        return await ingest_apple_health_payload(pool, payload)
    except AppleHealthIngestionError as exc:
        logger.warning("Apple Health payload rejected for user_id=%s: %s", sync["user_id"], exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
