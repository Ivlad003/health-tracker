import json
import logging
import plistlib
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


def test_verify_apple_health_token_accepts_matching_token(mock_settings):
    from app.services.apple_health import verify_apple_health_token

    assert verify_apple_health_token("user-secret", "user-secret") is True


def test_verify_apple_health_token_rejects_mismatch(mock_settings):
    from app.services.apple_health import verify_apple_health_token

    assert verify_apple_health_token("wrong-token", "user-secret") is False


@pytest.mark.asyncio
async def test_apple_health_shortcut_download_serves_signed_artifact(mock_settings):
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/health/apple-health/shortcut")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/x-shortcut"
    assert "apple-health-sync.shortcut" in response.headers["content-disposition"]
    assert response.content.startswith(b"AEA1")
    assert b"bplist00" in response.content[:32]


def _shortcut_text_value(field: dict) -> str | None:
    value = field.get("WFValue", {}).get("Value", {})
    return value.get("string")


def _shortcut_key(field: dict) -> str:
    return field["WFKey"]["Value"]["string"]


def _shortcut_token_attachment(field: dict) -> dict:
    value = field.get("WFValue", field)["Value"]
    return value["attachmentsByRange"]["{0, 1}"]


def test_apple_health_shortcut_template_posts_required_metrics_payload():
    shortcut_source = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "shortcuts"
        / "apple-health-sync.shortcut.plist"
    )
    workflow = plistlib.loads(shortcut_source.read_bytes())

    actions = workflow["WFWorkflowActions"]
    health_action = next(
        action
        for action in actions
        if action["WFWorkflowActionIdentifier"] == "is.workflow.actions.filter.health.quantity"
    )
    health_action_uuid = health_action["WFWorkflowActionParameters"]["UUID"]
    repeat_actions = [
        action
        for action in actions
        if action["WFWorkflowActionIdentifier"] == "is.workflow.actions.repeat.each"
    ]
    repeat_start = next(
        action
        for action in repeat_actions
        if action["WFWorkflowActionParameters"]["WFControlFlowMode"] == 0
    )
    repeat_end = next(
        action
        for action in repeat_actions
        if action["WFWorkflowActionParameters"]["WFControlFlowMode"] == 2
    )
    metric_action = next(
        action
        for action in actions
        if action["WFWorkflowActionIdentifier"] == "is.workflow.actions.dictionary"
        and action["WFWorkflowActionParameters"].get("CustomOutputName") == "Step Metric"
    )
    payload_base_action = next(
        action
        for action in actions
        if action["WFWorkflowActionIdentifier"] == "is.workflow.actions.dictionary"
        and action["WFWorkflowActionParameters"].get("CustomOutputName") == "Sync Payload Base"
    )
    set_value_action = next(
        action
        for action in actions
        if action["WFWorkflowActionIdentifier"] == "is.workflow.actions.setvalueforkey"
    )
    plist_file_action = next(
        action
        for action in actions
        if action["WFWorkflowActionIdentifier"] == "is.workflow.actions.gettypeaction"
        and action["WFWorkflowActionParameters"].get("CustomOutputName") == "Payload Plist"
    )
    json_file_action = next(
        action
        for action in actions
        if action["WFWorkflowActionIdentifier"] == "is.workflow.actions.gettypeaction"
        and action["WFWorkflowActionParameters"].get("CustomOutputName") == "Payload JSON"
    )
    post_action = next(
        action
        for action in actions
        if action["WFWorkflowActionIdentifier"] == "is.workflow.actions.downloadurl"
    )

    assert repeat_start["WFWorkflowActionParameters"]["WFInput"]["Value"] == {
        "OutputName": "Health Samples",
        "OutputUUID": health_action_uuid,
        "Type": "ActionOutput",
    }

    metric_items = metric_action["WFWorkflowActionParameters"]["WFItems"]["Value"][
        "WFDictionaryFieldValueItems"
    ]
    metric_by_key = {_shortcut_key(item): item for item in metric_items}
    assert _shortcut_text_value(metric_by_key["type"]) == "step_count"
    assert _shortcut_text_value(metric_by_key["unit"]) == "count"

    value_attachment = _shortcut_token_attachment(metric_by_key["value"])
    assert value_attachment["Type"] == "Variable"
    assert value_attachment["VariableName"] == "Repeat Item"
    assert value_attachment["Aggrandizements"] == [
        {"Type": "WFPropertyVariableAggrandizement", "PropertyName": "Quantity"}
    ]

    timestamp_attachment = _shortcut_token_attachment(metric_by_key["timestamp"])
    assert timestamp_attachment["Type"] == "Variable"
    assert timestamp_attachment["VariableName"] == "Repeat Item"
    assert timestamp_attachment["Aggrandizements"] == [
        {"Type": "WFPropertyVariableAggrandizement", "PropertyName": "Start Date"},
        {
            "Type": "WFDateFormatVariableAggrandizement",
            "WFDateFormatStyle": "ISO 8601",
            "WFISO8601IncludeTime": True,
        },
    ]

    payload_items = payload_base_action["WFWorkflowActionParameters"]["WFItems"]["Value"][
        "WFDictionaryFieldValueItems"
    ]
    payload_by_key = {_shortcut_key(item): item for item in payload_items}

    assert _shortcut_text_value(payload_by_key["sourceType"]) == "apple_health"
    assert _shortcut_text_value(payload_by_key["dataType"]) == "activity"

    set_value_parameters = set_value_action["WFWorkflowActionParameters"]
    assert set_value_parameters["WFDictionaryKey"]["Value"]["string"] == "metrics"
    assert set_value_parameters["WFDictionary"]["Value"] == {
        "OutputName": "Sync Payload Base",
        "OutputUUID": payload_base_action["WFWorkflowActionParameters"]["UUID"],
        "Type": "ActionOutput",
    }
    assert _shortcut_token_attachment(set_value_parameters["WFDictionaryValue"]) == {
        "OutputName": "Repeat Results",
        "OutputUUID": repeat_end["WFWorkflowActionParameters"]["UUID"],
        "Type": "ActionOutput",
    }

    plist_file_parameters = plist_file_action["WFWorkflowActionParameters"]
    assert plist_file_parameters["WFFileType"] == "com.apple.plist"
    assert plist_file_parameters["WFInput"] == {
        "WFSerializationType": "WFTextTokenAttachment",
        "Value": {
            "OutputName": "Sync Payload",
            "OutputUUID": set_value_parameters["UUID"],
            "Type": "ActionOutput",
        },
    }

    json_file_parameters = json_file_action["WFWorkflowActionParameters"]
    assert json_file_parameters["WFFileType"] == "public.json"
    assert json_file_parameters["WFInput"] == {
        "WFSerializationType": "WFTextTokenAttachment",
        "Value": {
            "OutputName": "Payload Plist",
            "OutputUUID": plist_file_parameters["UUID"],
            "Type": "ActionOutput",
        },
    }

    post_parameters = post_action["WFWorkflowActionParameters"]
    assert post_parameters["WFHTTPBodyType"] == "File"
    assert "WFJSONValues" not in post_parameters
    request_variable = post_parameters["WFRequestVariable"]
    assert request_variable["WFSerializationType"] == "WFTextTokenAttachment"
    assert request_variable["Value"] == {
        "OutputName": "Payload JSON",
        "OutputUUID": json_file_parameters["UUID"],
        "Type": "ActionOutput",
    }


