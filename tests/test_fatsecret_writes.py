"""Typed FatSecret diary writes and structured reads (plan §7, AC-09)."""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


def _client(post):
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = post
    return client


def _response(status=200, body=None, text=None):
    resp = MagicMock()
    resp.status_code = status
    if body is None and text is not None:
        resp.json.side_effect = ValueError("not json")
    else:
        resp.json.return_value = body
    resp.text = text or ""
    return resp


async def _create(post):
    from app.services.fatsecret_api import create_food_entry

    with patch("app.services.fatsecret_api.httpx.AsyncClient", return_value=_client(post)):
        return await create_food_entry("tok", "sec", "123", "Egg", "5", "100", meal_type="lunch", date=20000)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("post", "status", "remote_id"),
    [
        (AsyncMock(return_value=_response(body={"food_entry_id": {"value": "987"}})), "succeeded", "987"),
        (AsyncMock(return_value=_response(body={"error": {"code": 106, "message": "Invalid ID"}})), "failed", None),
        (AsyncMock(return_value=_response(body={"success": {"value": "1"}})), "unknown", None),
        (AsyncMock(return_value=_response(body={"food_entry_id": {"value": "0"}})), "unknown", None),
        (AsyncMock(return_value=_response(text="<html>")), "unknown", None),
        (AsyncMock(return_value=_response(status=502, body={})), "unknown", None),
        (AsyncMock(return_value=_response(status=400, body={})), "failed", None),
        (AsyncMock(side_effect=httpx.ReadTimeout("slow")), "unknown", None),
        (AsyncMock(side_effect=httpx.ConnectError("down")), "failed", None),
    ],
)
async def test_create_food_entry_classification(mock_settings, post, status, remote_id):
    result = await _create(post)
    assert result.status == status
    assert result.remote_entry_id == remote_id


@pytest.mark.asyncio
async def test_auth_error_is_flagged(mock_settings):
    result = await _create(AsyncMock(return_value=_response(body={"error": {"code": 8, "message": "bad token"}})))
    assert result.status == "failed" and result.auth_error


@pytest.mark.asyncio
async def test_derived_serving_is_never_written(mock_settings):
    from app.services.fatsecret_api import create_food_entry

    post = AsyncMock()
    with patch("app.services.fatsecret_api.httpx.AsyncClient", return_value=_client(post)):
        result = await create_food_entry("t", "s", "1", "x", "0", "1")
    assert result.status == "failed" and result.error == "derived_serving"
    post.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_boolean_wrapper(mock_settings):
    from app.services.fatsecret_api import create_food_diary_entry

    post = AsyncMock(return_value=_response(body={"food_entry_id": {"value": "5"}}))
    with patch("app.services.fatsecret_api.httpx.AsyncClient", return_value=_client(post)):
        assert await create_food_diary_entry("t", "s", "1", "x", "2", 1) is True
    post = AsyncMock(return_value=_response(body={"error": {"code": 106, "message": "x"}}))
    with patch("app.services.fatsecret_api.httpx.AsyncClient", return_value=_client(post)):
        assert await create_food_diary_entry("t", "s", "1", "x", "2", 1) is False


@pytest.mark.asyncio
async def test_edit_and_delete_need_explicit_success(mock_settings):
    from app.services.fatsecret_api import delete_food_entry, edit_food_entry

    ok = AsyncMock(return_value=_response(body={"success": {"value": "1"}}))
    weird = AsyncMock(return_value=_response(body={}))
    with patch("app.services.fatsecret_api.httpx.AsyncClient", return_value=_client(ok)):
        assert (await delete_food_entry("t", "s", "9")).ok
        assert (await edit_food_entry("t", "s", "9", number_of_units="150")).ok
    with patch("app.services.fatsecret_api.httpx.AsyncClient", return_value=_client(weird)):
        assert (await delete_food_entry("t", "s", "9")).status == "unknown"


@pytest.mark.asyncio
async def test_structured_diary_keeps_ids(mock_settings):
    from app.services.fatsecret_api import fetch_food_diary, fetch_food_entries

    body = {"food_entries": {"food_entry": {
        "food_entry_id": "77", "food_id": "33691", "serving_id": "34321", "number_of_units": "180.000",
        "food_entry_name": "Гречка варена", "meal": "Lunch", "calories": "198", "protein": "7",
        "fat": "1", "carbohydrate": "40", "date_int": "20000",
    }}}
    post = AsyncMock(return_value=MagicMock(json=MagicMock(return_value=body), raise_for_status=MagicMock()))
    with patch("app.services.fatsecret_api.httpx.AsyncClient", return_value=_client(post)):
        entries = await fetch_food_entries("t", "s", 20000)
        diary = await fetch_food_diary("t", "s", date=20000)
    assert entries[0]["food_entry_id"] == "77"
    assert entries[0]["serving_id"] == "34321"
    assert diary["entries"][0]["food_id"] == "33691"
    assert diary["total_calories"] == 198


def test_local_date_to_fatsecret_integer():
    from datetime import date

    from app.services.fatsecret_api import date_from_fatsecret, fatsecret_date

    assert fatsecret_date(date(2026, 9, 26)) == 20722
    assert date_from_fatsecret(20722) == date(2026, 9, 26)
