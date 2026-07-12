from __future__ import annotations

import base64
import json
import logging
import plistlib
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse

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
SHORTCUT_PATH = Path(__file__).resolve().parents[2] / "docs" / "shortcuts" / "apple-health-sync.shortcut"
MACOS_SHORTCUT_HANDOFF_HTML = """<!doctype html>
<html lang="uk">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Open Apple Health Sync on iPhone or iPad</title>
  <style>
    body { font: 17px/1.5 -apple-system, BlinkMacSystemFont, sans-serif; margin: 0; color: #1d1d1f; }
    main { max-width: 680px; margin: 12vh auto; padding: 0 24px; }
    h1 { font-size: 32px; line-height: 1.15; }
    section { margin-top: 32px; }
    button { font: inherit; padding: 10px 16px; border: 0; border-radius: 10px; color: white; background: #0071e3; cursor: pointer; }
  </style>
</head>
<body>
  <main>
    <section>
      <h1>Відкрий Shortcut на iPhone або iPad</h1>
      <p>Цей Shortcut не запускається на Mac: дія <strong>Find Health Samples</strong> доступна лише на iPhone та iPad.</p>
      <p>Відкрий це саме посилання в Telegram або Safari на iPhone чи iPad, імпортуй Shortcut і встав URL, який надіслав бот.</p>
      <button type="button" onclick="navigator.clipboard.writeText(location.href).then(() => this.textContent = 'Скопійовано / Copied')">Скопіювати посилання / Copy link</button>
    </section>
    <section lang="en">
      <h1>Open the Shortcut on iPhone or iPad</h1>
      <p>This Shortcut cannot run on Mac because <strong>Find Health Samples</strong> is available only on iPhone and iPad.</p>
      <p>Open this same link in Telegram or Safari on your iPhone or iPad, import the Shortcut, and paste the URL sent by the bot.</p>
    </section>
  </main>
</body>
</html>
"""


def _is_macos_browser(user_agent: str) -> bool:
    """Distinguish macOS browsers from iPad browsers using desktop-mode UAs."""
    normalized = user_agent.lower()
    looks_like_macos = "macintosh" in normalized or "mac os x" in normalized
    looks_like_ipad = "mobile/" in normalized
    return looks_like_macos and not looks_like_ipad


@router.get("/shortcut")
async def download_apple_health_shortcut(request: Request):
    """Serve the signed Apple Shortcuts template used by Telegram onboarding."""
    if _is_macos_browser(request.headers.get("user-agent", "")):
        return HTMLResponse(
            MACOS_SHORTCUT_HANDOFF_HTML,
            headers={"Cache-Control": "no-store", "Vary": "User-Agent"},
        )
    if not SHORTCUT_PATH.is_file():
        raise HTTPException(status_code=404, detail="Apple Health Shortcut template is not available")
    return FileResponse(
        SHORTCUT_PATH,
        media_type="application/x-shortcut",
        filename="apple-health-sync.shortcut",
        headers={"Vary": "User-Agent"},
    )


def _plist_to_jsonable(value: Any) -> Any:
    """Recursively convert plist-only types to their JSON equivalents.

    Apple property lists natively encode dates (datetime) and raw data
    (bytes); downstream ingestion expects the JSON shapes — ISO 8601
    strings and plain/base64 strings.
    """
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return base64.b64encode(value).decode("ascii")
    if isinstance(value, dict):
        return {str(key): _plist_to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plist_to_jsonable(item) for item in value]
    return value


# Ukrainian labels for the parsed-data summary sent to the user's chat, keyed
# by the normalized metric key returned in ingest result["records_by_type"].
_UA_METRIC_LABELS = {
    "step_count": "кроки",
    "active_energy": "активна енергія",
    "heart_rate": "пульс",
    "heart_rate_variability": "ВСР (HRV)",
    "sleep_analysis": "сон",
}