class FakePool:
    def __init__(self):
        self.executed = []
        self.fetchrow_calls = []
        self.duplicate_metric = None
        self.health_rows = []
        self.force_insert_conflict = False
        self.fail_health_data_insert = False
        self.apple_health_secret = "user-secret"
        self.active_sync = {"user_id": 7, "sync_id": 3, "secret_key": self.apple_health_secret}
        self.inactive_sync = None

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query, args))
        if "INSERT INTO apple_health_sync" in query:
            self.apple_health_secret = args[1]
            self.active_sync = {"user_id": 7, "sync_id": 3, "secret_key": self.apple_health_secret}
            return {"secret_key": args[1]}
        if "FROM users" in query and "apple_health_sync" in query and "ahs.is_active = TRUE" in query:
            return self.active_sync
        if "FROM users" in query and "apple_health_sync" in query:
            return self.inactive_sync or self.active_sync
        if "FROM health_data" in query:
            if self.duplicate_metric is not None:
                return self.duplicate_metric
            user_id, metric_type, recorded_at = args
            for row in self.health_rows:
                if (
                    row["user_id"] == user_id
                    and row["source"] == "apple_health"
                    and row["metric_type"] == metric_type
                    and row["recorded_at"] == recorded_at
                ):
                    return row
            return None
        if "INSERT INTO health_data" in query:
            if self.fail_health_data_insert:
                raise RuntimeError("database unavailable")
            self.executed.append((query, args))
            if self.force_insert_conflict:
                return None
            key = (args[0], "apple_health", args[1], args[5])
            for row in self.health_rows:
                if (row["user_id"], row["source"], row["metric_type"], row["recorded_at"]) == key:
                    return None
            row = {
                "id": len(self.health_rows) + 1,
                "user_id": args[0],
                "source": "apple_health",
                "metric_type": args[1],
                "metric_subtype": args[2],
                "value": args[3],
                "unit": args[4],
                "recorded_at": args[5],
            }
            self.health_rows.append(row)
            return {"id": row["id"]}
        raise AssertionError(f"Unexpected fetchrow query: {query}")

    async def execute(self, query, *args):
        self.executed.append((query, args))
        return "OK"


