"""Security helpers: signed OAuth state and admin-only endpoint guard."""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Optional

from fastapi import Header, HTTPException

from app.config import settings

# OAuth state is valid for 1 hour — long enough to complete a consent screen
# after opening the bot link, short enough to limit replay of a leaked link.
OAUTH_STATE_TTL_SECONDS = 60 * 60


class InvalidStateError(ValueError):
    """Raised when an OAuth state value is malformed, forged, or expired."""


def _state_key() -> bytes:
    if settings.oauth_state_secret:
        return settings.oauth_state_secret.encode()
    # Derived fallback: stable across restarts and never exposed.
    material = (
        f"oauth-state:{settings.whoop_client_secret}:"
        f"{settings.fatsecret_client_secret}:{settings.telegram_bot_token}"
    )
    return hashlib.sha256(material.encode()).digest()


def _sign(purpose: str, payload: str) -> str:
    mac = hmac.new(_state_key(), f"{purpose}|{payload}".encode(), hashlib.sha256)
    return base64.urlsafe_b64encode(mac.digest()[:18]).decode().rstrip("=")


def sign_oauth_state(telegram_user_id: int, purpose: str, *, now: Optional[int] = None) -> str:
    """Return `<telegram_id>.<issued_at>.<mac>` bound to `purpose`."""
    issued_at = int(now if now is not None else time.time())
    payload = f"{int(telegram_user_id)}.{issued_at}"
    return f"{payload}.{_sign(purpose, payload)}"


def verify_oauth_state(state: str, purpose: str, *, now: Optional[int] = None) -> int:
    """Validate a signed state and return the Telegram user id."""
    if not state or state.count(".") != 2:
        raise InvalidStateError("malformed state")
    tg_raw, issued_raw, mac = state.split(".")
    try:
        telegram_user_id = int(tg_raw)
        issued_at = int(issued_raw)
    except ValueError as exc:
        raise InvalidStateError("malformed state") from exc
    expected = _sign(purpose, f"{tg_raw}.{issued_raw}")
    if not hmac.compare_digest(expected, mac):
        raise InvalidStateError("bad signature")
    current = int(now if now is not None else time.time())
    if issued_at > current + 60 or current - issued_at > OAUTH_STATE_TTL_SECONDS:
        raise InvalidStateError("state expired")
    return telegram_user_id


async def require_admin(
    authorization: Optional[str] = Header(default=None),
    x_admin_token: Optional[str] = Header(default=None),
) -> None:
    """FastAPI dependency: allow only requests with the admin token.

    Debug endpoints are hidden (404) when ADMIN_API_TOKEN is not configured so
    that a misconfigured deployment never exposes user data.
    """
    expected = settings.admin_api_token
    if not expected:
        raise HTTPException(status_code=404, detail="Not Found")
    supplied = x_admin_token
    if not supplied and authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")
