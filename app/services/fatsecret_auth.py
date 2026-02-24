from __future__ import annotations

import hashlib
import hmac
import base64
import time
import secrets
import logging
from urllib.parse import quote

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

FATSECRET_REQUEST_TOKEN_URL = "https://authentication.fatsecret.com/oauth/request_token"
FATSECRET_AUTHORIZE_URL = "https://authentication.fatsecret.com/oauth/authorize"
FATSECRET_ACCESS_TOKEN_URL = "https://authentication.fatsecret.com/oauth/access_token"


def percent_encode(s: str) -> str:
    """RFC 5849 percent encoding."""
    return quote(str(s), safe="")


def sign_oauth1_request(
    method: str,
    url: str,
    params: dict,
    consumer_secret: str,
    token_secret: str = "",
) -> str:
    """Generate HMAC-SHA1 signature for OAuth 1.0 request."""
    sorted_params = sorted(params.items())
    param_string = "&".join(f"{percent_encode(k)}={percent_encode(v)}" for k, v in sorted_params)
    base_string = f"{method.upper()}&{percent_encode(url)}&{percent_encode(param_string)}"
    signing_key = f"{percent_encode(consumer_secret)}&{percent_encode(token_secret)}"

    hashed = hmac.new(
        signing_key.encode("utf-8"),
        base_string.encode("utf-8"),
        hashlib.sha1,
    )
    return base64.b64encode(hashed.digest()).decode("utf-8")


def build_oauth1_header(params: dict) -> str:
    """Build OAuth Authorization header string."""
    parts = ", ".join(
        f'{percent_encode(k)}="{percent_encode(v)}"'
        for k, v in sorted(params.items())
    )
    return f"OAuth {parts}"


async def get_request_token(callback_url: str) -> dict:
    """Step 1 of OAuth 1.0: Get request token from FatSecret."""
    params = {
        "oauth_consumer_key": settings.fatsecret_client_id,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_nonce": secrets.token_hex(16),
        "oauth_version": "1.0",
        "oauth_callback": callback_url,
    }

    signature = sign_oauth1_request(
        method="POST",
        url=FATSECRET_REQUEST_TOKEN_URL,
        params=params,
        consumer_secret=settings.fatsecret_shared_secret,
    )
    params["oauth_signature"] = signature

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            FATSECRET_REQUEST_TOKEN_URL,
            headers={"Authorization": build_oauth1_header(params)},
        )
        resp.raise_for_status()

    # Parse form-encoded response: oauth_token=X&oauth_token_secret=Y&oauth_callback_confirmed=true
    parsed = dict(pair.split("=", 1) for pair in resp.text.split("&"))
    return {
        "oauth_token": parsed.get("oauth_token", ""),
        "oauth_token_secret": parsed.get("oauth_token_secret", ""),
    }


async def exchange_access_token(
    oauth_token: str,
    oauth_verifier: str,
    token_secret: str,
) -> dict:
    """Step 3 of OAuth 1.0: Exchange request token for access token."""
    params = {
        "oauth_consumer_key": settings.fatsecret_client_id,
        "oauth_token": oauth_token,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_nonce": secrets.token_hex(16),
        "oauth_version": "1.0",
        "oauth_verifier": oauth_verifier,
    }

    signature = sign_oauth1_request(
        method="POST",
        url=FATSECRET_ACCESS_TOKEN_URL,
        params=params,
        consumer_secret=settings.fatsecret_shared_secret,
        token_secret=token_secret,
    )
    params["oauth_signature"] = signature

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            FATSECRET_ACCESS_TOKEN_URL,
            headers={"Authorization": build_oauth1_header(params)},
        )
        resp.raise_for_status()

    parsed = dict(pair.split("=", 1) for pair in resp.text.split("&"))
    return {
        "access_token": parsed.get("oauth_token", ""),
        "access_secret": parsed.get("oauth_token_secret", ""),
    }
