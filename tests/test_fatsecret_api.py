import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.asyncio
async def test_get_oauth2_token(mock_settings):
    from app.services.fatsecret_api import get_oauth2_token

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"access_token": "test_token", "expires_in": 86400}
    mock_response.raise_for_status = MagicMock()

    with patch("app.services.fatsecret_api.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_client

        token = await get_oauth2_token()

    assert token == "test_token"


@pytest.mark.asyncio
async def test_search_food(mock_settings):
    from app.services.fatsecret_api import search_food

    token_response = MagicMock()
    token_response.json.return_value = {"access_token": "tok", "expires_in": 86400}
    token_response.raise_for_status = MagicMock()

    search_response = MagicMock()
    search_response.json.return_value = {
        "foods": {
            "food": [
                {
                    "food_id": "123",
                    "food_name": "Chicken Breast",
                    "brand_name": "Generic",
                    "food_description": "Per 100g - Calories: 165kcal | Fat: 3.60g | Carbs: 0.00g | Protein: 31.02g",
                }
            ]
        }
    }
    search_response.raise_for_status = MagicMock()

    with patch("app.services.fatsecret_api.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=[token_response, search_response])
        mock_cls.return_value = mock_client

        result = await search_food("chicken breast")

    assert result["results_count"] == 1
    assert result["results"][0]["food_id"] == "123"
    assert result["results"][0]["name"] == "Chicken Breast"
