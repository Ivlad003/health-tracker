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
    get_apple_health_sync_for_observability,
    ingest_apple_health_payload,
    is_health_auto_export_payload,
    record_apple_health_failure,
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
    has_query_token = bool(token)
    has_header_token = bool(x_apple_health_token)

    logger.info(
        "AppleHealth IN client=%s ua=%r ct=%r body_len=%d has_query_userId=%s "
        "has_query_token=%s has_header_token=%s",
        client_host,
        user_agent,
        content_type,
        body_len,
        query_user_id is not None,
        has_query_token,
        has_header_token,
    )

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        if query_user_id is not None:
            pool = await get_pool()
            sync = await get_apple_health_sync_for_observability(pool, query_user_id)
            if sync:
                provided_token = x_apple_health_token or token
                if not verify_apple_health_token(provided_token, sync["secret_key"]):
                    logger.warning(
                        "AppleHealth REJECT json_parse_unauthenticated client=%s ct=%r body_len=%d",
                        client_host,
                        content_type,
                        body_len,
                    )
                    raise HTTPException(status_code=401, detail="Invalid Apple Health token") from exc
                await record_apple_health_failure(
                    pool,
                    user_id=sync["user_id"],
                    sync_id=sync["sync_id"],
                    http_status=400,
                    error_message="Invalid JSON payload",
                    request_summary={
                        "parse_error": "invalid_json",
                        "body_length": body_len,
                    },
                )
        logger.warning(
            "AppleHealth REJECT json_parse client=%s ct=%r body_len=%d err_class=%s",
            client_host,
            content_type,
            body_len,
            type(exc).__name__,
        )
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    # Health Auto Export iOS app uses {"data": {"metrics": [...]}}; it has no
    # userId in the body, so URL userId is mandatory for that shape.
    is_hae = is_health_auto_export_payload(payload)
    native_metric_count = (
        len(payload.get("metrics", []))
        if isinstance(payload, dict) and isinstance(payload.get("metrics"), list)
        else 0
    )

    payload_user_id = None if is_hae else payload.get("userId") if isinstance(payload, dict) else None
    try:
        telegram_user_id = int(payload_user_id if payload_user_id is not None else query_user_id)
    except (TypeError, ValueError) as exc:
        logger.warning(
            "AppleHealth REJECT userId client=%s ct=%r body_len=%d has_query_userId=%s",
            client_host,
            content_type,
            body_len,
            query_user_id is not None,
        )
        raise HTTPException(status_code=400, detail="userId must be a Telegram user id") from exc
    if payload_user_id is not None and query_user_id is not None and int(payload_user_id) != query_user_id:
        logger.warning(
            "AppleHealth REJECT userId_mismatch client=%s ct=%r body_len=%d",
            client_host,
            content_type,
            body_len,
        )
        raise HTTPException(status_code=400, detail="Payload userId does not match URL userId")

    pool = await get_pool()
    try:
        sync = await _get_active_sync(pool, telegram_user_id)
    except AppleHealthIngestionError as exc:
        inactive_sync = await get_apple_health_sync_for_observability(pool, telegram_user_id)
        if inactive_sync:
            await record_apple_health_failure(
                pool,
                user_id=inactive_sync["user_id"],
                sync_id=inactive_sync["sync_id"],
                http_status=404,
                error_message=str(exc),
            )
        logger.warning(
            "AppleHealth REJECT no_active_sync client=%s ct=%r body_len=%d err_class=%s",
            client_host,
            content_type,
            body_len,
            type(exc).__name__,
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    provided_token = x_apple_health_token or token
    if not verify_apple_health_token(provided_token, sync["secret_key"]):
        await record_apple_health_failure(
            pool,
            user_id=sync["user_id"],
            sync_id=sync["sync_id"],
            http_status=401,
            error_message="Invalid Apple Health token",
        )
        logger.warning(
            "AppleHealth REJECT bad_token client=%s ct=%r body_len=%d "
            "header_token_len=%d query_token_len=%d expected_len=%d",
            client_host,
            content_type,
            body_len,
            len(x_apple_health_token or ""),
            len(token or ""),
            len(sync["secret_key"] or ""),
        )
        raise HTTPException(status_code=401, detail="Invalid Apple Health token")

    logger.info(
        "AppleHealth PARSED user_id=%s sync_id=%s format=%s body_len=%d metric_count=%d",
        sync["user_id"],
        sync["sync_id"],
        "auto_export" if is_hae else "native",
        body_len,
        native_metric_count,
    )

    if is_hae:
        payload = convert_health_auto_export(payload, telegram_user_id=telegram_user_id)
        logger.info(
            "AppleHealth CONVERTED hae_to_native user_id=%s sync_id=%s flattened_metric_count=%d",
            sync["user_id"],
            sync["sync_id"],
            len(payload.get("metrics", [])),
        )
    else:
        payload["userId"] = telegram_user_id

    metrics = payload.get("metrics") if isinstance(payload, dict) else None
    logger.info(
        "AppleHealth INGEST user_id=%s sync_id=%s metric_count=%d",
        sync["user_id"],
        sync["sync_id"],
        len(metrics) if isinstance(metrics, list) else 0,
    )

    try:
        result = await ingest_apple_health_payload(pool, payload)
        logger.info(
            "AppleHealth OK user_id=%s sync_id=%s received=%d processed=%d failed=%d",
            sync["user_id"],
            sync["sync_id"],
            result["records_received"],
            result["records_processed"],
            result["records_failed"],
        )
        return result
    except AppleHealthIngestionError as exc:
        logger.warning(
            "AppleHealth REJECT ingestion user_id=%s sync_id=%s err_class=%s",
            sync["user_id"],
            sync["sync_id"],
            type(exc).__name__,
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
