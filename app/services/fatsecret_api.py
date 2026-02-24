import httpx
import logging

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
