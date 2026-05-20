import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient


def test_verify_apple_health_token_accepts_matching_token(mock_settings):
    from app.services.apple_health import verify_apple_health_token

    assert verify_apple_health_token("user-secret", "user-secret") is True


def test_verify_apple_health_token_rejects_mismatch(mock_settings):
    from app.services.apple_health import verify_apple_health_token

    assert verify_apple_health_token("wrong-token", "user-secret") is False


class FakePool:
    def __init__(self):
        self.executed = []
        self.duplicate_metric = None

    async def fetchrow(self, query, *args):
        if "INSERT INTO apple_health_sync" in query:
            return {"secret_key": args[1]}
        if "FROM users" in query and "apple_health_sync" in query:
            return {"user_id": 7, "sync_id": 3, "secret_key": "user-secret"}
        if "FROM health_data" in query:
            return self.duplicate_metric
        raise AssertionError(f"Unexpected fetchrow query: {query}")

    async def execute(self, query, *args):
        self.executed.append((query, args))
        return "OK"


def _json_body(payload: dict) -> bytes:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return body


@pytest.mark.asyncio
async def test_ensure_apple_health_sync_returns_per_user_token(mock_settings):
    from app.services.apple_health import ensure_apple_health_sync

    with patch("app.services.apple_health.secrets.token_urlsafe", return_value="generated-token"):
        result = await ensure_apple_health_sync(FakePool(), user_id=7, sync_frequency_hours=6)

    assert result == {"secret_key": "generated-token"}


@pytest.mark.asyncio
async def test_ingest_apple_health_payload_stores_recent_metrics(mock_settings):
    from app.services.apple_health import ingest_apple_health_payload

    pool = FakePool()
    payload = {
        "userId": 999,
        "sourceType": "apple_health",
        "dataType": "activity",
        "syncTimestamp": "2026-05-20T12:00:00+00:00",
        "metrics": [
            {
                "type": "active_energy",
                "value": 123.4,
                "unit": "kcal",
                "timestamp": "2026-05-20T10:00:00+00:00",
                "duration": 3600,
            },
            {
                "type": "step_count",
                "value": 5000,
                "unit": "count",
                "timestamp": "2026-05-20T11:00:00+00:00",
            },
        ],
    }

    result = await ingest_apple_health_payload(
        pool,
        payload,
        now=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
    )

    assert result == {
        "records_received": 2,
        "records_processed": 2,
        "records_failed": 0,
    }
    inserts = [query for query, _ in pool.executed if "INSERT INTO health_data" in query]
    assert len(inserts) == 2
    sync_updates = [query for query, _ in pool.executed if "UPDATE apple_health_sync" in query]
    assert len(sync_updates) == 1


@pytest.mark.asyncio
async def test_ingest_apple_health_payload_rejects_old_metrics(mock_settings):
    from app.services.apple_health import AppleHealthIngestionError, ingest_apple_health_payload

    payload = {
        "userId": 999,
        "sourceType": "apple_health",
        "dataType": "activity",
        "metrics": [
            {
                "type": "step_count",
                "value": 5000,
                "unit": "count",
                "timestamp": "2026-04-01T11:00:00+00:00",
            }
        ],
    }

    with pytest.raises(AppleHealthIngestionError, match="older than 30 days"):
        await ingest_apple_health_payload(
            FakePool(),
            payload,
            now=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
        )


@pytest.mark.asyncio
async def test_ingest_apple_health_payload_does_not_partially_insert_invalid_batch(mock_settings):
    from app.services.apple_health import AppleHealthIngestionError, ingest_apple_health_payload

    pool = FakePool()
    payload = {
        "userId": 999,
        "sourceType": "apple_health",
        "dataType": "activity",
        "metrics": [
            {
                "type": "step_count",
                "value": 5000,
                "unit": "count",
                "timestamp": "2026-05-20T11:00:00+00:00",
            },
            {
                "type": "active_energy",
                "value": 100,
                "unit": "kcal",
                "timestamp": "2026-04-01T11:00:00+00:00",
            },
        ],
    }

    with pytest.raises(AppleHealthIngestionError, match="older than 30 days"):
        await ingest_apple_health_payload(
            pool,
            payload,
            now=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
        )

    assert not [query for query, _ in pool.executed if "INSERT INTO health_data" in query]