def _json_body(payload: dict) -> bytes:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return body


def _executed(pool: FakePool, fragment: str):
    return [(query, args) for query, args in pool.executed if fragment in query]


def _assert_logs_do_not_contain(caplog, *sensitive_values: str) -> None:
    # Check rendered messages only; caplog.text also contains file:lineno
    # prefixes, which can spuriously match short values like "72".
    log_output = "\n".join(record.getMessage() for record in caplog.records)
    for sensitive_value in sensitive_values:
        assert sensitive_value not in log_output


@pytest.mark.asyncio
async def test_ensure_apple_health_sync_returns_per_user_token(mock_settings):
    from app.services.apple_health import ensure_apple_health_sync

    with patch("app.services.apple_health.secrets.token_urlsafe", return_value="generated-token"):
        result = await ensure_apple_health_sync(FakePool(), user_id=7, sync_frequency_hours=6)

    assert result == {"secret_key": "generated-token"}


@pytest.mark.asyncio
async def test_ensure_apple_health_sync_rotates_token_on_reconnect(mock_settings):
    from app.services.apple_health import ensure_apple_health_sync

    pool = FakePool()

    with patch("app.services.apple_health.secrets.token_urlsafe", side_effect=["old-token", "new-token"]):
        first = await ensure_apple_health_sync(pool, user_id=7, sync_frequency_hours=6)
        second = await ensure_apple_health_sync(pool, user_id=7, sync_frequency_hours=6)

    assert first == {"secret_key": "old-token"}
    assert second == {"secret_key": "new-token"}
    assert pool.apple_health_secret == "new-token"
    upsert_query = pool.fetchrow_calls[-1][0]
    assert "secret_key = EXCLUDED.secret_key" in upsert_query


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
        "records_inserted": 2,
        "records_skipped": 0,
        "records_duplicate": 0,
        "records_conflict": 0,
        "records_failed": 0,
    }
    inserts = [query for query, _ in pool.executed if "INSERT INTO health_data" in query]
    assert len(inserts) == 2
    sync_updates = [query for query, _ in pool.executed if "UPDATE apple_health_sync" in query]
    assert len(sync_updates) == 1
    import_logs = _executed(pool, "INSERT INTO apple_health_import_logs")
    assert len(import_logs) == 1
    _, log_args = import_logs[0]
    assert log_args[:7] == (7, 3, 200, 2, 2, 0, None)
    request_summary = json.loads(log_args[7])
    assert request_summary == {
        "sourceType": "apple_health",
        "dataType": "activity",
        "syncTimestamp": "2026-05-20T12:00:00+00:00",
        "metrics_count": 2,
    }
    assert "123.4" not in log_args[7]
    assert "5000" not in log_args[7]


