import json
import logging
import plistlib
from datetime import datetime, timedelta, timezone
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


def test_parse_decimal_accepts_shortcuts_text_renders(mock_settings):
    from decimal import Decimal

    from app.services.apple_health import _parse_decimal

    assert _parse_decimal(434, "metric value") == Decimal("434")
    assert _parse_decimal("434", "metric value") == Decimal("434")
    assert _parse_decimal("434 count", "metric value") == Decimal("434")
    assert _parse_decimal("68.5", "metric value") == Decimal("68.5")
    assert _parse_decimal("68,5", "metric value") == Decimal("68.5")
    assert _parse_decimal("5 037", "metric value") == Decimal("5037")
    assert _parse_decimal("5 037 count", "metric value") == Decimal("5037")
    assert _parse_decimal("-2.5 kg", "metric value") == Decimal("-2.5")


def test_parse_decimal_rejects_non_numeric_values(mock_settings):
    from app.services.apple_health import AppleHealthIngestionError, _parse_decimal

    for bad in ("", "count", None, "5,037"):
        with pytest.raises(AppleHealthIngestionError, match="must be numeric"):
            _parse_decimal(bad, "metric value")


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


@pytest.mark.asyncio
async def test_apple_health_shortcut_download_hands_macos_users_off_to_iphone(
    mock_settings,
):
    from app.main import app

    macos_user_agent = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 Chrome/140.0 Safari/537.36"
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/health/apple-health/shortcut",
            headers={"User-Agent": macos_user_agent},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["vary"] == "User-Agent"
    assert response.headers["cache-control"] == "no-store"
    assert "не запускається на Mac" in response.text
    assert "Find Health Samples" in response.text
    assert not response.content.startswith(b"AEA1")


@pytest.mark.asyncio
async def test_apple_health_shortcut_download_keeps_iphone_supported(mock_settings):
    from app.main import app

    iphone_user_agent = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) "
        "AppleWebKit/605.1.15 Version/18.5 Mobile/15E148 Safari/604.1"
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/health/apple-health/shortcut",
            headers={"User-Agent": iphone_user_agent},
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/x-shortcut"
    assert response.headers["vary"] == "User-Agent"
    assert response.content.startswith(b"AEA1")


