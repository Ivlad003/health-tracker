import httpx
from fastapi import APIRouter

router = APIRouter()


@router.get("/ip-check")
async def ip_check():
    async with httpx.AsyncClient() as client:
        resp = await client.get("https://api.ipify.org?format=json")
        resp.raise_for_status()
        return resp.json()
