import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone, timedelta


@pytest.mark.asyncio
async def test_refresh_token_if_expired(mock_settings):
    from app.services.whoop_sync import refresh_token_if_needed

    user = {
        "id": 1,
        "whoop_access_token": "old_token",
        "whoop_refresh_token": "refresh_tok",
        "whoop_token_expires_at": datetime.now(timezone.utc) - timedelta(hours=1),
    }

    new_token_resp = MagicMock()
    new_token_resp.json.return_value = {
        "access_token": "new_token",
        "refresh_token": "new_refresh",
        "expires_in": 3600,
    }
    new_token_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=new_token_resp)

    mock_pool = AsyncMock()
    mock_pool.execute = AsyncMock()

    token = await refresh_token_if_needed(user, mock_client, mock_pool)
    assert token == "new_token"
    mock_client.post.assert_called_once()
    mock_pool.execute.assert_called_once()


@pytest.mark.asyncio
async def test_no_refresh_if_not_expired(mock_settings):
    from app.services.whoop_sync import refresh_token_if_needed

    user = {
        "id": 1,
        "whoop_access_token": "valid_token",
        "whoop_refresh_token": "refresh_tok",
        "whoop_token_expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
    }

    mock_client = AsyncMock()
    mock_pool = AsyncMock()

    token = await refresh_token_if_needed(user, mock_client, mock_pool)
    assert token == "valid_token"
    mock_client.post.assert_not_called()


@pytest.mark.asyncio
async def test_process_workouts_empty(mock_settings):
    from app.services.whoop_sync import process_workouts

    result = process_workouts({"records": []}, user_id=1)
    assert result == []


@pytest.mark.asyncio
async def test_process_workouts_with_data(mock_settings):
    from app.services.whoop_sync import process_workouts

    data = {
        "records": [
            {
                "id": 100,
                "sport_name": "Running",
                "score_state": "SCORED",
                "score": {
                    "kilojoule": 1000,
                    "strain": 12.5,
                    "average_heart_rate": 145,
                    "max_heart_rate": 180,
                },
                "start": "2026-02-24T10:00:00Z",
                "end": "2026-02-24T11:00:00Z",
            }
        ]
    }

    result = process_workouts(data, user_id=1)
    assert len(result) == 1
    assert result[0]["whoop_workout_id"] == "100"
    assert result[0]["sport_name"] == "Running"
    assert result[0]["calories"] == pytest.approx(1000 / 4.184, rel=0.01)