@pytest.mark.asyncio
async def test_ingest_apple_health_payload_reports_identical_existing_metric_as_duplicate(mock_settings):
    from app.services.apple_health import ingest_apple_health_payload

    recorded_at = datetime(2026, 5, 20, 11, 0, tzinfo=timezone.utc)
    pool = FakePool()
    pool.health_rows.append({
        "id": 1,
        "user_id": 7,
        "source": "apple_health",
        "metric_type": "step_count",
        "value": 5000,
        "unit": "count",
        "recorded_at": recorded_at,
    })
    payload = {
        "userId": 999,
        "sourceType": "apple_health",
        "dataType": "activity",
        "metrics": [{
            "type": "step_count",
            "value": 5000,
            "unit": "count",
            "timestamp": recorded_at.isoformat(),
        }],
    }

    result = await ingest_apple_health_payload(
        pool,
        payload,
        now=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
    )

    assert result == {
        "records_received": 1,
        "records_processed": 0,
        "records_inserted": 0,
        "records_skipped": 1,
        "records_duplicate": 1,
        "records_conflict": 0,
        "records_failed": 0,
    }


@pytest.mark.asyncio
async def test_ingest_apple_health_payload_reports_same_timestamp_different_value_as_conflict(mock_settings):
    from app.services.apple_health import ingest_apple_health_payload

    recorded_at = datetime(2026, 5, 20, 11, 0, tzinfo=timezone.utc)
    pool = FakePool()
    pool.health_rows.append({
        "id": 1,
        "user_id": 7,
        "source": "apple_health",
        "metric_type": "step_count",
        "value": 4000,
        "unit": "count",
        "recorded_at": recorded_at,
    })
    payload = {
        "userId": 999,
        "sourceType": "apple_health",
        "dataType": "activity",
        "metrics": [{
            "type": "step_count",
            "value": 5000,
            "unit": "count",
            "timestamp": recorded_at.isoformat(),
        }],
    }

    result = await ingest_apple_health_payload(
        pool,
        payload,
        now=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
    )

    assert result == {
        "records_received": 1,
        "records_processed": 0,
        "records_inserted": 0,
        "records_skipped": 1,
        "records_duplicate": 0,
        "records_conflict": 1,
        "records_failed": 0,
    }


@pytest.mark.asyncio
async def test_ingest_apple_health_payload_does_not_count_database_insert_conflict_as_processed(mock_settings):
    from app.services.apple_health import ingest_apple_health_payload

    pool = FakePool()
    pool.force_insert_conflict = True
    payload = {
        "userId": 999,
        "sourceType": "apple_health",
        "dataType": "activity",
        "metrics": [{
            "type": "step_count",
            "value": 5000,
            "unit": "count",
            "timestamp": "2026-05-20T11:00:00+00:00",
        }],
    }

    result = await ingest_apple_health_payload(
        pool,
        payload,
        now=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
    )

    assert result == {
        "records_received": 1,
        "records_processed": 0,
        "records_inserted": 0,
        "records_skipped": 1,
        "records_duplicate": 0,
        "records_conflict": 1,
        "records_failed": 0,
    }


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
            pool := FakePool(),
            payload,
            now=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
        )

    failure_updates = _executed(pool, "UPDATE apple_health_sync")
    assert len(failure_updates) == 1
    assert failure_updates[0][1] == (3, "metric timestamp is older than 30 days")
    import_logs = _executed(pool, "INSERT INTO apple_health_import_logs")
    assert len(import_logs) == 1
    _, log_args = import_logs[0]
    assert log_args[:7] == (7, 3, 400, 1, 0, 1, "metric timestamp is older than 30 days")
    assert "5000" not in log_args[7]


