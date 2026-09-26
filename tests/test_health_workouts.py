import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

NOW = datetime(2026, 9, 20, 18, 0, tzinfo=timezone.utc)


def test_normalize_native_workout(mock_settings):
    from app.services.health_workouts import normalize_workout

    w = normalize_workout(
        {
            "type": "Running",
            "start": "2026-09-20T07:00:00+03:00",
            "end": "2026-09-20T07:45:00+03:00",
            "active_energy": "420,5",
            "active_energy_unit": "ккал",
            "distance": "7.2",
            "distance_unit": "km",
            "avg_heart_rate": "151",
        },
        collector="shortcut",
        current_time=NOW,
    )
    assert w["workout_type"] == "Running"
    assert w["duration_seconds"] == 45 * 60
    assert w["active_energy_kcal"] == Decimal("420.50")
    assert w["distance_m"] == Decimal("7200.00")
    assert w["avg_heart_rate"] == Decimal("151.00")
    assert w["external_id"].startswith("derived:")


def test_normalize_health_auto_export_workout(mock_settings):
    from app.services.health_workouts import normalize_workout

    w = normalize_workout(
        {
            "id": "ABC-123",
            "name": "Outdoor Cycling",
            "start": "2026-09-20 10:00:00 +0300",
            "end": "2026-09-20 11:00:00 +0300",
            "duration": 3500,
            "activeEnergyBurned": {"qty": 2092, "units": "kJ"},
            "distance": {"qty": 20, "units": "mi"},
            "heartRate": {"avg": {"qty": 140}, "max": {"qty": 171}},
        },
        collector="health_auto_export",
        current_time=NOW,
    )
    assert w["external_id"] == "ABC-123"
    assert w["duration_seconds"] == 3500
    assert w["active_energy_kcal"] == Decimal("500.00")
    assert w["distance_m"] == Decimal("32186.88")
    assert w["max_heart_rate"] == Decimal("171.00")


@pytest.mark.parametrize(
    "raw",
    [
        {"start": "2026-09-20T07:00:00+00:00", "end": "2026-09-20T08:00:00+00:00"},  # no type
        {"type": "Run", "start": "2026-09-20T08:00:00+00:00", "end": "2026-09-20T07:00:00+00:00"},
        {"type": "Run", "start": "2026-09-20T07:00:00", "end": "2026-09-20T08:00:00"},  # naive
        {"type": "Run", "start": "2026-07-01T07:00:00+00:00", "end": "2026-07-01T08:00:00+00:00"},
        {"type": "Run", "start": "2026-09-20T07:00:00+00:00", "end": "2026-09-20T08:00:00+00:00",
         "avg_heart_rate": 400},
        {"type": "Run", "start": "2026-09-20T07:00:00+00:00", "end": "2026-09-20T08:00:00+00:00",
         "distance": 5, "distance_unit": "kcal"},
        "not-an-object",
    ],
)
def test_invalid_workouts_are_rejected(mock_settings, raw):
    from app.services.apple_health import AppleHealthIngestionError
    from app.services.health_workouts import normalize_workout

    with pytest.raises(AppleHealthIngestionError):
        normalize_workout(raw, collector="shortcut", current_time=NOW)


def test_normalize_workouts_dedupes_and_limits(mock_settings):
    from app.services.apple_health import AppleHealthIngestionError
    from app.services.health_workouts import MAX_WORKOUTS_PER_REQUEST, normalize_workouts

    item = {"id": "x", "type": "Walk", "start": "2026-09-20T07:00:00+00:00",
            "end": "2026-09-20T07:30:00+00:00"}
    assert len(normalize_workouts([item, dict(item)], collector="shortcut", current_time=NOW)) == 1
    with pytest.raises(AppleHealthIngestionError):
        normalize_workouts([item] * (MAX_WORKOUTS_PER_REQUEST + 1), collector="shortcut")
    with pytest.raises(AppleHealthIngestionError):
        normalize_workouts({"not": "a list"}, collector="shortcut")


