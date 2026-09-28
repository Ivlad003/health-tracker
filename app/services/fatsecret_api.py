from __future__ import annotations

import asyncio
import httpx
import logging
import time
import secrets as secrets_mod
from dataclasses import dataclass
from datetime import date as date_cls, datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from app.config import settings
from app.timeutils import resolve_timezone

logger = logging.getLogger(__name__)

FATSECRET_TOKEN_URL = "https://oauth.fatsecret.com/connect/token"
FATSECRET_API_URL = "https://platform.fatsecret.com/rest/server.api"

# FatSecret OAuth 1.0 error codes that mean the token is invalid/expired
_FS_AUTH_ERROR_CODES = {2, 4, 8, 13, 14}  # Invalid key, signature, token, etc.


class FatSecretAPIError(Exception):
    """Raised when FatSecret returns a non-auth error body with HTTP 200."""

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"FatSecret API error {code}: {message}")


class FatSecretAuthError(Exception):
    """Raised when FatSecret returns an auth error (invalid/expired token)."""

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"FatSecret auth error {code}: {message}")


_EPOCH = date_cls(1970, 1, 1)
# Cached OAuth 2.0 client-credentials token for the "basic" scope:
# (access_token, monotonic expiry). Other scopes live in _oauth2_scoped_cache.
_oauth2_token_cache: Optional[tuple[str, float]] = None
_oauth2_scoped_cache: dict[str, tuple[str, float]] = {}
_oauth2_token_lock = asyncio.Lock()


def _http_timeout() -> httpx.Timeout:
    return httpx.Timeout(settings.http_timeout_seconds)


def fatsecret_today(tz: Optional[ZoneInfo] = None) -> int:
    """FatSecret `date` (days since epoch) for the user's LOCAL today.

    Using UTC here made the diary empty for Kyiv users between 00:00 and 03:00.
    """
    local_today = datetime.now(tz or resolve_timezone(None)).date()
    return (local_today - _EPOCH).days


def fatsecret_date(local_date: date_cls) -> int:
    """FatSecret `date` integer for a user-local calendar date."""
    return (local_date - _EPOCH).days


def date_from_fatsecret(value: Any) -> Optional[date_cls]:
    try:
        from datetime import timedelta

        return _EPOCH + timedelta(days=int(value))
    except (TypeError, ValueError):
        return None


async def clear_fatsecret_tokens(pool: Any, user_id: int) -> None:
    """Forget FatSecret credentials for a user (forces /connect_fatsecret)."""
    await pool.execute(
        """UPDATE users
           SET fatsecret_access_token = NULL,
               fatsecret_access_secret = NULL,
               updated_at = NOW()
           WHERE id = $1""",
        user_id,
    )


def _raise_on_error_body(data: Any, context: str) -> None:
    """FatSecret returns HTTP 200 with an error body — surface it."""
    if not isinstance(data, dict) or "error" not in data:
        return
    err = data["error"] or {}
    try:
        code = int(err.get("code", 0))
    except (TypeError, ValueError):
        code = 0
    msg = err.get("message", "Unknown error")
    logger.error("FatSecret %s error: code=%s message=%s", context, code, msg)
    if code in _FS_AUTH_ERROR_CODES:
        raise FatSecretAuthError(code, msg)
    raise FatSecretAPIError(code, msg)


def _as_list(value: Any) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


async def get_oauth2_token(scope: str = "basic") -> str:
    """Get FatSecret OAuth 2.0 access token (server-to-server, client_credentials).

    Tokens live for 24h; cache them per scope instead of requesting one per
    call. Adding a scope (e.g. ``barcode``) does not grant the entitlement:
    FatSecret rejects the token request when the account lacks it.
    """
    global _oauth2_token_cache
    cached = _oauth2_token_cache if scope == "basic" else _oauth2_scoped_cache.get(scope)
    if cached and cached[1] > time.monotonic():
        return cached[0]
    async with _oauth2_token_lock:
        cached = _oauth2_token_cache if scope == "basic" else _oauth2_scoped_cache.get(scope)
        if cached and cached[1] > time.monotonic():
            return cached[0]
        token, expires_in = await _request_oauth2_token(scope)
        entry = (token, time.monotonic() + max(expires_in - 300, 60))
        if scope == "basic":
            _oauth2_token_cache = entry
        else:
            _oauth2_scoped_cache[scope] = entry
        return token