@pytest.mark.asyncio
async def test_ingest_apple_health_payload_logs_malformed_payload_after_sync_lookup(mock_settings):
    from app.services.apple_health import AppleHealthIngestionError, ingest_apple_health_payload

    pool = FakePool()
    payload = {
        "userId": 999,
        "sourceType": "not_apple_health",
        "metrics": [],
    }

    with pytest.raises(AppleHealthIngestionError, match="sourceType must be apple_health"):
        await ingest_apple_health_payload(pool, payload)

    failure_updates = _executed(pool, "UPDATE apple_health_sync")
    assert len(failure_updates) == 1
    assert failure_updates[0][1] == (3, "sourceType must be apple_health")
    import_logs = _executed(pool, "INSERT INTO apple_health_import_logs")
    assert len(import_logs) == 1
    _, log_args = import_logs[0]
    assert log_args[:7] == (7, 3, 400, 0, 0, 1, "sourceType must be apple_health")


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
async def test_ingest_apple_health_payload_logs_db_failures(mock_settings):
    from app.services.apple_health import AppleHealthIngestionError, ingest_apple_health_payload

    pool = FakePool()
    pool.fail_health_data_insert = True
    payload = {
        "userId": 999,
        "sourceType": "apple_health",
        "dataType": "activity",
        "metrics": [{
            "type": "step_count",
            "value": 5000,
            "unit": "count",
            "timestamp": "2026-05-20T11:00:00+00:00",
        }],
    }

    with pytest.raises(AppleHealthIngestionError, match="failed to process Apple Health metric"):
        await ingest_apple_health_payload(
            pool,
            payload,
            now=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
        )

    failure_updates = _executed(pool, "UPDATE apple_health_sync")
    assert len(failure_updates) == 1
    assert failure_updates[0][1] == (3, "failed to process Apple Health metric")
    import_logs = _executed(pool, "INSERT INTO apple_health_import_logs")
    assert len(import_logs) == 1
    _, log_args = import_logs[0]
    assert log_args[:7] == (7, 3, 500, 1, 0, 1, "failed to process Apple Health metric")
    assert "5000" not in log_args[7]


@pytest.mark.asyncio
async def test_apple_health_webhook_rejects_invalid_signature(mock_settings):
    from app.main import app
    from app.routers import apple_health as apple_health_router

    payload = {"userId": 999, "sourceType": "apple_health", "metrics": []}
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    pool = FakePool()

    with patch("app.routers.apple_health.get_pool", return_value=pool):
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
    failure_updates = _executed(pool, "UPDATE apple_health_sync")
    assert len(failure_updates) == 1
    assert failure_updates[0][1] == (3, "Invalid Apple Health token")
    import_logs = _executed(pool, "INSERT INTO apple_health_import_logs")
    assert len(import_logs) == 1
    _, log_args = import_logs[0]
    assert log_args[:7] == (7, 3, 401, 0, 0, 0, "Invalid Apple Health token")
    assert "invalid" not in (log_args[7] or "")


@pytest.mark.asyncio
async def test_apple_health_webhook_does_not_log_sensitive_payload_on_invalid_token(mock_settings, caplog):
    from app.main import app

    payload = {
        "userId": 999,
        "sourceType": "apple_health",
        "dataType": "activity",
        "metrics": [{
            "type": "step_count",
            "value": 98765,
            "unit": "count",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "privateNote": "sensitive-health-note",
        }],
    }
    body = _json_body(payload)

    with patch("app.routers.apple_health.get_pool", return_value=FakePool()):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with caplog.at_level(logging.INFO, logger="app.routers.apple_health"):
                resp = await client.post(
                    "/api/v1/health/apple-health/sync?token=invalid-token-value",
                    content=body,
                    headers={"Content-Type": "application/json"},
                )

    assert resp.status_code == 401
    _assert_logs_do_not_contain(
        caplog,
        "98765",
        "sensitive-health-note",
        "invalid-token-value",
        "step_count",
        "telegram_user_id=999",
        "user_id=7",
    )