@pytest.mark.asyncio
async def test_apple_health_shortcut_download_keeps_ipad_desktop_user_agent_supported(
    mock_settings,
):
    from app.main import app

    ipad_desktop_user_agent = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) "
        "AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1"
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/health/apple-health/shortcut",
            headers={"User-Agent": ipad_desktop_user_agent},
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/x-shortcut"
    assert response.headers["vary"] == "User-Agent"
    assert response.content.startswith(b"AEA1")


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
    health_actions = [
        action
        for action in actions
        if action["WFWorkflowActionIdentifier"] == "is.workflow.actions.filter.health.quantity"
    ]
    repeat_starts = [
        action
        for action in actions
        if action["WFWorkflowActionIdentifier"] == "is.workflow.actions.repeat.each"
        and action["WFWorkflowActionParameters"]["WFControlFlowMode"] == 0
    ]

    def metric_fields(name: str) -> dict:
        metric_action = next(
            action
            for action in actions
            if action["WFWorkflowActionIdentifier"] == "is.workflow.actions.dictionary"
            and action["WFWorkflowActionParameters"].get("CustomOutputName") == name
        )
        metric_items = metric_action["WFWorkflowActionParameters"]["WFItems"]["Value"][
            "WFDictionaryFieldValueItems"
        ]
        return {_shortcut_key(item): item for item in metric_items}
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

    assert [
        repeat["WFWorkflowActionParameters"]["WFInput"]["Value"]["OutputUUID"]
        for repeat in repeat_starts
    ] == [
        health_action["WFWorkflowActionParameters"]["UUID"]
        for health_action in health_actions
    ]

    # Health sample content items expose "Value" (alongside Start Date, End
    # Date, Duration, Source, Name); "Quantity" is not a property and renders
    # as an empty string.
    sample_value = [{"Type": "WFPropertyVariableAggrandizement", "PropertyName": "Value"}]
    iso_start_date = [
        {"Type": "WFPropertyVariableAggrandizement", "PropertyName": "Start Date"},
        {
            "Type": "WFDateFormatVariableAggrandizement",
            "WFDateFormatStyle": "ISO 8601",
            "WFISO8601IncludeTime": True,
        },
    ]
    iso_end_date = [
        {"Type": "WFPropertyVariableAggrandizement", "PropertyName": "End Date"},
        {
            "Type": "WFDateFormatVariableAggrandizement",
            "WFDateFormatStyle": "ISO 8601",
            "WFISO8601IncludeTime": True,
        },
    ]

    def assert_repeat_item_field(field: dict, aggrandizements: list) -> None:
        attachment = _shortcut_token_attachment(field)
        assert attachment["Type"] == "Variable"
        assert attachment["VariableName"] == "Repeat Item"
        assert attachment["Aggrandizements"] == aggrandizements

    step_metric = metric_fields("Step Metric")
    assert _shortcut_text_value(step_metric["type"]) == "step_count"
    assert _shortcut_text_value(step_metric["unit"]) == "count"
    assert_repeat_item_field(step_metric["value"], sample_value)
    assert_repeat_item_field(step_metric["timestamp"], iso_start_date)

    energy_metric = metric_fields("Energy Metric")
    assert _shortcut_text_value(energy_metric["type"]) == "active_energy"
    assert _shortcut_text_value(energy_metric["unit"]) == "kcal"
    assert_repeat_item_field(energy_metric["value"], sample_value)
    assert_repeat_item_field(energy_metric["timestamp"], iso_start_date)

    # Sleep Value/Duration render as localized text in Shortcuts, so the sleep
    # metric ships value 0 with start/end ISO timestamps; the server derives
    # the duration from "end" and the localized stage text is diagnostic only.
    sleep_metric = metric_fields("Sleep Metric")
    assert _shortcut_text_value(sleep_metric["type"]) == "sleep_analysis"
    assert _shortcut_text_value(sleep_metric["value"]) == "0"
    assert _shortcut_text_value(sleep_metric["unit"]) == "s"
    assert_repeat_item_field(sleep_metric["timestamp"], iso_start_date)
    assert_repeat_item_field(sleep_metric["end"], iso_end_date)
    assert_repeat_item_field(sleep_metric["stage"], sample_value)

    hrv_metric = metric_fields("HRV Metric")
    assert _shortcut_text_value(hrv_metric["type"]) == "heart_rate_variability"
    assert _shortcut_text_value(hrv_metric["unit"]) == "ms"
    assert_repeat_item_field(hrv_metric["value"], sample_value)
    assert_repeat_item_field(hrv_metric["timestamp"], iso_start_date)

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
        "Type": "Variable",
        "VariableName": "Metrics",
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


def _natural_key_duration(duration_seconds):
    """Model health_data_natural_key's NULLS NOT DISTINCT: NULL durations must
    compare equal to each other (mapping them to a shared sentinel does that)."""
    return duration_seconds if duration_seconds is not None else -1