async def _request_oauth2_token(scope: str = "basic") -> tuple[str, int]:
    async with httpx.AsyncClient(timeout=_http_timeout()) as client:
        resp = await client.post(
            FATSECRET_TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": settings.fatsecret_client_id,
                "client_secret": settings.fatsecret_client_secret,
                "scope": scope,
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        return payload["access_token"], int(payload.get("expires_in") or 86400)


def food_locales(language: Optional[str]) -> list[tuple[Optional[str], Optional[str]]]:
    """Where to look. The mobile app for a Ukrainian account searches Ukraine, not the US set."""
    if language == "uk":
        return [("UA", "uk"), ("UA", "ru"), (None, None)]
    if language == "ru":
        return [("UA", "ru"), ("UA", "uk"), (None, None)]
    return [(None, None)]


def _food_results(data: dict) -> list[dict]:
    foods = _as_list((data.get("foods") or {}).get("food", []))
    return [
        {
            "name": f.get("food_name", ""),
            "brand": f.get("brand_name", "Generic"),
            "description": f.get("food_description", ""),
            "food_id": f.get("food_id", ""),
            "food_type": f.get("food_type", ""),
        }
        for f in foods
    ]


async def _search_food_once(
    query: str, max_results: int, region: Optional[str], language: Optional[str],
) -> list[dict]:
    scope = "basic localization" if region else "basic"
    try:
        token = await get_oauth2_token(scope)
    except Exception:
        if region:
            return []
        raise
    payload = {
        "method": "foods.search",
        "search_expression": query,
        "format": "json",
        "max_results": str(max_results),
    }
    if region:
        payload["region"] = region
        if language:
            payload["language"] = language
    async with httpx.AsyncClient(timeout=_http_timeout()) as client:
        resp = await client.post(
            FATSECRET_API_URL,
            headers={"Authorization": f"Bearer {token}"},
            data=payload,
        )
        resp.raise_for_status()
        data = resp.json()
    if isinstance(data, dict) and "error" in data:
        try:
            code = int((data["error"] or {}).get("code", 0))
        except (TypeError, ValueError):
            code = 0
        if region and code in (14, 208):
            return []
        _raise_on_error_body(data, "search")
    return _food_results(data if isinstance(data, dict) else {})


async def search_food(query: str, max_results: int = 8, language: Optional[str] = None) -> dict:
    """Search FatSecret. Ukrainian and Russian try Ukraine before the US catalogue."""
    logger.info("FatSecret search: query='%s' max=%d lang=%s", query, max_results, language)
    results: list[dict] = []
    for region, lang in food_locales(language):
        try:
            results = await _search_food_once(query, max_results, region, lang)
        except Exception:
            logger.warning(
                "FatSecret search failed region=%s language=%s", region, lang, exc_info=True,
            )
            continue
        if results:
            break
    logger.info("FatSecret search result: query='%s' found=%d", query, len(results))
    return {"query": query, "results_count": len(results), "results": results}


def _parse_serving(s: dict) -> dict:
    """Structured serving, values kept as provider strings (Decimal-parsed later)."""
    return {
        "serving_id": str(s.get("serving_id", "") or ""),
        "description": s.get("serving_description", ""),
        "measurement_description": s.get("measurement_description", ""),
        "metric_serving_amount": s.get("metric_serving_amount"),
        "metric_serving_unit": s.get("metric_serving_unit"),
        "number_of_units": s.get("number_of_units"),
        "calories": s.get("calories"),
        "protein": s.get("protein"),
        "fat": s.get("fat"),
        "carbohydrate": s.get("carbohydrate"),
        "fiber": s.get("fiber"),
        "sugar": s.get("sugar"),
        "is_default": str(s.get("is_default", "0")) == "1",
    }


def _parse_food(food: dict) -> dict:
    return {
        "food_id": str(food.get("food_id", "") or ""),
        "name": food.get("food_name", ""),
        "brand": food.get("brand_name") or None,
        "food_type": food.get("food_type", ""),
        "servings": [
            _parse_serving(s) for s in _as_list((food.get("servings") or {}).get("serving"))
        ],
    }


def _locale_params(region: Optional[str], language: Optional[str]) -> dict:
    params: dict = {}
    if region:
        params["region"] = region
        if language:
            params["language"] = language
    return params


async def get_food_details(
    food_id: str,
    *,
    access_token: Optional[str] = None,
    access_secret: Optional[str] = None,
    region: Optional[str] = None,
    language: Optional[str] = None,
) -> dict:
    """``food.get.v4`` → food identity + structured servings (with IDs).

    Diary foods from the mobile app often exist only in the user's region.
    A US ``food.get`` then answers 106. Pass ``region`` (for example ``UA``).
    """
    logger.info("FatSecret food.get: food_id=%s region=%s", food_id, region)
    locale = _locale_params(region, language)
    if access_token and access_secret:
        data = await _user_call(
            access_token,
            access_secret,
            {"method": "food.get.v4", "food_id": str(food_id), "format": "json", **locale},
            "food.get",
        )
        return _parse_food(data.get("food") or {})
    scope = "basic localization" if region else "basic"
    try:
        token = await get_oauth2_token(scope)
    except Exception:
        if not region:
            raise
        raise FatSecretAPIError(14, "localization scope unavailable")
    async with httpx.AsyncClient(timeout=_http_timeout()) as client:
        resp = await client.post(
            FATSECRET_API_URL,
            headers={"Authorization": f"Bearer {token}"},
            data={"method": "food.get.v4", "food_id": food_id, "format": "json", **locale},
        )
        resp.raise_for_status()
        data = resp.json()
    _raise_on_error_body(data, "food.get")
    return _parse_food(data.get("food") or {})


async def get_food_servings(food_id: str) -> list[dict]:
    """Get serving options for a food item. Returns list of servings with serving_id."""
    details = await get_food_details(food_id)

    def _f(value: Any, default: float) -> float:
        try:
            return float(value) if value not in (None, "") else default
        except (TypeError, ValueError):
            return default

    return [
        {
            "serving_id": s["serving_id"],
            "description": s["description"],
            "metric_serving_amount": _f(s["metric_serving_amount"], 0.0),
            "metric_serving_unit": s["metric_serving_unit"] or "g",
            "number_of_units": _f(s["number_of_units"], 1.0),
            "calories": _f(s["calories"], 0.0),
        }
        for s in details["servings"]
    ]


async def find_food_by_barcode(gtin13: str) -> Optional[dict]:
    """``food.find_id_for_barcode.v2`` (Premier, OAuth2 scope ``barcode``).

    Returns the parsed food or ``None`` when not found. Callers must check
    ``settings.fatsecret_barcode_enabled`` first: the scope is an account
    entitlement, not something a code change can enable.
    """
    token = await get_oauth2_token("basic barcode")
    async with httpx.AsyncClient(timeout=_http_timeout()) as client:
        resp = await client.post(
            FATSECRET_API_URL,
            headers={"Authorization": f"Bearer {token}"},
            data={"method": "food.find_id_for_barcode.v2", "barcode": gtin13, "format": "json"},
        )
        resp.raise_for_status()
        data = resp.json()
    try:
        _raise_on_error_body(data, "barcode")
    except FatSecretAPIError:
        return None
    if isinstance(data.get("food"), dict):
        return _parse_food(data["food"])
    food_id = (data.get("food_id") or {}).get("value")
    if not food_id or str(food_id) == "0":
        return None
    return await get_food_details(str(food_id))


def _meal_type_to_fatsecret(meal_type: str) -> str:
    """Convert bot meal_type to FatSecret meal name."""
    return {
        "breakfast": "breakfast",
        "lunch": "lunch",
        "dinner": "dinner",
        "snack": "other",
    }.get(meal_type, "other")


def meal_type_from_fatsecret(meal: str) -> str:
    return {"breakfast": "breakfast", "lunch": "lunch", "dinner": "dinner"}.get(
        str(meal or "").strip().lower(), "snack",
    )


def _signed_params(access_token: str, access_secret: str, api_params: dict) -> dict:
    from app.services.fatsecret_auth import sign_oauth1_request

    oauth_params = {
        "oauth_consumer_key": settings.fatsecret_client_id,
        "oauth_token": access_token,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_nonce": secrets_mod.token_hex(16),
        "oauth_version": "1.0",
    }
    # Signature is computed over all params (OAuth + API)
    all_params = {**oauth_params, **api_params}
    oauth_params["oauth_signature"] = sign_oauth1_request(
        method="POST",
        url=FATSECRET_API_URL,
        params=all_params,
        consumer_secret=settings.fatsecret_shared_secret,
        token_secret=access_secret,
    )
    return {**oauth_params, **api_params}


async def _user_call(access_token: str, access_secret: str, api_params: dict, context: str) -> dict:
    """Signed OAuth 1.0 read call. Raises on HTTP/auth/API errors."""
    async with httpx.AsyncClient(timeout=_http_timeout()) as client:
        resp = await client.post(
            FATSECRET_API_URL, data=_signed_params(access_token, access_secret, api_params),
        )
        resp.raise_for_status()
        data = resp.json()
    _raise_on_error_body(data, context)
    return data if isinstance(data, dict) else {}


# --------------------------------------------------------------------------
# Typed diary writes
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class FoodEntryWriteResult:
    """Outcome of a remote diary write.

    ``succeeded`` requires an acknowledged remote id (create) or an explicit
    success flag (edit/delete). ``unknown`` means the request may have been
    applied (timeout after dispatch, 5xx, malformed success body): never
    retry it blindly, reconcile first. ``failed`` means it was definitely
    not applied.
    """

    status: str  # "succeeded" | "failed" | "unknown"
    remote_entry_id: Optional[str] = None
    error: Optional[str] = None
    auth_error: bool = False

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"


async def _user_write(
    access_token: str, access_secret: str, api_params: dict, context: str,
) -> tuple[Optional[dict], Optional[FoodEntryWriteResult]]:
    """POST a write; returns (json_body, None) or (None, non-success result)."""
    try:
        async with httpx.AsyncClient(timeout=_http_timeout()) as client:
            resp = await client.post(
                FATSECRET_API_URL, data=_signed_params(access_token, access_secret, api_params),
            )
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
        # The request never reached FatSecret.
        return None, FoodEntryWriteResult("failed", error=f"connect:{exc.__class__.__name__}")
    except httpx.HTTPError as exc:
        # Sent (fully or partially) but no reliable answer.
        return None, FoodEntryWriteResult("unknown", error=f"transport:{exc.__class__.__name__}")

    if resp.status_code >= 500:
        logger.error("FatSecret %s: HTTP %s", context, resp.status_code)
        return None, FoodEntryWriteResult("unknown", error=f"http_{resp.status_code}")
    if resp.status_code != 200:
        logger.error(
            "FatSecret %s failed: status=%s body=%s", context, resp.status_code, resp.text[:200],
        )
        return None, FoodEntryWriteResult(
            "failed", error=f"http_{resp.status_code}", auth_error=resp.status_code in (401, 403),
        )
    try:
        data = resp.json()
    except ValueError:
        logger.error("FatSecret %s returned non-JSON body: %s", context, resp.text[:200])
        return None, FoodEntryWriteResult("unknown", error="non_json")
    try:
        _raise_on_error_body(data, context)
    except FatSecretAuthError as exc:
        return None, FoodEntryWriteResult("failed", error=f"auth_{exc.code}", auth_error=True)
    except FatSecretAPIError as exc:
        return None, FoodEntryWriteResult("failed", error=f"api_{exc.code}")
    if not isinstance(data, dict):
        return None, FoodEntryWriteResult("unknown", error="malformed")
    return data, None


async def create_food_entry(
    access_token: str,
    access_secret: str,
    food_id: str,
    food_entry_name: str,
    serving_id: str,
    number_of_units: Any,
    meal_type: str = "other",
    date: int | None = None,
    tz: Optional[ZoneInfo] = None,
) -> FoodEntryWriteResult:
    """``food_entry.create.v2`` with a typed result carrying the remote id."""
    if str(serving_id) in ("", "0"):
        # Derived servings cannot be written; never let one become writable.
        return FoodEntryWriteResult("failed", error="derived_serving")
    if date is None:
        date = fatsecret_today(tz)
    data, failure = await _user_write(
        access_token,
        access_secret,
        {
            "method": "food_entry.create.v2",
            "format": "json",
            "food_id": str(food_id),
            "food_entry_name": food_entry_name,
            "serving_id": str(serving_id),
            "number_of_units": str(number_of_units),
            "meal": _meal_type_to_fatsecret(meal_type),
            "date": str(date),
        },
        "create entry",
    )
    if failure:
        return failure
    remote_id = ((data or {}).get("food_entry_id") or {})
    remote_id = remote_id.get("value") if isinstance(remote_id, dict) else remote_id
    if not remote_id or not str(remote_id).isdigit() or str(remote_id) == "0":
        logger.error("FatSecret create entry: success body without food_entry_id")
        return FoodEntryWriteResult("unknown", error="missing_entry_id")
    logger.info(
        "FatSecret diary entry created: food_id=%s serving_id=%s units=%s meal=%s",
        food_id, serving_id, number_of_units, _meal_type_to_fatsecret(meal_type),
    )
    return FoodEntryWriteResult("succeeded", remote_entry_id=str(remote_id))


async def create_food_diary_entry(
    access_token: str,
    access_secret: str,
    food_id: str,
    food_entry_name: str,
    serving_id: str,
    number_of_units: float,
    meal_type: str = "other",
    date: int | None = None,
    tz: Optional[ZoneInfo] = None,
) -> bool:
    """Backward-compatible boolean wrapper around :func:`create_food_entry`."""
    result = await create_food_entry(
        access_token, access_secret, food_id, food_entry_name, serving_id,
        number_of_units, meal_type=meal_type, date=date, tz=tz,
    )
    return result.ok


def _success_flag(data: Optional[dict]) -> bool:
    success = (data or {}).get("success")
    value = success.get("value") if isinstance(success, dict) else success
    return str(value) == "1"


async def edit_food_entry(
    access_token: str,
    access_secret: str,
    remote_entry_id: str,
    *,
    serving_id: Optional[str] = None,
    number_of_units: Any = None,
    meal_type: Optional[str] = None,
    entry_name: Optional[str] = None,
) -> FoodEntryWriteResult:
    params = {"method": "food_entry.edit.v2", "format": "json", "food_entry_id": str(remote_entry_id)}
    if serving_id:
        params["serving_id"] = str(serving_id)
    if number_of_units is not None:
        params["number_of_units"] = str(number_of_units)
    if meal_type:
        params["meal"] = _meal_type_to_fatsecret(meal_type)
    if entry_name:
        params["entry_name"] = entry_name
    data, failure = await _user_write(access_token, access_secret, params, "edit entry")
    if failure:
        return failure
    if not _success_flag(data):
        return FoodEntryWriteResult("unknown", error="malformed")
    return FoodEntryWriteResult("succeeded", remote_entry_id=str(remote_entry_id))


async def delete_food_entry(
    access_token: str, access_secret: str, remote_entry_id: str,
) -> FoodEntryWriteResult:
    data, failure = await _user_write(
        access_token,
        access_secret,
        {"method": "food_entry.delete.v2", "format": "json", "food_entry_id": str(remote_entry_id)},
        "delete entry",
    )
    if failure:
        return failure
    if not _success_flag(data):
        return FoodEntryWriteResult("unknown", error="malformed")
    return FoodEntryWriteResult("succeeded", remote_entry_id=str(remote_entry_id))


# --------------------------------------------------------------------------
# Structured diary / history reads
# --------------------------------------------------------------------------

def _parse_entry(e: dict) -> dict:
    return {
        "food_entry_id": str(e.get("food_entry_id", "") or ""),
        "food_id": str(e.get("food_id", "") or ""),
        "serving_id": str(e.get("serving_id", "") or ""),
        "number_of_units": e.get("number_of_units"),
        "name": e.get("food_entry_name", ""),
        "description": e.get("food_entry_description", ""),
        "meal": e.get("meal", ""),
        "date_int": e.get("date_int"),
        "calories": e.get("calories"),
        "protein": e.get("protein"),
        "fat": e.get("fat"),
        "carbohydrate": e.get("carbohydrate"),
    }


async def fetch_food_entries(
    access_token: str, access_secret: str, date: int,
) -> list[dict]:
    """``food_entries.get.v2`` for one day, preserving entry/food/serving IDs."""
    data = await _user_call(
        access_token,
        access_secret,
        {"method": "food_entries.get.v2", "format": "json", "date": str(date)},
        "diary",
    )
    return [_parse_entry(e) for e in _as_list((data.get("food_entries") or {}).get("food_entry"))]


async def fetch_food_diary(
    access_token: str,
    access_secret: str,
    date: int | None = None,
    tz: Optional[ZoneInfo] = None,
) -> dict:
    """Fetch user's food diary from FatSecret via OAuth 1.0 signed request.

    Args:
        access_token: User's OAuth 1.0 access token.
        access_secret: User's OAuth 1.0 token secret.
        date: Days since epoch (Jan 1, 1970). Defaults to the user's local today.
        tz: User timezone used to compute "today".
    """
    if date is None:
        date = fatsecret_today(tz)

    data = await _user_call(
        access_token,
        access_secret,
        {"method": "food_entries.get.v2", "format": "json", "date": str(date)},
        "diary",
    )
    raw_entries = _as_list((data.get("food_entries") or {}).get("food_entry", []))

    logger.info("FatSecret diary fetched: %d entries for date=%s", len(raw_entries), date)

    total_calories = 0.0
    meals = []
    for e in raw_entries:
        cal = float(e.get("calories", 0) or 0)
        total_calories += cal
        meals.append({
            "food": e.get("food_entry_name", ""),
            "meal": e.get("meal", ""),
            "calories": cal,
            "protein": e.get("protein", "0"),
            "fat": e.get("fat", "0"),
            "carbs": e.get("carbohydrate", "0"),
            "serving": f"{e.get('number_of_units', '')} {e.get('serving_description', '')}".strip(),
        })

    return {
        "date": date,
        "total_calories": round(total_calories),
        "entries_count": len(meals),
        "meals": meals,
        "entries": [_parse_entry(e) for e in raw_entries],
    }


def _parse_history_food(f: dict) -> dict:
    return {
        "food_id": str(f.get("food_id", "") or ""),
        "name": f.get("food_name", ""),
        "brand": f.get("brand_name") or None,
        "serving_id": str(f.get("serving_id", "") or ""),
        "number_of_units": f.get("number_of_units"),
    }


async def _history_list(access_token: str, access_secret: str, method: str) -> list[dict]:
    data = await _user_call(access_token, access_secret, {"method": method, "format": "json"}, method)
    return [
        _parse_history_food(f)
        for f in _as_list((data.get("foods") or {}).get("food"))
        if f.get("food_id")
    ]


async def get_recently_eaten(access_token: str, access_secret: str) -> list[dict]:
    return await _history_list(access_token, access_secret, "foods.get_recently_eaten.v2")


async def get_most_eaten(access_token: str, access_secret: str) -> list[dict]:
    return await _history_list(access_token, access_secret, "foods.get_most_eaten.v2")


async def get_favorite_foods(access_token: str, access_secret: str) -> list[dict]:
    return await _history_list(access_token, access_secret, "foods.get_favorites.v2")


async def check_fatsecret_tokens() -> None:
    """Health check: verify FatSecret tokens are still valid every 30 min."""
    from app.database import get_pool

    logger.info("Starting FatSecret token check")

    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT id, telegram_user_id, fatsecret_access_token, fatsecret_access_secret,
                  language
           FROM users
           WHERE fatsecret_access_token IS NOT NULL
                 AND fatsecret_access_token != ''
                 AND fatsecret_access_secret IS NOT NULL
                 AND fatsecret_access_secret != ''"""
    )

    if not rows:
        return

    valid = 0
    for row in rows:
        try:
            await fetch_food_diary(
                access_token=row["fatsecret_access_token"],
                access_secret=row["fatsecret_access_secret"],
            )
            valid += 1
        except (httpx.HTTPStatusError, FatSecretAuthError) as e:
            is_auth = (
                isinstance(e, FatSecretAuthError)
                or (isinstance(e, httpx.HTTPStatusError) and e.response.status_code in (401, 403))
            )
            if is_auth:
                logger.warning("FatSecret token invalid for user_id=%s, clearing", row["id"])
                await clear_fatsecret_tokens(pool, row["id"])
                try:
                    from app.i18n import t
                    from app.services.telegram_bot import send_message

                    await send_message(
                        row["telegram_user_id"], t("fatsecret_expired", row.get("language")),
                    )
                except Exception:
                    logger.warning("Failed to notify user_id=%s about FatSecret expiry", row["id"])
            else:
                logger.warning("FatSecret check failed for user_id=%s: %s", row["id"], e)
        except Exception:
            logger.warning("FatSecret check failed for user_id=%s", row["id"], exc_info=True)

    logger.info("FatSecret token check complete: %d/%d valid", valid, len(rows))