@pytest.mark.asyncio
async def test_workouts_summary_formats_rows(mock_settings):
    from app.services.health_workouts import get_workouts_summary

    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[{
        "workout_type": "Running", "started_at": NOW, "duration_seconds": 1800,
        "active_energy_kcal": Decimal("300"), "distance_m": Decimal("5000"),
        "avg_heart_rate": Decimal("150"),
    }])
    summary = await get_workouts_summary(pool, 1, start_at=NOW, end_at=NOW)
    assert summary["count"] == 1
    assert summary["active_energy_kcal"] == 300
    assert summary["summary"] == (
        "Apple Health workouts today: Running (30 min, 300 kcal, 5.00 km, avg HR 150)"
    )


class _WorkoutPool:
    """Minimal pool for the webhook: active sync lookup + workout upserts."""

    def __init__(self):
        from app.crypto import hash_secret

        self.upserts = []
        self.executed = []
        self.secret = hash_secret("user-secret")

    async def fetchrow(self, query, *args):
        if "INSERT INTO health_workouts" in query:
            self.upserts.append(args)
            return {"inserted": True}
        if "apple_health_sync" in query:
            return {"user_id": 7, "sync_id": 3, "secret_key": self.secret}
        raise AssertionError(query)

    async def execute(self, query, *args):
        self.executed.append(query)
        return "OK"


def _recent(hour):
    day = datetime.now(timezone.utc).date().isoformat()
    return f"{day}T{hour:02d}:00:00+00:00"


@pytest.mark.asyncio
async def test_webhook_accepts_workouts_only_native_payload(mock_settings):
    from app.main import app

    pool = _WorkoutPool()
    body = {"sourceType": "apple_health", "workouts": [
        {"type": "Yoga", "start": _recent(0), "end": _recent(1)},
    ]}
    with (
        patch("app.routers.apple_health.get_pool", return_value=pool),
        patch("app.services.telegram_bot.send_message", AsyncMock()) as send,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/health/apple-health/sync?userId=999&token=user-secret",
                content=json.dumps(body),
                headers={"Content-Type": "application/json"},
            )
    assert resp.status_code == 200, resp.text
    assert resp.json()["workouts"] == {
        "workouts_received": 1, "workouts_inserted": 1, "workouts_updated": 0,
    }
    assert pool.upserts[0][1] == "shortcut"
    assert "Тренувань: 1" in send.await_args.args[1]


@pytest.mark.asyncio
async def test_webhook_accepts_health_auto_export_workouts_without_metric_headers(mock_settings):
    from app.main import app

    pool = _WorkoutPool()
    day = datetime.now(timezone.utc).date().isoformat()
    body = {"data": {"metrics": [], "workouts": [{
        "id": "HAE-1", "name": "Swimming",
        "start": f"{day} 00:00:00 +0000", "end": f"{day} 00:40:00 +0000",
    }]}}
    with patch("app.routers.apple_health.get_pool", return_value=pool):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/health/apple-health/sync?userId=999&token=user-secret",
                content=json.dumps(body),
                headers={"Content-Type": "application/json"},
            )
    assert resp.status_code == 200, resp.text
    assert pool.upserts[0][1] == "health_auto_export"
    assert pool.upserts[0][2] == "HAE-1"


@pytest.mark.asyncio
async def test_webhook_rejects_invalid_workouts_before_writing(mock_settings):
    from app.main import app

    pool = _WorkoutPool()
    body = {"sourceType": "apple_health", "workouts": [{"type": "Run"}]}
    with patch("app.routers.apple_health.get_pool", return_value=pool):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/health/apple-health/sync?userId=999&token=user-secret",
                content=json.dumps(body),
                headers={"Content-Type": "application/json"},
            )
    assert resp.status_code == 400
    assert pool.upserts == []


@pytest.mark.asyncio
async def test_webhook_rejects_workouts_with_wrong_token(mock_settings):
    from app.main import app

    pool = _WorkoutPool()
    body = {"sourceType": "apple_health", "workouts": []}
    with patch("app.routers.apple_health.get_pool", return_value=pool):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/health/apple-health/sync?userId=999&token=wrong",
                content=json.dumps(body),
                headers={"Content-Type": "application/json"},
            )
    assert resp.status_code == 401