class FakePool:
    def __init__(self):
        self.executed = []
        self.fetchrow_calls = []
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
        if "INSERT INTO health_data" in query:
            if self.fail_health_data_insert:
                raise RuntimeError("database unavailable")
            self.executed.append((query, args))
            if self.force_insert_conflict:
                return None
            # Model the widened UNIQUE health_data_natural_key:
            # (user_id, source, metric_type, recorded_at, value,
            #  COALESCE(duration_seconds, -1)). ON CONFLICT DO NOTHING => None.
            key = (args[0], "apple_health", args[1], args[5], args[3], _natural_key_duration(args[6]))
            for row in self.health_rows:
                row_key = (
                    row["user_id"],
                    row["source"],
                    row["metric_type"],
                    row["recorded_at"],
                    row["value"],
                    _natural_key_duration(row.get("duration_seconds")),
                )
                if row_key == key:
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
                "duration_seconds": args[6],
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
        "records_failed": 0,
        "records_by_type": {"active_energy": 1, "step_count": 1},
        "unmapped_metric_types": [],
        "summary": "2 records received, 2 stored: 1 active energy, 1 steps",
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
async def test_ingest_apple_health_payload_skips_identical_resend(mock_settings):
    """An identical resend (same value AND duration) collides on the widened
    natural key and stays an idempotent no-op skip."""
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
        "duration_seconds": None,
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
        "records_failed": 0,
        "records_by_type": {"step_count": 1},
        "unmapped_metric_types": [],
        "summary": "1 records received, 0 stored, 1 skipped (duplicate): 1 steps",
    }


