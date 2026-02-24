import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.asyncio
async def test_fetch_food_diary(mock_settings):
    from app.services.fatsecret_api import fetch_food_diary

    diary_response = MagicMock()
    diary_response.json.return_value = {
        "food_entries": {
            "food_entry": [
                {
                    "food_entry_name": "Chicken Breast",
                    "meal": "Lunch",
                    "calories": "165",
                    "protein": "31.02",
                    "fat": "3.60",
                    "carbohydrate": "0.00",
                    "number_of_units": "1.00",
                    "serving_description": "100g",
                }
            ]
        }
    }
    diary_response.raise_for_status = MagicMock()

    with patch("app.services.fatsecret_api.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=diary_response)
        mock_cls.return_value = mock_client

        result = await fetch_food_diary(
            access_token="tok",
            access_secret="sec",
        )

    assert result["entries_count"] == 1
    assert result["meals"][0]["food"] == "Chicken Breast"
    assert result["meals"][0]["calories"] == 165.0
