import time
from datetime import datetime, time as dtime, timezone
from zoneinfo import ZoneInfo

import pytest


def test_oauth_state_roundtrip(mock_settings):
    from app.security import sign_oauth_state, verify_oauth_state

    state = sign_oauth_state(123456789, "whoop")
    assert verify_oauth_state(state, "whoop") == 123456789


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s.replace("123", "124", 1),  # different user id
        lambda s: s + "x",
        lambda s: "",
        lambda s: "123456789",
        lambda s: "a.b.c",
    ],
)
def test_oauth_state_rejects_tampering(mock_settings, mutate):
    from app.security import InvalidStateError, sign_oauth_state, verify_oauth_state

    with pytest.raises(InvalidStateError):
        verify_oauth_state(mutate(sign_oauth_state(123456789, "whoop")), "whoop")


def test_oauth_state_is_purpose_bound_and_expires(mock_settings):
    from app.security import (
        OAUTH_STATE_TTL_SECONDS,
        InvalidStateError,
        sign_oauth_state,
        verify_oauth_state,
    )

    now = int(time.time())
    state = sign_oauth_state(1, "whoop", now=now)
    with pytest.raises(InvalidStateError):
        verify_oauth_state(state, "fatsecret", now=now)
    with pytest.raises(InvalidStateError):
        verify_oauth_state(state, "whoop", now=now + OAUTH_STATE_TTL_SECONDS + 1)
    assert verify_oauth_state(state, "whoop", now=now + OAUTH_STATE_TTL_SECONDS - 1) == 1


def test_resolve_timezone_falls_back_on_invalid(mock_settings):
    from app.timeutils import resolve_timezone

    assert resolve_timezone("Europe/Warsaw") == ZoneInfo("Europe/Warsaw")
    assert resolve_timezone("Not/AZone").key == "Europe/Kyiv"
    assert resolve_timezone(None).key == "Europe/Kyiv"


def test_local_day_bounds_utc_respects_timezone(mock_settings):
    from app.timeutils import local_day_bounds_utc

    # 22:30 UTC on Sep 19 is already Sep 20 in Kyiv (UTC+3).
    now = datetime(2026, 9, 19, 22, 30, tzinfo=timezone.utc)
    start, end = local_day_bounds_utc(ZoneInfo("Europe/Kyiv"), now)
    assert start == datetime(2026, 9, 19, 21, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 9, 20, 21, 0, tzinfo=timezone.utc)


def test_fatsecret_today_uses_local_date(mock_settings, monkeypatch):
    from app.services import fatsecret_api

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 19, 22, 30, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(fatsecret_api, "datetime", FrozenDatetime)
    kyiv = fatsecret_api.fatsecret_today(ZoneInfo("Europe/Kyiv"))
    utc = fatsecret_api.fatsecret_today(ZoneInfo("UTC"))
    assert kyiv == utc + 1


def test_fatsecret_form_parser_decodes_values(mock_settings):
    from app.services.fatsecret_auth import _parse_form

    parsed = _parse_form("oauth_token=a%2Bb&oauth_token_secret=s%3D1&oauth_callback_confirmed=true")
    assert parsed == {
        "oauth_token": "a+b",
        "oauth_token_secret": "s=1",
        "oauth_callback_confirmed": "true",
    }


@pytest.mark.asyncio
async def test_fatsecret_error_body_raises_api_error(mock_settings):
    from app.services.fatsecret_api import (
        FatSecretAPIError,
        FatSecretAuthError,
        _raise_on_error_body,
    )

    with pytest.raises(FatSecretAuthError):
        _raise_on_error_body({"error": {"code": 8, "message": "Invalid token"}}, "t")
    with pytest.raises(FatSecretAPIError):
        _raise_on_error_body({"error": {"code": 106, "message": "Invalid ID"}}, "t")
    _raise_on_error_body({"food_entries": None}, "t")


def test_minutes_apart_wraps_midnight(mock_settings):
    from app.services.briefings import _minutes_apart

    assert _minutes_apart(dtime(23, 58), dtime(0, 2)) == 4
    assert _minutes_apart(dtime(10, 0), dtime(10, 5)) == 5


def test_users_at_local_hour_uses_each_users_timezone(mock_settings):
    from app.services.briefings import _users_at_local_hour

    now = datetime(2026, 9, 20, 5, 0, tzinfo=timezone.utc)  # 08:00 Kyiv, 07:00 Warsaw
    users = [
        {"id": 1, "timezone": "Europe/Kyiv"},
        {"id": 2, "timezone": "Europe/Warsaw"},
        {"id": 3, "timezone": None},  # default Europe/Kyiv
    ]
    assert [u["id"] for u in _users_at_local_hour(users, 8, now)] == [1, 3]
    assert _users_at_local_hour(users, None, now) == users


def test_settings_reject_out_of_range_sync_hours(mock_settings):
    from pydantic import ValidationError

    from app.config import Settings

    with pytest.raises(ValidationError):
        Settings(apple_health_sync_hours=0)