@pytest.mark.asyncio
async def test_ingest_stores_same_second_different_value_as_distinct_row(mock_settings):
    """The core fix: two samples sharing metric_type + second but with a
    different value are now stored as separate rows, not dropped as a conflict."""
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
        "duration_seconds": None,
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
        "records_processed": 1,
        "records_inserted": 1,
        "records_skipped": 0,
        "records_failed": 0,
        "records_by_type": {"step_count": 1},
        "unmapped_metric_types": [],
        "summary": "1 records received, 1 stored: 1 steps",
    }
    # Both the pre-existing 4000 sample and the new 5000 sample coexist.
    assert len(pool.health_rows) == 2
    assert {int(r["value"]) for r in pool.health_rows} == {4000, 5000}


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
        "records_failed": 0,
        "records_by_type": {"step_count": 1},
        "unmapped_metric_types": [],
        "summary": "1 records received, 0 stored, 1 skipped (duplicate): 1 steps",
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
async def test_apple_health_webhook_ingests_text_rendered_values(mock_settings):
    """The Shortcut posts the sample Value as localized text, e.g. "434 count"."""
    from app.main import app

    payload = {
        "userId": 999,
        "sourceType": "apple_health",
        "dataType": "activity",
        "metrics": [
            {
                "type": "step_count",
                "value": "434 count",
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
    inserts = _executed(pool, "INSERT INTO health_data")
    assert len(inserts) == 1


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


def test_plist_to_jsonable_normalizes_plist_only_types(mock_settings):
    from app.routers.apple_health import _plist_to_jsonable

    converted = _plist_to_jsonable(
        {
            "when": datetime(2026, 7, 10, 12, 30, 0),
            "note": b"hello",
            "blob": b"\xff\xfe",
            "nested": [{"deep": datetime(2026, 7, 9, 8, 0, 0)}],
            "count": 5,
        }
    )

    assert converted == {
        "when": "2026-07-10T12:30:00",
        "note": "hello",
        "blob": "//4=",
        "nested": [{"deep": "2026-07-09T08:00:00"}],
        "count": 5,
    }


@pytest.mark.asyncio
async def test_apple_health_webhook_accepts_binary_plist_payload(mock_settings):
    from app.main import app

    payload = {
        "sourceType": "apple_health",
        "dataType": "activity",
        "metrics": [
            {
                "type": "step_count",
                "value": 5000,
                "unit": "count",
                # Shortcuts encodes dates as native plist <date> values;
                # plistlib loads them back as naive UTC datetimes.
                "timestamp": datetime.now(timezone.utc).replace(tzinfo=None),
            }
        ],
    }
    body = plistlib.dumps(payload, fmt=plistlib.FMT_BINARY)
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
async def test_apple_health_webhook_accepts_xml_plist_payload(mock_settings):
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
    body = plistlib.dumps(payload, fmt=plistlib.FMT_XML)
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
async def test_apple_health_webhook_echoes_plist_payload_with_plist_filename(mock_settings):
    from app.main import app

    payload = {
        "sourceType": "apple_health",
        "dataType": "activity",
        "metrics": [
            {
                "type": "step_count",
                "value": 5000,
                "unit": "count",
                "timestamp": datetime.now(timezone.utc).replace(tzinfo=None),
            }
        ],
    }
    body = plistlib.dumps(payload, fmt=plistlib.FMT_BINARY)
    pool = FakePool()
    send_document = AsyncMock()

    with patch("app.routers.apple_health.get_pool", return_value=pool), \
            patch("app.services.telegram_bot.send_document", send_document):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/health/apple-health/sync?userId=999&token=user-secret",
                content=body,
                headers={"Content-Type": "application/json"},
            )

    assert resp.status_code == 200
    send_document.assert_awaited_once()
    args, kwargs = send_document.await_args
    assert args[0] == 999
    assert kwargs["document"] == body
    assert kwargs["filename"] == "apple-health-payload.plist"


@pytest.mark.asyncio
async def test_apple_health_webhook_rejects_body_that_is_neither_json_nor_plist(mock_settings):
    from app.main import app

    pool = FakePool()

    with patch("app.routers.apple_health.get_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/health/apple-health/sync?userId=999&token=user-secret",
                content=b"<?xml version=\"1.0\"?><note>not a plist</note>",
                headers={"Content-Type": "application/json"},
            )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid JSON payload"


@pytest.mark.asyncio
async def test_apple_health_webhook_rejects_plist_whose_root_is_not_a_dictionary(mock_settings):
    from app.main import app

    body = plistlib.dumps(["not", "a", "dictionary"], fmt=plistlib.FMT_XML)
    pool = FakePool()

    with patch("app.routers.apple_health.get_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/health/apple-health/sync?userId=999&token=user-secret",
                content=body,
                headers={"Content-Type": "application/json"},
            )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid JSON payload"


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


@pytest.mark.asyncio
async def test_ingest_apple_health_payload_derives_sleep_duration_from_end_timestamp(mock_settings):
    from app.services.apple_health import ingest_apple_health_payload

    pool = FakePool()
    payload = {
        "userId": 999,
        "sourceType": "apple_health",
        "dataType": "activity",
        "metrics": [
            {
                "type": "sleep_analysis",
                "value": "0",
                "unit": "s",
                "timestamp": "2026-05-19T23:04:00+03:00",
                "end": "2026-05-20T06:34:00+03:00",
                "stage": "Core",
            },
        ],
    }

    result = await ingest_apple_health_payload(
        pool,
        payload,
        now=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
    )

    assert result["records_inserted"] == 1
    inserts = _executed(pool, "INSERT INTO health_data")
    assert len(inserts) == 1
    _, args = inserts[0]
    assert args[1] == "sleep_analysis"
    # 23:04 → 06:34 is 7.5h, derived from the "end" field the Shortcut sends.
    assert args[6] == 27000
    additional_data = json.loads(args[7])
    assert additional_data["stage"] == "Core"
    assert additional_data["end"] == "2026-05-20T06:34:00+03:00"


def test_convert_health_auto_export_extracts_sleep_from_hour_buckets(mock_settings):
    from app.services.apple_health import convert_health_auto_export

    hae_payload = {
        "data": {
            "metrics": [
                {
                    "name": "sleep_analysis",
                    "units": "hr",
                    "data": [
                        {
                            "date": "2026-05-20 07:12:00 +0300",
                            "sleepStart": "2026-05-19 23:04:00 +0300",
                            "sleepEnd": "2026-05-20 07:12:00 +0300",
                            "asleep": 7.5,
                            "inBed": 8.1,
                        },
                    ],
                }
            ]
        }
    }

    result = convert_health_auto_export(hae_payload, telegram_user_id=42)

    assert result["metrics"] == [
        {
            "type": "sleep_analysis",
            "value": 7.5,
            "unit": "hr",
            "timestamp": "2026-05-19T23:04:00+03:00",
        }
    ]


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


# ---------------------------------------------------------------------------
# Ingestion completeness accounting + parsed-data summary
# ---------------------------------------------------------------------------

_REAL_PAYLOAD_PATH = Path(__file__).resolve().parent / "fixtures" / "apple-health-payload.json"


def _load_real_shortcut_payload() -> dict:
    payload = json.loads(_REAL_PAYLOAD_PATH.read_text(encoding="utf-8"))
    payload["userId"] = 999
    return payload


@pytest.mark.asyncio
async def test_ingest_accounts_for_every_record_in_real_shortcut_payload(mock_settings):
    """With the widened natural key (migration 008), every distinct metric in
    the real shortcut export is stored — the 9 same-second samples that used to
    collide now coexist as separate rows."""
    from app.services.apple_health import ingest_apple_health_payload

    payload = _load_real_shortcut_payload()
    pool = FakePool()

    # The sample spans 2026-07-08..2026-07-11; anchor "now" just after it so no
    # metric trips the 30-day-old / future-dated validation guards.
    result = await ingest_apple_health_payload(
        pool,
        payload,
        now=datetime(2026, 7, 12, 0, 0, tzinfo=timezone.utc),
    )

    # All 1239 land: the 6 step/energy pairs (different value) and 3 sleep pairs
    # (different duration) that shared a second are now distinct on
    # (..., recorded_at, value, COALESCE(duration_seconds, -1)).
    assert result["records_received"] == 1239
    assert result["records_inserted"] == 1239
    assert result["records_processed"] == 1239
    assert result["records_skipped"] == 0
    assert result["records_failed"] == 0

    # Completeness invariant: received is fully partitioned into
    # stored + skipped + failed.
    assert (
        result["records_received"]
        == result["records_inserted"] + result["records_skipped"] + result["records_failed"]
    )

    inserted_rows = [q for q, _ in pool.executed if "INSERT INTO health_data" in q]
    assert len(inserted_rows) == 1239
    assert len(pool.health_rows) == 1239

    # Prove the fix concretely: at least one (metric_type, recorded_at) second
    # now holds more than one stored row — impossible under the old key.
    from collections import Counter

    per_second = Counter(
        (row["metric_type"], row["recorded_at"]) for row in pool.health_rows
    )
    assert max(per_second.values()) == 2
    assert sum(1 for n in per_second.values() if n == 2) == 9

    assert result["records_by_type"] == {
        "step_count": 515,
        "active_energy": 509,
        "sleep_analysis": 215,
    }
    # All three types are aggregation-mapped, so nothing is flagged as unmapped.
    assert result["unmapped_metric_types"] == []


@pytest.mark.asyncio
async def test_ingest_logs_completeness_breakdown_in_import_log(mock_settings):
    """apple_health_import_logs captures received/processed/skipped columns and
    the by-type breakdown in response_body for the real payload."""
    from app.services.apple_health import ingest_apple_health_payload

    payload = _load_real_shortcut_payload()
    pool = FakePool()

    await ingest_apple_health_payload(
        pool,
        payload,
        now=datetime(2026, 7, 12, 0, 0, tzinfo=timezone.utc),
    )

    import_logs = _executed(pool, "INSERT INTO apple_health_import_logs")
    assert len(import_logs) == 1
    _, log_args = import_logs[0]
    # columns: user_id, sync_id, http_status, received, processed, failed, error,
    #          request_body, response_body, records_skipped
    assert log_args[:7] == (7, 3, 200, 1239, 1239, 0, None)
    assert log_args[9] == 0  # records_skipped column
    response_body = json.loads(log_args[8])
    assert response_body["records_skipped"] == 0
    assert response_body["records_by_type"] == {
        "step_count": 515,
        "active_energy": 509,
        "sleep_analysis": 215,
    }
    assert "1239 records received, 1239 stored" in response_body["summary"]


@pytest.mark.asyncio
async def test_ingest_returns_parsed_data_summary(mock_settings):
    from app.services.apple_health import ingest_apple_health_payload

    pool = FakePool()
    payload = {
        "userId": 999,
        "sourceType": "apple_health",
        "dataType": "activity",
        "metrics": [
            {"type": "step_count", "value": 100, "unit": "count", "timestamp": "2026-05-20T10:00:00+00:00"},
            {"type": "step_count", "value": 200, "unit": "count", "timestamp": "2026-05-20T10:01:00+00:00"},
            {"type": "heart_rate", "value": 70, "unit": "count/min", "timestamp": "2026-05-20T10:02:00+00:00"},
        ],
    }

    result = await ingest_apple_health_payload(
        pool,
        payload,
        now=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
    )

    assert result["records_by_type"] == {"step_count": 2, "heart_rate": 1}
    assert result["summary"] == "3 records received, 3 stored: 2 steps, 1 HR samples"


@pytest.mark.asyncio
async def test_ingest_flags_metric_types_the_aggregator_does_not_map(mock_settings):
    """Unmapped types are still stored, but called out so they are not assumed
    to feed the daily summary."""
    from app.services.apple_health import ingest_apple_health_payload

    pool = FakePool()
    payload = {
        "userId": 999,
        "sourceType": "apple_health",
        "dataType": "activity",
        "metrics": [
            {"type": "step_count", "value": 100, "unit": "count", "timestamp": "2026-05-20T10:00:00+00:00"},
            {"type": "blood_glucose", "value": 5.4, "unit": "mmol/L", "timestamp": "2026-05-20T10:05:00+00:00"},
        ],
    }

    result = await ingest_apple_health_payload(
        pool,
        payload,
        now=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
    )

    assert result["records_inserted"] == 2  # stored regardless of mapping
    assert result["unmapped_metric_types"] == ["blood_glucose"]
    assert "stored but not summarized: blood_glucose" in result["summary"]


def test_build_ingestion_summary_formats_counts_and_skips(mock_settings):
    from app.services.apple_health import build_ingestion_summary

    summary = build_ingestion_summary(
        {"step_count": 515, "active_energy": 509, "sleep_analysis": 215},
        received=1239,
        inserted=1230,
        skipped=9,
        failed=0,
    )
    assert summary == (
        "1239 records received, 1230 stored, 9 skipped (duplicate): "
        "515 steps, 509 active energy, 215 sleep"
    )


def test_build_ingestion_summary_handles_clean_import_and_unmapped(mock_settings):
    from app.services.apple_health import build_ingestion_summary

    summary = build_ingestion_summary(
        {"step_count": 10, "blood_glucose": 2},
        received=12,
        inserted=12,
        skipped=0,
        failed=0,
        unmapped_types=["blood_glucose"],
    )
    assert summary == (
        "12 records received, 12 stored: 10 steps, 2 blood glucose "
        "(stored but not summarized: blood_glucose)"
    )


@pytest.mark.asyncio
async def test_apple_health_webhook_sends_parsed_summary_to_telegram(mock_settings):
    from app.main import app

    payload = {
        "userId": 999,
        "sourceType": "apple_health",
        "dataType": "activity",
        "metrics": [
            {"type": "step_count", "value": 5000, "unit": "count",
             "timestamp": datetime.now(timezone.utc).isoformat()},
            {"type": "sleep_analysis", "value": "0", "unit": "s",
             "timestamp": (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat(),
             "end": datetime.now(timezone.utc).isoformat(), "stage": "Core"},
        ],
    }
    body = _json_body(payload)
    pool = FakePool()
    send_message = AsyncMock()

    with patch("app.routers.apple_health.get_pool", return_value=pool), \
            patch("app.services.telegram_bot.send_message", send_message):
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
    send_message.assert_awaited_once()
    args, _ = send_message.await_args
    assert args[0] == 999
    message = args[1]
    assert "Apple Health синхронізовано" in message
    assert "Отримано 2 записів, збережено 2" in message
    assert "1 кроки" in message
    assert "1 сон" in message


@pytest.mark.asyncio
async def test_apple_health_webhook_survives_summary_notification_failure(mock_settings):
    from app.main import app

    payload = {
        "userId": 999,
        "sourceType": "apple_health",
        "dataType": "activity",
        "metrics": [
            {"type": "step_count", "value": 5000, "unit": "count",
             "timestamp": datetime.now(timezone.utc).isoformat()},
        ],
    }
    body = _json_body(payload)
    pool = FakePool()
    send_message = AsyncMock(side_effect=RuntimeError("telegram down"))

    with patch("app.routers.apple_health.get_pool", return_value=pool), \
            patch("app.services.telegram_bot.send_message", send_message):
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
    send_message.assert_awaited_once()


class _SleepSummaryPool:
    """Minimal pool feeding get_apple_health_summary the rows a sync stored."""

    def __init__(self, health_rows):
        self._rows = health_rows

    async def fetchrow(self, query, *args):
        # No FatSecret / WHOOP tokens for this user in the sleep-only test.
        return None

    async def fetch(self, query, *args):
        assert "FROM health_data" in query
        return self._rows


@pytest.mark.asyncio
async def test_sleep_in_bed_and_awake_same_start_persist_and_feed_aggregation(mock_settings):
    """Regression: the 'In Bed' envelope + 'Awake' segment share a start
    timestamp with identical value/unit. Before the widened key the multi-hour
    In-Bed row was silently discarded as a duplicate, starving nightly sleep.
    Now both persist as distinct rows AND the aggregation sees the 8h envelope.
    """
    from app.services.apple_health import (
        _merged_interval_seconds,
        get_apple_health_summary,
        ingest_apple_health_payload,
    )

    sleep_start = "2026-07-11T05:00:00+00:00"
    payload = {
        "userId": 999,
        "sourceType": "apple_health",
        "dataType": "activity",
        "metrics": [
            {  # 20-minute Awake segment
                "type": "sleep_analysis", "value": "0", "unit": "s",
                "timestamp": sleep_start, "end": "2026-07-11T05:20:00+00:00",
                "stage": "Awake",
            },
            {  # 8-hour In Bed envelope sharing the same start
                "type": "sleep_analysis", "value": "0", "unit": "s",
                "timestamp": sleep_start, "end": "2026-07-11T13:00:00+00:00",
                "stage": "In Bed",
            },
        ],
    }

    pool = FakePool()
    result = await ingest_apple_health_payload(
        pool,
        payload,
        now=datetime(2026, 7, 12, 0, 0, tzinfo=timezone.utc),
    )

    # Both segments stored as distinct rows — neither dropped as a duplicate.
    assert result["records_inserted"] == 2
    assert result["records_skipped"] == 0
    assert result["records_received"] == result["records_inserted"] + result["records_skipped"] + result["records_failed"]
    assert len(pool.health_rows) == 2
    assert sorted(r["duration_seconds"] for r in pool.health_rows) == [1200, 28800]

    # The union of the overlapping intervals is the In-Bed envelope (8h), not
    # the 20-minute Awake segment.
    start = datetime(2026, 7, 11, 5, 0, tzinfo=timezone.utc)
    assert _merged_interval_seconds([
        (start, start + timedelta(minutes=20)),
        (start, start + timedelta(hours=8)),
    ]) == 8 * 3600

    # End-to-end: feed the stored rows through the nightly-sleep aggregation.
    summary_rows = [
        {
            "metric_type": r["metric_type"],
            "value": r["value"],
            "unit": r["unit"],
            "recorded_at": r["recorded_at"],
            "duration_seconds": r["duration_seconds"],
        }
        for r in pool.health_rows
    ]
    summary = await get_apple_health_summary(
        _SleepSummaryPool(summary_rows),
        7,
        start_at=datetime(2026, 7, 11, 0, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 7, 12, 0, 0, tzinfo=timezone.utc),
    )

    assert summary["sleep_hours"] == 8.0
    assert summary["metric_counts"]["sleep_analysis"] == 2
