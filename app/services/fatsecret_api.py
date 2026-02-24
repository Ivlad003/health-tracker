from __future__ import annotations

import httpx
import logging
import math
import time
import secrets as secrets_mod

from app.config import settings

logger = logging.getLogger(__name__)

FATSECRET_TOKEN_URL = "https://oauth.fatsecret.com/connect/token"
FATSECRET_API_URL = "https://platform.fatsecret.com/rest/server.api"


async def get_oauth2_token() -> str:
    """Get FatSecret OAuth 2.0 access token (server-to-server, client_credentials)."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            FATSECRET_TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": settings.fatsecret_client_id,
                "client_secret": settings.fatsecret_client_secret,
                "scope": "basic",
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


async def search_food(query: str, max_results: int = 5) -> dict:
    """Search FatSecret public food database. Returns formatted results."""
    token = await get_oauth2_token()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            FATSECRET_API_URL,
            headers={"Authorization": f"Bearer {token}"},
            data={
                "method": "foods.search",
                "search_expression": query,
                "format": "json",
                "max_results": str(max_results),
            },
        )
        resp.raise_for_status()
        data = resp.json()

    foods = data.get("foods", {}).get("food", [])
    if not isinstance(foods, list):
        foods = [foods]

    results = [
        {
            "name": f.get("food_name", ""),
            "brand": f.get("brand_name", "Generic"),
            "description": f.get("food_description", ""),
            "food_id": f.get("food_id", ""),
        }
        for f in foods
    ]

    return {"query": query, "results_count": len(results), "results": results}


async def get_food_servings(food_id: str) -> list[dict]:
    """Get serving options for a food item. Returns list of servings with serving_id."""
    token = await get_oauth2_token()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            FATSECRET_API_URL,
            headers={"Authorization": f"Bearer {token}"},
            data={
                "method": "food.get.v4",
                "food_id": food_id,
                "format": "json",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    servings = data.get("food", {}).get("servings", {}).get("serving", [])
    if not isinstance(servings, list):
        servings = [servings]

    return [
        {
            "serving_id": s.get("serving_id", ""),
            "description": s.get("serving_description", ""),
            "metric_serving_amount": float(s.get("metric_serving_amount", 0) or 0),
            "metric_serving_unit": s.get("metric_serving_unit", "g"),
            "calories": float(s.get("calories", 0) or 0),
        }
        for s in servings
    ]


def _meal_type_to_fatsecret(meal_type: str) -> str:
    """Convert bot meal_type to FatSecret meal name."""
    return {
        "breakfast": "breakfast",
        "lunch": "lunch",
        "dinner": "dinner",
        "snack": "other",
    }.get(meal_type, "other")


async def create_food_diary_entry(
    access_token: str,
    access_secret: str,
    food_id: str,
    serving_id: str,
    number_of_units: float,
    meal_type: str = "other",
    date: int | None = None,
) -> bool:
    """Add a food entry to user's FatSecret diary via OAuth 1.0."""
    from app.services.fatsecret_auth import sign_oauth1_request

    if date is None:
        date = math.floor(time.time() / 86400)

    api_params = {
        "method": "food_entry.create.v2",
        "format": "json",
        "food_id": str(food_id),
        "serving_id": str(serving_id),
        "number_of_units": str(number_of_units),
        "meal": _meal_type_to_fatsecret(meal_type),
        "date": str(date),
    }

    oauth_params = {
        "oauth_consumer_key": settings.fatsecret_client_id,
        "oauth_token": access_token,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_nonce": secrets_mod.token_hex(16),
        "oauth_version": "1.0",
    }

    all_params = {**oauth_params, **api_params}
    signature = sign_oauth1_request(
        method="POST",
        url=FATSECRET_API_URL,
        params=all_params,
        consumer_secret=settings.fatsecret_shared_secret,
        token_secret=access_secret,
    )
    oauth_params["oauth_signature"] = signature

    all_post_params = {**oauth_params, **api_params}
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            FATSECRET_API_URL,
            data=all_post_params,
        )
        if resp.status_code != 200:
            logger.error(
                "FatSecret create entry failed: status=%s body=%s",
                resp.status_code, resp.text,
            )
            return False

    logger.info("FatSecret diary entry created: food_id=%s serving_id=%s units=%s",
                food_id, serving_id, number_of_units)
    return True


async def fetch_food_diary(
    access_token: str,
    access_secret: str,
    date: int | None = None,
) -> dict:
    """Fetch user's food diary from FatSecret via OAuth 1.0 signed request.

    Args:
        access_token: User's OAuth 1.0 access token.
        access_secret: User's OAuth 1.0 token secret.
        date: Days since epoch (Jan 1, 1970). Defaults to today.
    """
    from app.services.fatsecret_auth import sign_oauth1_request, build_oauth1_header

    if date is None:
        date = math.floor(time.time() / 86400)

    api_params = {
        "method": "food_entries.get.v2",
        "format": "json",
        "date": str(date),
    }

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
    signature = sign_oauth1_request(
        method="POST",
        url=FATSECRET_API_URL,
        params=all_params,
        consumer_secret=settings.fatsecret_shared_secret,
        token_secret=access_secret,
    )
    oauth_params["oauth_signature"] = signature

    all_post_params = {**oauth_params, **api_params}
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            FATSECRET_API_URL,
            data=all_post_params,
        )
        resp.raise_for_status()
        data = resp.json()

    entries = data.get("food_entries", {}).get("food_entry", [])
    if not isinstance(entries, list):
        entries = [entries]

    total_calories = 0.0
    meals = []
    for e in entries:
        cal = float(e.get("calories", 0))
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
    }