def _format_ingest_summary_uk(result: dict[str, Any]) -> str:
    """Build the Ukrainian parsed-data summary message from an ingest result.

    Reports samples received/aggregated, how many daily aggregate rows were
    updated, failures, and the raw-retention guarantee (``raw stored: 0``).
    """
    received = result.get("records_received", 0)
    aggregated = result.get("records_aggregated", result.get("records_processed", 0))
    aggregate_rows = result.get("aggregate_rows_updated", 0)
    raw_stored = result.get("raw_stored", 0)
    failed = result.get("records_failed", 0)
    counts_by_type = result.get("records_by_type") or {}
    covered = result.get("covered_dates") or []

    parts = [
        f"{count} {_UA_METRIC_LABELS.get(key, key.replace('_', ' '))}"
        for key, count in sorted(counts_by_type.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    breakdown = ", ".join(parts) if parts else "немає метрик"

    row_word = "день" if aggregate_rows == 1 else "днів"
    lines = [
        "📊 Apple Health синхронізовано",
        f"Отримано {received} семплів, зведено {aggregated} у {aggregate_rows} {row_word}.",
        f"Розпізнано: {breakdown}.",
        f"Сирих семплів збережено: {raw_stored} (зберігаються лише добові підсумки).",
    ]
    if covered:
        lines.append("Дні: " + ", ".join(covered) + ".")
    if failed:
        lines.append(f"⚠️ Помилок: {failed}.")
    unmapped = result.get("unmapped_metric_types") or []
    if unmapped:
        lines.append(
            "ℹ️ Пораховано, але не входить у зведення: " + ", ".join(unmapped) + "."
        )
    return "\n".join(lines)


async def _notify_ingest_summary_to_telegram(
    telegram_user_id: int,
    result: dict[str, Any],
) -> None:
    """Send the human-readable parsed-data summary to the owner's chat.

    Best-effort: a Telegram failure must never break the sync response.
    """
    try:
        from app.services.telegram_bot import send_message

        await send_message(telegram_user_id, _format_ingest_summary_uk(result))
    except Exception:
        logger.exception(
            "AppleHealth ingest summary notification failed for user %s",
            telegram_user_id,
        )


async def _echo_payload_to_telegram(
    telegram_user_id: int,
    body: bytes,
    filename: str,
    caption: str,
) -> None:
    """Echo the raw received sync body to the owner's Telegram chat for debugging.

    Only called after the Apple Health token has been verified. Must never
    break request handling.
    """
    try:
        from app.services.telegram_bot import send_document, send_message

        if body:
            await send_document(
                telegram_user_id,
                document=body,
                filename=filename,
                caption=caption,
            )
        else:
            await send_message(
                telegram_user_id,
                f"{caption}\nТіло запиту порожнє (0 байт).",
            )
    except Exception:
        logger.exception(
            "AppleHealth payload echo to Telegram failed for user %s", telegram_user_id
        )


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

    body_format = "json"
    try:
        payload = json.loads(body)
    # ValueError also covers UnicodeDecodeError: binary plist bodies are not
    # valid UTF-8, so json.loads fails before reaching the JSON parser.
    except ValueError as exc:
        # Apple Shortcuts often posts the dictionary as an Apple property
        # list (binary "bplist00" or XML) even when the flow intends JSON —
        # coercing a plist file to public.json only renames the type.
        # Accept that transparently instead of rejecting the sync.
        try:
            parsed_plist = plistlib.loads(body)
        except Exception:
            parsed_plist = None
        if isinstance(parsed_plist, dict):
            payload = _plist_to_jsonable(parsed_plist)
            body_format = "plist"
            logger.info(
                "AppleHealth PLIST_FALLBACK client=%s ct=%r body_len=%d",
                client_host,
                content_type,
                body_len,
            )
        else:
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
                    await _echo_payload_to_telegram(
                        query_user_id,
                        body,
                        filename="apple-health-payload.txt",
                        caption=(
                            "⚠️ Apple Health: запит відхилено — тіло не є валідним JSON "
                            "або Apple plist (HTTP 400 Invalid JSON payload).\n"
                            f"Content-Type: {content_type}\n"
                            f"Розмір: {body_len} байт.\n"
                            "Отримане тіло запиту — у файлі."
                        ),
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
        "AppleHealth PARSED user_id=%s sync_id=%s format=%s body_format=%s body_len=%d metric_count=%d",
        sync["user_id"],
        sync["sync_id"],
        "auto_export" if is_hae else "native",
        body_format,
        body_len,
        native_metric_count,
    )

    await _echo_payload_to_telegram(
        telegram_user_id,
        body,
        filename="apple-health-payload.plist" if body_format == "plist" else "apple-health-payload.json",
        caption=(
            "📥 Apple Health: отримано payload "
            f"(формат: {'Health Auto Export' if is_hae else 'native'}, "
            f"тіло: {'Apple plist' if body_format == 'plist' else 'JSON'}, "
            f"{body_len} байт). Отримане тіло запиту — у файлі."
        ),
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
            "AppleHealth OK user_id=%s sync_id=%s received=%d aggregated=%d "
            "aggregate_rows=%d raw_stored=%d failed=%d",
            sync["user_id"],
            sync["sync_id"],
            result["records_received"],
            result["records_aggregated"],
            result["aggregate_rows_updated"],
            result["raw_stored"],
            result["records_failed"],
        )
        await _notify_ingest_summary_to_telegram(telegram_user_id, result)
        return result
    except AppleHealthIngestionError as exc:
        logger.warning(
            "AppleHealth REJECT ingestion user_id=%s sync_id=%s err_class=%s",
            sync["user_id"],
            sync["sync_id"],
            type(exc).__name__,
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
