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
    client_host = request.client.host if request.client else "unknown"
    content_type = request.headers.get("content-type", "<missing>")
    user_agent = request.headers.get("user-agent", "<missing>")
    body = await request.body()
    body_len = len(body)
    body_preview = body[:500].decode("utf-8", errors="replace") if body else "<empty>"
    has_query_token = bool(token)
    has_header_token = bool(x_apple_health_token)

    logger.info(
        "AppleHealth IN client=%s ua=%r ct=%r body_len=%d query_userId=%s "
        "has_query_token=%s has_header_token=%s body_preview=%r",
        client_host, user_agent, content_type, body_len, query_user_id,
        has_query_token, has_header_token, body_preview,
    )

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        logger.warning(
            "AppleHealth REJECT json_parse client=%s body_len=%d body_preview=%r err=%s",
            client_host, body_len, body_preview, exc,
        )
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    # Health Auto Export iOS app uses {"data": {"metrics": [...]}}; it has no
    # userId in the body, so URL userId is mandatory for that shape.
    is_hae = is_health_auto_export_payload(payload)
    payload_keys = list(payload.keys()) if isinstance(payload, dict) else f"<{type(payload).__name__}>"
    native_metric_count = (
        len(payload.get("metrics", [])) if isinstance(payload, dict) and isinstance(payload.get("metrics"), list) else 0
    )
    logger.info(
        "AppleHealth PARSED format=%s payload_keys=%s native_metric_count=%d",
        "auto_export" if is_hae else "native", payload_keys, native_metric_count,
    )

    payload_user_id = None if is_hae else payload.get("userId") if isinstance(payload, dict) else None
    try:
        telegram_user_id = int(payload_user_id if payload_user_id is not None else query_user_id)
    except (TypeError, ValueError) as exc:
        logger.warning(
            "AppleHealth REJECT userId client=%s payload_user_id=%r query_user_id=%r",
            client_host, payload_user_id, query_user_id,
        )
        raise HTTPException(status_code=400, detail="userId must be a Telegram user id") from exc
    if payload_user_id is not None and query_user_id is not None and int(payload_user_id) != query_user_id:
        raise HTTPException(status_code=400, detail="Payload userId does not match URL userId")

    pool = await get_pool()
    try:
        sync = await _get_active_sync(pool, telegram_user_id)
    except AppleHealthIngestionError as exc:
        logger.warning(
            "AppleHealth REJECT no_active_sync telegram_user_id=%s err=%s",
            telegram_user_id, exc,
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    provided_token = x_apple_health_token or token
    if not verify_apple_health_token(provided_token, sync["secret_key"]):
        logger.warning(
            "AppleHealth REJECT bad_token user_id=%s telegram_user_id=%s "
            "header_token_len=%d query_token_len=%d expected_len=%d",
            sync["user_id"], telegram_user_id,
            len(x_apple_health_token or ""), len(token or ""), len(sync["secret_key"] or ""),
        )
        raise HTTPException(status_code=401, detail="Invalid Apple Health token")

    if is_hae:
        payload = convert_health_auto_export(payload, telegram_user_id=telegram_user_id)
        logger.info(
            "AppleHealth CONVERTED hae→native user_id=%s flattened_metric_count=%d",
            sync["user_id"], len(payload.get("metrics", [])),
        )
    else:
        payload["userId"] = telegram_user_id

    metrics = payload.get("metrics") if isinstance(payload, dict) else None
    sample_metric = metrics[0] if isinstance(metrics, list) and metrics else None
    logger.info(
        "AppleHealth INGEST user_id=%s metric_count=%d sample_metric=%r",
        sync["user_id"], len(metrics) if isinstance(metrics, list) else 0, sample_metric,
    )

    try:
        result = await ingest_apple_health_payload(pool, payload)
        logger.info(
            "AppleHealth OK user_id=%s received=%d processed=%d failed=%d",
            sync["user_id"], result["records_received"],
            result["records_processed"], result["records_failed"],
        )
        return result
    except AppleHealthIngestionError as exc:
        logger.warning(
            "AppleHealth REJECT ingestion user_id=%s err=%s sample_metric=%r",
            sync["user_id"], exc, sample_metric,
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
