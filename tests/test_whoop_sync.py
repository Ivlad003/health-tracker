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
async def test_refresh_rejected_clears_tokens_and_raises(mock_settings):
    from app.services.whoop_sync import TokenExpiredError, refresh_token_if_needed

    user = {
        "id": 5,
        "whoop_access_token": "old",
        "whoop_refresh_token": "revoked",
        "whoop_token_expires_at": datetime.now(timezone.utc) - timedelta(minutes=1),
    }
    rejected = MagicMock(status_code=400, text="invalid_grant")
    client = AsyncMock()
    client.post = AsyncMock(return_value=rejected)
    pool = AsyncMock()

    with pytest.raises(TokenExpiredError):
        await refresh_token_if_needed(user, client, pool)

    sql = pool.execute.call_args[0][0]
    assert "whoop_access_token = NULL" in sql


@pytest.mark.asyncio
async def test_concurrent_refresh_spends_refresh_token_once(mock_settings):
    import asyncio

    from app.services.whoop_sync import refresh_token_if_needed

    user = {
        "id": 9,
        "whoop_access_token": "old",
        "whoop_refresh_token": "refresh",
        "whoop_token_expires_at": datetime.now(timezone.utc) - timedelta(minutes=1),
    }
    ok = MagicMock(status_code=200)
    ok.json.return_value = {"access_token": "new", "refresh_token": "r2", "expires_in": 3600}

    async def slow_post(*args, **kwargs):
        await asyncio.sleep(0.01)
        return ok

    client = AsyncMock()
    client.post = AsyncMock(side_effect=slow_post)
    pool = AsyncMock()

    tokens = await asyncio.gather(
        refresh_token_if_needed(dict(user), client, pool),
        refresh_token_if_needed(dict(user), client, pool),
    )

    assert tokens == ["new", "new"]
    client.post.assert_called_once()


@pytest.mark.asyncio
async def test_whoop_context_retries_once_after_401_then_clears(mock_settings):
    import httpx

    from app.services import whoop_sync

    user_row = {
        "id": 3,
        "whoop_access_token": "a",
        "whoop_refresh_token": "r",
        "whoop_token_expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=user_row)
    unauthorized = httpx.HTTPStatusError(
        "401", request=MagicMock(), response=MagicMock(status_code=401),
    )

    with (
        patch.object(whoop_sync, "refresh_token_if_needed", AsyncMock(return_value="t")),
        patch.object(whoop_sync, "fetch_whoop_context", AsyncMock(side_effect=unauthorized)) as fetch,
    ):
        with pytest.raises(whoop_sync.TokenExpiredError):
            await whoop_sync.get_whoop_context_for_user(pool, 3)

    assert fetch.await_count == 2
    assert "whoop_access_token = NULL" in pool.execute.call_args[0][0]


@pytest.mark.asyncio
async def test_whoop_context_returns_none_when_not_connected(mock_settings):
    from app.services.whoop_sync import get_whoop_context_for_user

    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)

    assert await get_whoop_context_for_user(pool, 1) is None
