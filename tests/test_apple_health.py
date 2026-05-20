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