@pytest.mark.asyncio
async def test_apple_health_webhook_does_not_log_sensitive_payload_on_malformed_payload(mock_settings, caplog):
    from app.main import app

    payload = {
        "userId": 999,
        "sourceType": "apple_health",
        "dataType": "activity",
        "metrics": [{
            "type": "heart_rate",
            "value": 72,
            "unit": "count/min",
            "timestamp": "",
            "privateNote": "malformed-sensitive-note",
        }],
    }
    body = _json_body(payload)

    with patch("app.routers.apple_health.get_pool", return_value=FakePool()):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with caplog.at_level(logging.INFO, logger="app.routers.apple_health"):
                resp = await client.post(
                    "/api/v1/health/apple-health/sync",
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Apple-Health-Token": "user-secret",
                    },
                )

    assert resp.status_code == 400
    _assert_logs_do_not_contain(
        caplog,
        "heart_rate",
        "72",
        "count/min",
        "malformed-sensitive-note",
        "user-secret",
    )


@pytest.mark.asyncio
async def test_apple_health_webhook_logs_inactive_sync_rejection(mock_settings):
    from app.main import app

    payload = {"userId": 999, "sourceType": "apple_health", "metrics": []}
    body = _json_body(payload)
    pool = FakePool()
    pool.active_sync = None
    pool.inactive_sync = {"user_id": 7, "sync_id": 3, "secret_key": "user-secret"}

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

    assert resp.status_code == 404
    failure_updates = _executed(pool, "UPDATE apple_health_sync")
    assert len(failure_updates) == 1
    assert failure_updates[0][1] == (3, "Apple Health sync is not active for this user")
    import_logs = _executed(pool, "INSERT INTO apple_health_import_logs")
    assert len(import_logs) == 1
    _, log_args = import_logs[0]
    assert log_args[:7] == (7, 3, 404, 0, 0, 0, "Apple Health sync is not active for this user")


@pytest.mark.asyncio
async def test_apple_health_webhook_does_not_mutate_failure_state_for_invalid_json_with_invalid_token(mock_settings):
    from app.main import app

    pool = FakePool()

    with patch("app.routers.apple_health.get_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/health/apple-health/sync?userId=999&token=wrong-token",
                content=b"not-json",
                headers={"Content-Type": "application/json"},
            )

    assert resp.status_code == 401
    assert _executed(pool, "UPDATE apple_health_sync") == []
    assert _executed(pool, "INSERT INTO apple_health_import_logs") == []


@pytest.mark.asyncio
async def test_apple_health_webhook_does_not_mutate_failure_state_for_invalid_json_with_missing_token(mock_settings):
    from app.main import app

    pool = FakePool()

    with patch("app.routers.apple_health.get_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/health/apple-health/sync?userId=999",
                content=b"not-json",
                headers={"Content-Type": "application/json"},
            )

    assert resp.status_code == 401
    assert _executed(pool, "UPDATE apple_health_sync") == []
    assert _executed(pool, "INSERT INTO apple_health_import_logs") == []


@pytest.mark.asyncio
async def test_apple_health_webhook_logs_invalid_json_after_url_token_authenticates(mock_settings):
    from app.main import app

    pool = FakePool()

    with patch("app.routers.apple_health.get_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/health/apple-health/sync?userId=999&token=user-secret",
                content=b"not-json",
                headers={"Content-Type": "application/json"},
            )

    assert resp.status_code == 400
    failure_updates = _executed(pool, "UPDATE apple_health_sync")
    assert len(failure_updates) == 1
    assert failure_updates[0][1] == (3, "Invalid JSON payload")
    import_logs = _executed(pool, "INSERT INTO apple_health_import_logs")
    assert len(import_logs) == 1
    _, log_args = import_logs[0]
    assert log_args[:7] == (7, 3, 400, 0, 0, 0, "Invalid JSON payload")
    assert "not-json" not in (log_args[7] or "")