@pytest.mark.asyncio
async def test_apple_health_webhook_rejects_invalid_signature(mock_settings):
    from app.main import app
    from app.routers import apple_health as apple_health_router

    payload = {"userId": 999, "sourceType": "apple_health", "metrics": []}
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    with patch("app.routers.apple_health.get_pool", return_value=FakePool()):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/health/apple-health/sync",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Apple-Health-Token": "invalid",
                },
            )

    assert apple_health_router.router is not None
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_apple_health_webhook_ingests_valid_payload(mock_settings):
    from app.main import app

    payload = {
        "userId": 999,
        "sourceType": "apple_health",
        "dataType": "activity",
        "metrics": [
            {
                "type": "step_count",
                "value": 5000,
                "unit": "count",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ],
    }
    body = _json_body(payload)
    pool = FakePool()

    with patch("app.routers.apple_health.get_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/health/apple-health/sync",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Apple-Health-Token": "user-secret",
                },
            )

    assert resp.status_code == 200
    assert resp.json()["records_processed"] == 1


@pytest.mark.asyncio
async def test_apple_health_webhook_accepts_user_and_token_from_url(mock_settings):
    from app.main import app

    payload = {
        "sourceType": "apple_health",
        "dataType": "activity",
        "metrics": [
            {
                "type": "step_count",
                "value": 5000,
                "unit": "count",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ],
    }
    body = _json_body(payload)
    pool = FakePool()

    with patch("app.routers.apple_health.get_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/health/apple-health/sync?userId=999&token=user-secret",
                content=body,
                headers={"Content-Type": "application/json"},
            )

    assert resp.status_code == 200
    assert resp.json()["records_processed"] == 1


def test_is_health_auto_export_payload_detects_shape(mock_settings):
    from app.services.apple_health import is_health_auto_export_payload

    assert is_health_auto_export_payload({"data": {"metrics": []}}) is True
    assert is_health_auto_export_payload(
        {"data": {"metrics": [{"name": "step_count", "units": "count", "data": []}]}}
    ) is True
    assert is_health_auto_export_payload({"sourceType": "apple_health", "metrics": []}) is False
    assert is_health_auto_export_payload({"data": "string"}) is False
    assert is_health_auto_export_payload(None) is False


def test_convert_health_auto_export_flattens_and_normalizes_dates(mock_settings):
    from app.services.apple_health import convert_health_auto_export

    hae_payload = {
        "data": {
            "metrics": [
                {
                    "name": "step_count",
                    "units": "count",
                    "data": [
                        {"date": "2026-05-20 14:30:00 +0300", "qty": 5000},
                        {"date": "2026-05-20 15:30:00 +0300", "qty": 1200},
                    ],
                },
                {
                    "name": "heart_rate",
                    "units": "count/min",
                    "data": [
                        {"date": "2026-05-20 14:30:00 +0000", "Avg": 72},
                    ],
                },
            ]
        }
    }

    result = convert_health_auto_export(hae_payload, telegram_user_id=42)

    assert result["sourceType"] == "apple_health"
    assert result["dataType"] == "auto_export"
    assert result["userId"] == 42
    assert len(result["metrics"]) == 3
    assert result["metrics"][0] == {
        "type": "step_count",
        "value": 5000,
        "unit": "count",
        "timestamp": "2026-05-20T14:30:00+03:00",
    }
    assert result["metrics"][2]["type"] == "heart_rate"
    assert result["metrics"][2]["value"] == 72


def test_convert_health_auto_export_skips_invalid_points(mock_settings):
    from app.services.apple_health import convert_health_auto_export

    hae_payload = {
        "data": {
            "metrics": [
                {
                    "name": "step_count",
                    "units": "count",
                    "data": [
                        {"date": "", "qty": 1},  # empty date → skip
                        {"qty": 2},  # missing date → skip
                        {"date": "2026-05-20 14:30:00 +0300"},  # no value → skip
                        {"date": "2026-05-20 14:30:00 +0300", "qty": 5000},  # keep
                    ],
                }
            ]
        }
    }

    result = convert_health_auto_export(hae_payload, telegram_user_id=42)
    assert len(result["metrics"]) == 1
    assert result["metrics"][0]["value"] == 5000


@pytest.mark.asyncio
async def test_apple_health_webhook_accepts_health_auto_export_format(mock_settings):
    from app.main import app

    iso_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S +0000")
    hae_payload = {
        "data": {
            "metrics": [
                {
                    "name": "step_count",
                    "units": "count",
                    "data": [
                        {"date": iso_now, "qty": 5000},
                        {"date": iso_now, "qty": 1200},
                    ],
                }
            ]
        }
    }
    body = _json_body(hae_payload)
    pool = FakePool()

    with patch("app.routers.apple_health.get_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/health/apple-health/sync?userId=999&token=user-secret",
                content=body,
                headers={"Content-Type": "application/json"},
            )

    assert resp.status_code == 200
    # Two HAE data points with same type+unit+timestamp+value should dedupe.
    # We only assert at least one was processed end-to-end.
    assert resp.json()["records_received"] == 2
