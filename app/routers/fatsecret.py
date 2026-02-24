from fastapi import APIRouter, Query, HTTPException

from app.services.fatsecret_api import search_food

router = APIRouter()


@router.get("/food/search")
async def food_search(q: str = Query(..., min_length=1)):
    try:
        return await search_food(q)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"FatSecret API error: {e}")