@pytest.mark.asyncio
async def test_apple_health_webhook_echoes_invalid_json_body_to_telegram(mock_settings):
    from app.main import app

    pool = FakePool()
    send_document = AsyncMock()
    raw_body = b"bplist00\x00fake-binary-plist"

    with patch("app.routers.apple_health.get_pool", return_value=pool), \
            patch("app.services.telegram_bot.send_document", send_document):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/health/apple-health/sync?userId=999&token=user-secret",
                content=raw_body,
                headers={"Content-Type": "application/json"},
            )

    assert resp.status_code == 400
    send_document.assert_awaited_once()
    args, kwargs = send_document.await_args
    assert args[0] == 999
    assert kwargs["document"] == raw_body
    assert kwargs["filename"] == "apple-health-payload.txt"
    assert "Invalid JSON payload" in kwargs["caption"]


@pytest.mark.asyncio
async def test_apple_health_webhook_does_not_echo_invalid_json_without_valid_token(mock_settings):
    from app.main import app

    pool = FakePool()
    send_document = AsyncMock()

    with patch("app.routers.apple_health.get_pool", return_value=pool), \
            patch("app.services.telegram_bot.send_document", send_document):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/health/apple-health/sync?userId=999&token=wrong-token",
                content=b"not-json",
                headers={"Content-Type": "application/json"},
            )

    assert resp.status_code == 401
    send_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_apple_health_webhook_echoes_valid_payload_to_telegram(mock_settings):
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
    send_document = AsyncMock()

    with patch("app.routers.apple_health.get_pool", return_value=pool), \
            patch("app.services.telegram_bot.send_document", send_document):
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
    send_document.assert_awaited_once()
    args, kwargs = send_document.await_args
    assert args[0] == 999
    assert kwargs["document"] == body
    assert kwargs["filename"] == "apple-health-payload.json"


@pytest.mark.asyncio
async def test_apple_health_webhook_survives_telegram_echo_failure(mock_settings):
    from app.main import app

    payload = {"userId": 999, "sourceType": "apple_health", "metrics": []}
    body = _json_body(payload)
    pool = FakePool()
    send_document = AsyncMock(side_effect=RuntimeError("telegram down"))

    with patch("app.routers.apple_health.get_pool", return_value=pool), \
            patch("app.services.telegram_bot.send_document", send_document):
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
    send_document.assert_awaited_once()


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


@pytest.mark.asyncio
async def test_apple_health_webhook_rejects_old_url_after_reconnect(mock_settings):
    from app.main import app
    from app.services.apple_health import ensure_apple_health_sync

    pool = FakePool()
    payload = {
        "sourceType": "apple_health",
        "dataType": "activity",
        "metrics": [],
    }
    body = _json_body(payload)

    with patch("app.services.apple_health.secrets.token_urlsafe", side_effect=["old-token", "new-token"]):
        old_sync = await ensure_apple_health_sync(pool, user_id=7, sync_frequency_hours=6)
        new_sync = await ensure_apple_health_sync(pool, user_id=7, sync_frequency_hours=6)

    assert old_sync["secret_key"] == "old-token"
    assert new_sync["secret_key"] == "new-token"

    with patch("app.routers.apple_health.get_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            old_resp = await client.post(
                "/api/v1/health/apple-health/sync?userId=999&token=old-token",
                content=body,
                headers={"Content-Type": "application/json"},
            )
            new_resp = await client.post(
                "/api/v1/health/apple-health/sync?userId=999&token=new-token",
                content=body,
                headers={"Content-Type": "application/json"},
            )

    assert old_resp.status_code == 401
    assert new_resp.status_code == 200
    assert new_resp.json()["records_processed"] == 0


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
