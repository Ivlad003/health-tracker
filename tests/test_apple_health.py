import json
import logging
import plistlib
from datetime import date, datetime, timedelta, timezone
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


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity", float("nan")])
def test_parse_decimal_rejects_non_finite_values(mock_settings, bad):
    from app.services.apple_health import AppleHealthIngestionError, _parse_decimal

    with pytest.raises(AppleHealthIngestionError, match="must be finite"):
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
    # duration from "end" and maps known localized stage labels fail-safely.
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


# Compatibility layout returned by FakePool.aggregate_for(). Production writes
# one schema-v3 row per metric family; the fake folds those rows into this
# legacy daily shape so existing value assertions stay readable.
AGG_USER_ID = 0
AGG_METRIC_DATE = 1
AGG_TIMEZONE = 2
AGG_STEPS = 3
AGG_ACTIVE_ENERGY = 4
AGG_AVG_HR = 5
AGG_HR_SAMPLES = 6
AGG_AVG_HRV = 7
AGG_HRV_SAMPLES = 8
AGG_SLEEP_SECONDS = 9
AGG_SAMPLES_RECEIVED = 10
AGG_SAMPLES_AGGREGATED = 11
AGG_METRICS_JSON = 12
AGG_SNAPSHOT_GENERATED_AT = 13


class FakePool:
    """Fake asyncpg pool for schema-v3 metric-family aggregates."""

    def __init__(self):
        self.executed = []
        self.fetchrow_calls = []
        # Compatibility daily rows plus the authoritative family-row store.
        self.aggregates = {}
        self.metric_families = {}
        self.fail_aggregate_upsert = False
        self.apple_health_secret = "user-secret"
        self.active_sync = {"user_id": 7, "sync_id": 3, "secret_key": self.apple_health_secret}
        self.inactive_sync = None

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query, args))
        if "INSERT INTO health_daily_metric_aggregates" in query:
            if self.fail_aggregate_upsert:
                raise RuntimeError("database unavailable")
            self.executed.append((query, args))
            key = (args[0], args[1], args[2], args[3])
            existing = self.metric_families.get(key)
            if existing is None or (
                args[11] > existing[11] and (args[13] or args[12] != existing[12])
            ):
                self.metric_families[key] = args
                self._rebuild_daily_aggregates()
                return {"id": len(self.metric_families)}
            return None
        if "SELECT snapshot_generated_at, payload_hash" in query:
            existing = self.metric_families.get((args[0], args[1], args[2], args[3]))
            if existing is None:
                return None
            return {"snapshot_generated_at": existing[11], "payload_hash": existing[12]}
        if "SELECT total_value, average_value" in query:
            existing = self.metric_families.get((args[0], args[1], args[2], args[3]))
            if existing is None:
                return None
            return {
                "total_value": existing[5],
                "average_value": existing[6],
                "sample_count": existing[7],
                "samples_received": existing[8],
                "samples_aggregated": existing[9],
                "metrics": existing[10],
            }
        if "INSERT INTO apple_health_sync" in query:
            self.apple_health_secret = args[1]
            self.active_sync = {"user_id": 7, "sync_id": 3, "secret_key": self.apple_health_secret}
            return {"secret_key": args[1]}
        if "FROM users" in query and "apple_health_sync" in query and "ahs.is_active = TRUE" in query:
            return self.active_sync
        if "FROM users" in query and "apple_health_sync" in query:
            return self.inactive_sync or self.active_sync
        raise AssertionError(f"Unexpected fetchrow query: {query}")

    async def execute(self, query, *args):
        self.executed.append((query, args))
        return "OK"

    def _rebuild_daily_aggregates(self):
        daily = {}
        for (user_id, _collector, metric_date, family), args in self.metric_families.items():
            row = daily.setdefault(
                (user_id, metric_date),
                [user_id, metric_date, args[4], 0, 0, None, 0, None, 0, 0, 0, 0, {}, args[11]],
            )
            total, average = args[5], args[6]
            if family == "steps":
                row[AGG_STEPS] = int(total)
            elif family == "active_energy":
                row[AGG_ACTIVE_ENERGY] = total
            elif family == "heart_rate":
                row[AGG_AVG_HR] = average
                row[AGG_HR_SAMPLES] = args[7]
            elif family == "hrv":
                row[AGG_AVG_HRV] = average
                row[AGG_HRV_SAMPLES] = args[7]
            elif family == "sleep":
                row[AGG_SLEEP_SECONDS] = int(total)
            row[AGG_SAMPLES_RECEIVED] += args[8]
            row[AGG_SAMPLES_AGGREGATED] += args[9]
            details = json.loads(args[10]) if isinstance(args[10], str) else args[10]
            for metric_type, count in details.get("records_by_type", {}).items():
                row[AGG_METRICS_JSON][metric_type] = (
                    row[AGG_METRICS_JSON].get(metric_type, 0) + int(count)
                )
            row[AGG_SNAPSHOT_GENERATED_AT] = max(row[AGG_SNAPSHOT_GENERATED_AT], args[11])
        self.aggregates = {key: tuple(row) for key, row in daily.items()}

    # --- convenience accessors for assertions ---------------------------------
    def aggregate_upserts(self):
        return list(self.aggregates.values())

    def aggregate_for(self, day):
        for (uid, d), args in self.aggregates.items():
            if d == day:
                return args
        return None


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


def _today_covered() -> list[str]:
    """coveredDates covering the UTC 'today' the webhook's default now falls on."""
    return [datetime.now(timezone.utc).date().isoformat()]


def test_raw_payload_document_sender_is_not_available(mock_settings):
    from app.routers import apple_health as apple_health_router
    from app.services import telegram_bot

    assert not hasattr(apple_health_router, "_echo_payload_to_telegram")
    assert not hasattr(telegram_bot, "send_document")


def _envelope(
    metrics,
    covered_dates,
    *,
    tz="+00:00",
    user_id=999,
    data_type="activity",
    collector="shortcut",
    generated_at=None,
    covered_families=None,
    **extra,
):
    """Wrap metrics in the schema-v3 freshness/completeness envelope."""
    family_by_type = {
        "step_count": "steps",
        "steps": "steps",
        "active_energy": "active_energy",
        "heart_rate": "heart_rate",
        "heart_rate_variability": "hrv",
        "heart_rate_variability_sdnn": "hrv",
        "hrv": "hrv",
        "sleep": "sleep",
        "sleep_analysis": "sleep",
    }
    if covered_families is None:
        covered_families = []
        for metric in metrics:
            family = family_by_type.get(str(metric.get("type", "")).lower())
            if family and family not in covered_families:
                covered_families.append(family)
        if not covered_families:
            covered_families = ["steps", "active_energy", "sleep", "hrv"]
    if generated_at is None:
        candidates = []
        for metric in metrics:
            raw = metric.get("end") or metric.get("timestamp")
            if isinstance(raw, datetime):
                parsed = raw.replace(tzinfo=raw.tzinfo or timezone.utc)
            else:
                try:
                    parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                except (TypeError, ValueError):
                    continue
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
            candidates.append(parsed.astimezone(timezone.utc))
        generated_at = max(candidates).isoformat() if candidates else datetime.now(timezone.utc).isoformat()
    payload = {
        "userId": user_id,
        "sourceType": "apple_health",
        "schemaVersion": 3,
        "snapshot": {
            "collector": collector,
            "generatedAt": generated_at,
            "timezone": tz,
            "coveredDates": covered_dates,
            "coveredMetricFamilies": covered_families,
        },
        "metrics": metrics,
    }
    if data_type is not None:
        payload["dataType"] = data_type
    payload.update(extra)
    return payload


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
    payload = _envelope(
        [
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
        ["2026-05-20"],
        syncTimestamp="2026-05-20T12:00:00+00:00",
    )

    result = await ingest_apple_health_payload(
        pool,
        payload,
        now=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
    )

    assert result == {
        "schema_version": 3,
        "records_received": 2,
        "records_aggregated": 2,
        "aggregate_rows_updated": 2,
        "aggregate_rows_replayed": 0,
        "aggregate_rows_stale": 0,
        "raw_stored": 0,
        "records_failed": 0,
        "covered_dates": ["2026-05-20"],
        "covered_metric_families": ["active_energy", "steps"],
        "collector": "shortcut",
        "daily": {
            "2026-05-20": {
                "steps": 5000,
                "active_energy_kcal": 123.4,
                "avg_heart_rate": 0,
                "avg_hrv_ms": 0,
                "sleep_hours": 0,
                "samples_received": 2,
                "records_by_type": {"active_energy": 1, "step_count": 1},
            },
        },
        "records_by_type": {"active_energy": 1, "step_count": 1},
        "unmapped_metric_types": [],
        "summary": "2 samples received, 2 aggregated; 2 daily family rows updated, raw stored: 0: 1 active energy, 1 steps",
    }
    # One upsert into the aggregate table for the single covered day; nothing
    # goes to health_data any more.
    upserts = pool.aggregate_upserts()
    assert len(upserts) == 1
    assert not [query for query, _ in pool.executed if "INSERT INTO health_data" in query]
    agg = pool.aggregate_for(date(2026, 5, 20))
    assert agg[AGG_STEPS] == 5000
    assert float(agg[AGG_ACTIVE_ENERGY]) == 123.4
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
async def test_ingest_apple_health_payload_replay_leaves_daily_aggregate_unchanged(mock_settings):
    """Replaying an identical snapshot replaces the day's row with the same
    values — the aggregate is not double-counted (the old raw-row 'skip a
    duplicate' no-op becomes an idempotent upsert-replace)."""
    from app.services.apple_health import ingest_apple_health_payload

    recorded_at = datetime(2026, 5, 20, 11, 0, tzinfo=timezone.utc)
    pool = FakePool()
    payload = _envelope(
        [{
            "type": "step_count",
            "value": 5000,
            "unit": "count",
            "timestamp": recorded_at.isoformat(),
        }],
        ["2026-05-20"],
    )
    now = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)

    first = await ingest_apple_health_payload(pool, payload, now=now)
    first_agg = pool.aggregate_for(date(2026, 5, 20))
    # Replay the identical snapshot.
    second = await ingest_apple_health_payload(pool, payload, now=now)
    second_agg = pool.aggregate_for(date(2026, 5, 20))

    # Values unchanged across the replay: one day, 5000 steps both times.
    assert first["records_aggregated"] == 1
    assert first["aggregate_rows_updated"] == 1
    assert first["aggregate_rows_replayed"] == 0
    assert second["aggregate_rows_updated"] == 0
    assert second["aggregate_rows_replayed"] == 1
    assert first_agg[AGG_STEPS] == 5000
    assert second_agg[AGG_STEPS] == 5000
    # Still exactly one aggregate row for that day (replace, not accumulate).
    assert len([d for (uid, d) in pool.aggregates if d == date(2026, 5, 20)]) == 1


@pytest.mark.asyncio
async def test_health_auto_export_receipt_retry_does_not_advance_freshness(
    mock_settings,
):
    from app.services.apple_health import ingest_apple_health_payload

    metric = {
        "type": "step_count",
        "value": 5000,
        "unit": "count",
        "timestamp": "2026-05-20T09:00:00+00:00",
    }
    first = _envelope(
        [metric],
        ["2026-05-20"],
        collector="health_auto_export",
        generated_at="2026-05-20T10:00:00+00:00",
    )
    retry = _envelope(
        [metric],
        ["2026-05-20"],
        collector="health_auto_export",
        generated_at="2026-05-20T11:00:00+00:00",
    )
    first["snapshot"]["generatedAtProvenance"] = "receipt"
    retry["snapshot"]["generatedAtProvenance"] = "receipt"
    pool = FakePool()
    now = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)

    await ingest_apple_health_payload(pool, first, now=now)
    result = await ingest_apple_health_payload(pool, retry, now=now)

    stored = next(iter(pool.metric_families.values()))
    assert result["aggregate_rows_replayed"] == 1
    assert stored[11] == datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_explicit_newer_shortcut_snapshot_advances_even_when_content_matches(
    mock_settings,
):
    from app.services.apple_health import ingest_apple_health_payload

    native_metric = {
        "type": "step_count",
        "value": 5000,
        "unit": "count",
        "timestamp": "2026-05-20T09:00:00+00:00",
    }
    hae_metric = {**native_metric, "value": 6000}
    native_first = _envelope(
        [native_metric],
        ["2026-05-20"],
        collector="shortcut",
        generated_at="2026-05-20T10:00:00+00:00",
    )
    hae = _envelope(
        [hae_metric],
        ["2026-05-20"],
        collector="health_auto_export",
        generated_at="2026-05-20T10:30:00+00:00",
    )
    native_again = _envelope(
        [native_metric],
        ["2026-05-20"],
        collector="shortcut",
        generated_at="2026-05-20T11:00:00+00:00",
    )
    pool = FakePool()
    now = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)

    await ingest_apple_health_payload(pool, native_first, now=now)
    await ingest_apple_health_payload(pool, hae, now=now)
    result = await ingest_apple_health_payload(pool, native_again, now=now)

    native_stored = pool.metric_families[
        (7, "shortcut", date(2026, 5, 20), "steps")
    ]
    assert result["aggregate_rows_updated"] == 1
    assert native_stored[11] == datetime(2026, 5, 20, 11, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_ingest_sums_same_second_distinct_samples_into_daily_total(mock_settings):
    """Two samples sharing metric_type + second but with different values both
    contribute to the day's aggregate (there is no raw natural-key collision to
    drop one) — the day's steps SUM the two."""
    from app.services.apple_health import ingest_apple_health_payload

    recorded_at = datetime(2026, 5, 20, 11, 0, tzinfo=timezone.utc)
    pool = FakePool()
    payload = _envelope(
        [
            {"type": "step_count", "value": 4000, "unit": "count",
             "timestamp": recorded_at.isoformat()},
            {"type": "step_count", "value": 5000, "unit": "count",
             "timestamp": recorded_at.isoformat()},
        ],
        ["2026-05-20"],
    )

    result = await ingest_apple_health_payload(
        pool,
        payload,
        now=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
    )

    assert result["records_received"] == 2
    assert result["records_aggregated"] == 2
    assert result["records_by_type"] == {"step_count": 2}
    # Both same-second samples SUM into the single day's steps.
    agg = pool.aggregate_for(date(2026, 5, 20))
    assert agg[AGG_STEPS] == 9000  # 4000 + 5000, not collapsed to one
    assert agg[AGG_SAMPLES_RECEIVED] == 2
    assert result["daily"]["2026-05-20"]["steps"] == 9000


@pytest.mark.asyncio
async def test_ingest_apple_health_payload_newer_snapshot_replaces_same_day(mock_settings):
    """A later snapshot for the same day replaces (not increments) the day's
    aggregate — the DO UPDATE upsert overwrites the previous row."""
    from app.services.apple_health import ingest_apple_health_payload

    pool = FakePool()
    now = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)

    await ingest_apple_health_payload(
        pool,
        _envelope(
            [{"type": "step_count", "value": 5000, "unit": "count",
              "timestamp": "2026-05-20T09:00:00+00:00"}],
            ["2026-05-20"],
        ),
        now=now,
    )
    assert pool.aggregate_for(date(2026, 5, 20))[AGG_STEPS] == 5000

    # Newer, fuller snapshot for the same day.
    await ingest_apple_health_payload(
        pool,
        _envelope(
            [{"type": "step_count", "value": 12345, "unit": "count",
              "timestamp": "2026-05-20T11:00:00+00:00"}],
            ["2026-05-20"],
        ),
        now=now,
    )

    # Replaced, not added to the previous 5000.
    assert pool.aggregate_for(date(2026, 5, 20))[AGG_STEPS] == 12345
    assert len([d for (uid, d) in pool.aggregates if d == date(2026, 5, 20)]) == 1


@pytest.mark.asyncio
async def test_ingest_apple_health_payload_rejects_old_metrics(mock_settings):
    from app.services.apple_health import AppleHealthIngestionError, ingest_apple_health_payload

    payload = _envelope(
        [
            {
                "type": "step_count",
                "value": 5000,
                "unit": "count",
                "timestamp": "2026-04-01T11:00:00+00:00",
            }
        ],
        ["2026-05-20"],
    )

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
    # Nothing persisted for the rejected batch.
    assert pool.aggregate_upserts() == []


@pytest.mark.asyncio
async def test_validation_failure_does_not_persist_raw_metadata_value(mock_settings):
    from app.services.apple_health import (
        AppleHealthIngestionError,
        ingest_apple_health_payload,
    )

    private_value = "private-medical-note"
    payload = _envelope(
        [],
        ["2026-05-20"],
        tz=private_value,
    )
    pool = FakePool()

    with pytest.raises(AppleHealthIngestionError, match="snapshot timezone"):
        await ingest_apple_health_payload(
            pool,
            payload,
            now=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
        )

    persisted_arguments = repr([args for _query, args in pool.executed])
    assert private_value not in persisted_arguments


@pytest.mark.asyncio
async def test_persisted_failure_message_is_bounded(mock_settings):
    from app.services.apple_health import record_apple_health_failure

    pool = FakePool()
    await record_apple_health_failure(
        pool,
        user_id=7,
        sync_id=3,
        http_status=400,
        error_message="x" * 10_000,
    )

    update_args = _executed(pool, "UPDATE apple_health_sync")[0][1]
    log_args = _executed(pool, "INSERT INTO apple_health_import_logs")[0][1]
    assert len(update_args[1]) <= 256
    assert len(log_args[6]) <= 256
    assert len(json.loads(log_args[8])["error"]) <= 256


@pytest.mark.asyncio
async def test_ingest_apple_health_payload_logs_malformed_payload_after_sync_lookup(mock_settings):
    from app.services.apple_health import AppleHealthIngestionError, ingest_apple_health_payload

    pool = FakePool()
    # A valid completeness envelope but wrong sourceType: the envelope passes,
    # then the metric-container validation rejects it — still logged against the
    # looked-up sync.
    payload = _envelope(
        [],
        ["2026-05-20"],
        generated_at="2026-05-20T11:00:00+00:00",
    )
    payload["sourceType"] = "not_apple_health"

    with pytest.raises(AppleHealthIngestionError, match="sourceType must be apple_health"):
        await ingest_apple_health_payload(
            pool,
            payload,
            now=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
        )

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
    payload = _envelope(
        [
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
        ["2026-05-20"],
    )

    with pytest.raises(AppleHealthIngestionError, match="older than 30 days"):
        await ingest_apple_health_payload(
            pool,
            payload,
            now=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
        )

    # A single invalid metric rejects the whole batch: no aggregate row upserted
    # (and nothing ever goes to health_data).
    assert pool.aggregate_upserts() == []
    assert not [query for query, _ in pool.executed if "INSERT INTO health_data" in query]


@pytest.mark.asyncio
async def test_ingest_apple_health_payload_raises_when_aggregate_upsert_fails(mock_settings):
    """A database failure during the daily-aggregate upsert is recorded as a
    sanitized failure and surfaced; the sync is not marked successful."""
    from app.services.apple_health import (
        AppleHealthPersistenceError,
        ingest_apple_health_payload,
    )

    pool = FakePool()
    pool.fail_aggregate_upsert = True
    payload = _envelope(
        [{
            "type": "step_count",
            "value": 5000,
            "unit": "count",
            "timestamp": "2026-05-20T11:00:00+00:00",
        }],
        ["2026-05-20"],
    )

    with pytest.raises(AppleHealthPersistenceError, match="failed to persist"):
        await ingest_apple_health_payload(
            pool,
            payload,
            now=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
        )

    # A sanitized 500 failure import log is recorded, and the success bookkeeping
    # (last_sync_at / success_count) never runs.
    import_logs = _executed(pool, "INSERT INTO apple_health_import_logs")
    assert len(import_logs) == 1
    assert import_logs[0][1][2] == 500  # http_status
    assert _executed(pool, "last_sync_at") == []


@pytest.mark.asyncio
async def test_apple_health_webhook_maps_persistence_failure_to_sanitized_500(mock_settings):
    from app.main import app
    from app.services.apple_health import AppleHealthPersistenceError

    payload = _envelope(
        [{
            "type": "step_count",
            "value": 5000,
            "unit": "count",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }],
        _today_covered(),
    )
    pool = FakePool()
    persist = AsyncMock(
        side_effect=AppleHealthPersistenceError("database password must stay private")
    )

    with patch("app.routers.apple_health.get_pool", return_value=pool), \
            patch("app.routers.apple_health.ingest_apple_health_payload", persist):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/health/apple-health/sync",
                content=_json_body(payload),
                headers={
                    "Content-Type": "application/json",
                    "X-Apple-Health-Token": "user-secret",
                },
            )

    assert response.status_code == 500
    assert response.json() == {"detail": "Apple Health sync is temporarily unavailable"}
    assert "password" not in response.text


@pytest.mark.asyncio
async def test_apple_health_webhook_maps_snapshot_content_conflict_to_409(mock_settings):
    from app.main import app
    from app.services.apple_health import AppleHealthSnapshotConflictError

    payload = _envelope(
        [{
            "type": "step_count",
            "value": 5000,
            "unit": "count",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }],
        _today_covered(),
    )
    pool = FakePool()
    persist = AsyncMock(
        side_effect=AppleHealthSnapshotConflictError(
            "snapshot timestamp conflicts with different processed data"
        )
    )

    with patch("app.routers.apple_health.get_pool", return_value=pool), \
            patch("app.routers.apple_health.ingest_apple_health_payload", persist):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/health/apple-health/sync",
                content=_json_body(payload),
                headers={
                    "Content-Type": "application/json",
                    "X-Apple-Health-Token": "user-secret",
                },
            )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "snapshot timestamp conflicts with different processed data"
    }


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
    assert _executed(pool, "UPDATE apple_health_sync") == []
    assert _executed(pool, "INSERT INTO apple_health_import_logs") == []


@pytest.mark.asyncio
async def test_native_webhook_cannot_claim_health_auto_export_collector(mock_settings):
    from app.main import app

    payload = _envelope(
        [],
        _today_covered(),
        collector="health_auto_export",
        covered_families=["steps"],
    )
    pool = FakePool()

    with patch("app.routers.apple_health.get_pool", return_value=pool):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/health/apple-health/sync?userId=999&token=user-secret",
                content=_json_body(payload),
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 400
    assert "collector" in response.json()["detail"]
    assert pool.aggregate_upserts() == []


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
async def test_inactive_sync_rejects_invalid_token_without_mutating_state(mock_settings):
    from app.main import app

    payload = {"userId": 999, "sourceType": "apple_health", "metrics": []}
    pool = FakePool()
    pool.active_sync = None
    pool.inactive_sync = {"user_id": 7, "sync_id": 3, "secret_key": "user-secret"}

    with patch("app.routers.apple_health.get_pool", return_value=pool):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/health/apple-health/sync",
                content=_json_body(payload),
                headers={
                    "Content-Type": "application/json",
                    "X-Apple-Health-Token": "wrong-token",
                },
            )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid Apple Health token"}
    assert _executed(pool, "UPDATE apple_health_sync") == []
    assert _executed(pool, "INSERT INTO apple_health_import_logs") == []


@pytest.mark.asyncio
async def test_unknown_sync_uses_same_unauthenticated_response_without_state_mutation(
    mock_settings,
):
    from app.main import app

    payload = {"userId": 999, "sourceType": "apple_health", "metrics": []}
    pool = FakePool()
    pool.active_sync = None
    pool.inactive_sync = None

    with patch("app.routers.apple_health.get_pool", return_value=pool):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/health/apple-health/sync",
                content=_json_body(payload),
                headers={
                    "Content-Type": "application/json",
                    "X-Apple-Health-Token": "guessed-token",
                },
            )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid Apple Health token"}
    assert _executed(pool, "UPDATE apple_health_sync") == []
    assert _executed(pool, "INSERT INTO apple_health_import_logs") == []


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
async def test_invalid_json_for_unknown_sync_does_not_reveal_connector_state(
    mock_settings,
):
    from app.main import app

    pool = FakePool()
    pool.active_sync = None
    pool.inactive_sync = None

    with patch("app.routers.apple_health.get_pool", return_value=pool):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/health/apple-health/sync?userId=999&token=guessed-token",
                content=b"not-json",
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid Apple Health token"}
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
async def test_apple_health_webhook_maps_excessive_json_nesting_to_400(mock_settings):
    from app.main import app

    body = b"[" * 200_000 + b"0" + b"]" * 200_000
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/health/apple-health/sync",
            content=body,
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid JSON payload"}


@pytest.mark.asyncio
async def test_apple_health_webhook_maps_excessive_plist_nesting_to_400(mock_settings):
    from app.main import app

    depth = 1_200
    body = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<plist version="1.0"><dict><key>nested</key>'
        + b"<array>" * depth
        + b"<integer>0</integer>"
        + b"</array>" * depth
        + b"</dict></plist>"
    )
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/health/apple-health/sync",
            content=body,
            headers={"Content-Type": "application/x-plist"},
        )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid JSON payload"}


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
async def test_apple_health_webhook_never_sends_invalid_raw_body_to_telegram(mock_settings):
    from app.main import app

    pool = FakePool()
    send_message = AsyncMock()
    raw_body = b"bplist00\x00fake-binary-plist"

    with patch("app.routers.apple_health.get_pool", return_value=pool), \
            patch("app.services.telegram_bot.send_message", send_message):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/health/apple-health/sync?userId=999&token=user-secret",
                content=raw_body,
                headers={"Content-Type": "application/json"},
            )

    assert resp.status_code == 400
    send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_apple_health_webhook_does_not_notify_invalid_json_without_valid_token(mock_settings):
    from app.main import app

    pool = FakePool()
    send_message = AsyncMock()

    with patch("app.routers.apple_health.get_pool", return_value=pool), \
            patch("app.services.telegram_bot.send_message", send_message):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/health/apple-health/sync?userId=999&token=wrong-token",
                content=b"not-json",
                headers={"Content-Type": "application/json"},
            )

    assert resp.status_code == 401
    send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_apple_health_webhook_sends_only_sanitized_summary_to_telegram(mock_settings):
    from app.main import app

    payload = _envelope(
        [
            {
                "type": "step_count",
                "value": 5000,
                "unit": "count",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ],
        _today_covered(),
    )
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
    args, _kwargs = send_message.await_args
    assert args[0] == 999
    assert "Apple Health синхронізовано" in args[1]
    assert "5000" not in args[1]
    assert "schemaVersion" not in args[1]


@pytest.mark.asyncio
async def test_webhook_survives_summary_notification_failure_for_empty_snapshot(
    mock_settings,
):
    from app.main import app

    payload = _envelope([], _today_covered())
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
    send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_apple_health_webhook_ingests_valid_payload(mock_settings):
    from app.main import app

    payload = _envelope(
        [
            {
                "type": "step_count",
                "value": 5000,
                "unit": "count",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ],
        _today_covered(),
    )
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
    assert resp.json()["records_aggregated"] == 1


@pytest.mark.asyncio
async def test_apple_health_webhook_ingests_text_rendered_values(mock_settings):
    """The Shortcut posts the sample Value as localized text, e.g. "434 count"."""
    from app.main import app

    payload = _envelope(
        [
            {
                "type": "step_count",
                "value": "434 count",
                "unit": "count",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ],
        _today_covered(),
    )
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
    assert resp.json()["records_aggregated"] == 1
    # The text-rendered "434 count" is parsed and aggregated into one daily row.
    assert len(pool.aggregate_upserts()) == 1
    assert pool.aggregate_for(datetime.now(timezone.utc).date())[AGG_STEPS] == 434


@pytest.mark.asyncio
async def test_apple_health_webhook_accepts_user_and_token_from_url(mock_settings):
    from app.main import app

    payload = _envelope(
        [
            {
                "type": "step_count",
                "value": 5000,
                "unit": "count",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ],
        _today_covered(),
        user_id=None,
    )
    payload.pop("userId")
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
    assert resp.json()["records_aggregated"] == 1


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

    payload = _envelope(
        [{
            "type": "step_count",
            "value": 5000,
            "unit": "count",
            # Shortcuts encodes dates as native plist <date> values;
            # plistlib loads them back as naive UTC datetimes.
            "timestamp": datetime.now(timezone.utc).replace(tzinfo=None),
        }],
        _today_covered(),
    )
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
    assert resp.json()["records_aggregated"] == 1


@pytest.mark.asyncio
async def test_apple_health_webhook_accepts_xml_plist_payload(mock_settings):
    from app.main import app

    payload = _envelope(
        [{
            "type": "step_count",
            "value": 5000,
            "unit": "count",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }],
        _today_covered(),
    )
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
    assert resp.json()["records_aggregated"] == 1


@pytest.mark.asyncio
async def test_apple_health_webhook_never_sends_raw_plist_to_telegram(mock_settings):
    from app.main import app

    payload = _envelope(
        [{
            "type": "step_count",
            "value": 5000,
            "unit": "count",
            "timestamp": datetime.now(timezone.utc).replace(tzinfo=None),
        }],
        _today_covered(),
    )
    body = plistlib.dumps(payload, fmt=plistlib.FMT_BINARY)
    pool = FakePool()
    send_message = AsyncMock()

    with patch("app.routers.apple_health.get_pool", return_value=pool), \
            patch("app.services.telegram_bot.send_message", send_message):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/health/apple-health/sync?userId=999&token=user-secret",
                content=body,
                headers={"Content-Type": "application/json"},
            )

    assert resp.status_code == 200
    send_message.assert_awaited_once()
    args, _kwargs = send_message.await_args
    assert args[0] == 999
    assert isinstance(args[1], str)
    assert "5000" not in args[1]
    assert "bplist00" not in args[1]


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
async def test_apple_health_webhook_rejects_oversized_body_before_parse_or_auth(
    mock_settings,
):
    from app.main import app
    from app.routers import apple_health as apple_health_router

    get_pool = AsyncMock()
    with patch.object(apple_health_router, "MAX_APPLE_HEALTH_BODY_BYTES", 32), \
            patch("app.routers.apple_health.get_pool", get_pool):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/health/apple-health/sync?userId=999&token=user-secret",
                content=b"{" + b"x" * 64,
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 413
    assert response.json()["detail"] == "Apple Health payload is too large"
    get_pool.assert_not_awaited()


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
async def test_apple_health_webhook_rejects_json_whose_root_is_not_an_object(mock_settings):
    from app.main import app

    pool = FakePool()

    with patch("app.routers.apple_health.get_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/health/apple-health/sync?userId=999&token=user-secret",
                content=json.dumps(["not", "an", "object"]),
                headers={"Content-Type": "application/json"},
            )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid JSON payload"


@pytest.mark.asyncio
async def test_apple_health_webhook_rejects_old_url_after_reconnect(mock_settings):
    from app.main import app
    from app.services.apple_health import ensure_apple_health_sync

    pool = FakePool()
    payload = _envelope([], _today_covered())
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
    assert new_resp.json()["records_aggregated"] == 0


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
            ]
        }
    }

    result = convert_health_auto_export(
        hae_payload,
        telegram_user_id=42,
        automation_period="today",
        snapshot_timezone="+03:00",
        snapshot_generated_at="2026-05-20T17:00:00+03:00",
        now=datetime(2026, 5, 20, 18, 0, tzinfo=timezone.utc),
    )

    assert result["sourceType"] == "apple_health"
    assert result["dataType"] == "auto_export"
    assert result["userId"] == 42
    assert len(result["metrics"]) == 2
    assert result["metrics"][0] == {
        "type": "step_count",
        "value": 5000,
        "unit": "count",
        "timestamp": "2026-05-20T14:30:00+03:00",
    }


def test_convert_health_auto_export_rejects_multiple_metrics(mock_settings):
    from app.services.apple_health import (
        AppleHealthIngestionError,
        convert_health_auto_export,
    )

    with pytest.raises(AppleHealthIngestionError, match="exactly one metric"):
        convert_health_auto_export(
            {
                "data": {
                    "metrics": [
                        {"name": "step_count", "units": "count", "data": []},
                        {"name": "heart_rate", "units": "bpm", "data": []},
                    ]
                }
            },
            telegram_user_id=42,
            automation_period="today",
            snapshot_timezone="UTC",
            snapshot_generated_at="2026-05-20T00:00:00Z",
            now=datetime(2026, 5, 20, tzinfo=timezone.utc),
        )


@pytest.mark.asyncio
async def test_ingest_apple_health_payload_derives_sleep_duration_from_end_timestamp(mock_settings):
    from app.services.apple_health import ingest_apple_health_payload

    pool = FakePool()
    # Sleep is attributed to the local day it ENDS (2026-05-20 in +03:00).
    payload = _envelope(
        [
            {
                "type": "sleep_analysis",
                "value": "0",
                "unit": "s",
                "timestamp": "2026-05-19T23:04:00+03:00",
                "end": "2026-05-20T06:34:00+03:00",
                "stage": "Core",
            },
        ],
        ["2026-05-20"],
        tz="+03:00",
    )

    result = await ingest_apple_health_payload(
        pool,
        payload,
        now=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
    )

    assert result["records_aggregated"] == 1
    # 23:04 → 06:34 is 7.5h, derived from the "end" field the Shortcut sends,
    # aggregated into the ending day's sleep_seconds.
    agg = pool.aggregate_for(date(2026, 5, 20))
    assert agg is not None
    assert agg[AGG_SLEEP_SECONDS] == 27000
    assert result["daily"]["2026-05-20"]["sleep_hours"] == 7.5
    assert result["daily"]["2026-05-20"]["records_by_type"] == {"sleep_analysis": 1}


def test_convert_health_auto_export_rejects_aggregated_sleep(mock_settings):
    from app.services.apple_health import (
        AppleHealthIngestionError,
        convert_health_auto_export,
    )

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

    with pytest.raises(AppleHealthIngestionError, match="unaggregated segments"):
        convert_health_auto_export(
            hae_payload,
            telegram_user_id=42,
            automation_period="today",
            snapshot_timezone="+03:00",
            snapshot_generated_at="2026-05-20T11:00:00Z",
            now=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
        )


@pytest.mark.asyncio
async def test_health_auto_export_unaggregated_sleep_preserves_stage_and_end(
    mock_settings,
):
    from app.services.apple_health import (
        convert_health_auto_export,
        ingest_apple_health_payload,
    )

    hae_payload = {
        "data": {
            "metrics": [
                {
                    "name": "sleep_analysis",
                    "units": "hr",
                    "data": [
                        {
                            "startDate": "2026-05-19 21:00:00 +0300",
                            "endDate": "2026-05-19 22:00:00 +0300",
                            "qty": 1,
                            "value": "Unspecified",
                        },
                        {
                            "startDate": "2026-05-19 22:00:00 +0300",
                            "endDate": "2026-05-19 23:00:00 +0300",
                            "qty": 1,
                            "value": "Core",
                        },
                        {
                            "startDate": "2026-05-19 23:00:00 +0300",
                            "endDate": "2026-05-19 23:30:00 +0300",
                            "qty": 0.5,
                            "value": "Awake",
                        },
                        {
                            "startDate": "2026-05-19 22:00:00 +0300",
                            "endDate": "2026-05-20 00:00:00 +0300",
                            "qty": 2,
                            "value": "In Bed",
                        },
                    ],
                }
            ]
        }
    }
    now = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)

    payload = convert_health_auto_export(
        hae_payload,
        telegram_user_id=999,
        automation_period="default",
        snapshot_timezone="+03:00",
        snapshot_generated_at="2026-05-20T11:00:00Z",
        now=now,
    )

    assert [metric["stage"] for metric in payload["metrics"]] == [
        "Unspecified",
        "Core",
        "Awake",
        "In Bed",
    ]
    assert [metric["end"] for metric in payload["metrics"]] == [
        "2026-05-19T22:00:00+03:00",
        "2026-05-19T23:00:00+03:00",
        "2026-05-19T23:30:00+03:00",
        "2026-05-20T00:00:00+03:00",
    ]

    result = await ingest_apple_health_payload(FakePool(), payload, now=now)

    # Once an asleep stage is present, Awake and In Bed are context only and
    # must not inflate the stored sleep total.
    assert result["daily"]["2026-05-19"]["sleep_hours"] == 2.0


def test_health_auto_export_uses_explicit_export_timestamp(
    mock_settings,
):
    from app.services.apple_health import convert_health_auto_export

    receipt_time = datetime(2026, 5, 21, 9, 30, tzinfo=timezone.utc)
    payload = {
        "data": {
            "metrics": [
                {
                    "name": "step_count",
                    "units": "count",
                    "data": [{"date": "2026-05-20 14:30:00 +0000", "qty": 1}],
                }
            ]
        }
    }

    converted = convert_health_auto_export(
        payload,
        telegram_user_id=42,
        automation_period="yesterday",
        snapshot_timezone="UTC",
        snapshot_generated_at="2026-05-21T09:00:00Z",
        now=receipt_time,
    )

    assert converted["snapshot"]["generatedAt"] == "2026-05-21T09:00:00+00:00"
    assert converted["snapshot"]["generatedAtProvenance"] == "export"
    assert converted["snapshot"]["generatedAtByFamilyDate"] == {
        "steps": {"2026-05-20": "2026-05-21T09:00:00+00:00"}
    }


def test_health_auto_export_none_period_maps_to_default_coverage(mock_settings):
    from app.services.apple_health import convert_health_auto_export

    converted = convert_health_auto_export(
        {
            "data": {
                "metrics": [
                    {
                        "name": "step_count",
                        "units": "count",
                        "data": [
                            {"date": "2026-05-20 14:30:00 +0000", "qty": 1}
                        ],
                    }
                ]
            }
        },
        telegram_user_id=42,
        automation_period="none",
        snapshot_timezone="UTC",
        snapshot_generated_at="2026-05-20T17:00:00Z",
        now=datetime(2026, 5, 20, 18, 0, tzinfo=timezone.utc),
    )

    assert converted["snapshot"]["coveredDatesByFamily"] == {
        "steps": ["2026-05-19", "2026-05-20"]
    }


def test_health_auto_export_empty_snapshot_uses_explicit_export_time(
    mock_settings,
):
    from app.services.apple_health import convert_health_auto_export

    converted = convert_health_auto_export(
        {
            "data": {
                "metrics": [
                    {"name": "step_count", "units": "count", "data": []}
                ]
            }
        },
        telegram_user_id=42,
        automation_period="today",
        snapshot_timezone="UTC",
        snapshot_generated_at="2026-05-20T10:00:00Z",
        now=datetime(2026, 5, 20, 11, 0, tzinfo=timezone.utc),
    )

    assert converted["metrics"] == []
    assert converted["snapshot"]["coveredDatesByFamily"] == {
        "steps": ["2026-05-20"]
    }
    assert converted["snapshot"]["generatedAt"] == "2026-05-20T10:00:00+00:00"


@pytest.mark.asyncio
async def test_delayed_older_health_auto_export_cannot_overwrite_newer_snapshot(
    mock_settings,
):
    from app.services.apple_health import (
        convert_health_auto_export,
        ingest_apple_health_payload,
    )

    now = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)

    def export(points, generated_at):
        return convert_health_auto_export(
            {
                "data": {
                    "metrics": [
                        {
                            "name": "step_count",
                            "units": "count",
                            "data": points,
                        }
                    ]
                }
            },
            telegram_user_id=999,
            automation_period="today",
            snapshot_timezone="UTC",
            snapshot_generated_at=generated_at,
            now=now,
        )

    newer_after_deletion = export(
        [{"date": "2026-05-20 09:00:00 +0000", "qty": 1000}],
        "2026-05-20T11:00:00Z",
    )
    delayed_pre_deletion = export(
        [
            {"date": "2026-05-20 09:00:00 +0000", "qty": 1000},
            {"date": "2026-05-20 10:00:00 +0000", "qty": 200},
        ],
        "2026-05-20T10:30:00Z",
    )
    pool = FakePool()

    await ingest_apple_health_payload(pool, newer_after_deletion, now=now)
    result = await ingest_apple_health_payload(
        pool,
        delayed_pre_deletion,
        now=now,
    )

    assert result["aggregate_rows_stale"] == 1
    assert result["daily"]["2026-05-20"]["steps"] == 1000


@pytest.mark.asyncio
async def test_health_auto_export_same_export_time_changed_content_conflicts(
    mock_settings,
):
    from app.services.apple_health import (
        AppleHealthSnapshotConflictError,
        convert_health_auto_export,
        ingest_apple_health_payload,
    )

    now = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)

    def export(qty):
        return convert_health_auto_export(
            {
                "data": {
                    "metrics": [
                        {
                            "name": "step_count",
                            "units": "count",
                            "data": [
                                {
                                    "date": "2026-05-20 09:00:00 +0000",
                                    "qty": qty,
                                }
                            ],
                        }
                    ]
                }
            },
            telegram_user_id=999,
            automation_period="today",
            snapshot_timezone="UTC",
            snapshot_generated_at="2026-05-20T10:00:00Z",
            now=now,
        )

    pool = FakePool()
    await ingest_apple_health_payload(pool, export(1000), now=now)

    with pytest.raises(AppleHealthSnapshotConflictError):
        await ingest_apple_health_payload(pool, export(1200), now=now)

    assert pool.aggregate_for(date(2026, 5, 20))[AGG_STEPS] == 1000


@pytest.mark.asyncio
async def test_health_auto_export_complete_period_clears_missing_day(
    mock_settings,
):
    from app.services.apple_health import (
        convert_health_auto_export,
        ingest_apple_health_payload,
    )

    now = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)

    def export(points, generated_at):
        return convert_health_auto_export(
            {
                "data": {
                    "metrics": [
                        {"name": "step_count", "units": "count", "data": points}
                    ]
                }
            },
            telegram_user_id=999,
            automation_period="default",
            snapshot_timezone="UTC",
            snapshot_generated_at=generated_at,
            now=now,
        )

    pool = FakePool()
    await ingest_apple_health_payload(
        pool,
        export(
            [
                {"date": "2026-05-19 09:00:00 +0000", "qty": 1000},
                {"date": "2026-05-20 09:00:00 +0000", "qty": 100},
            ],
            "2026-05-20T09:30:00Z",
        ),
        now=now,
    )
    result = await ingest_apple_health_payload(
        pool,
        export(
            [{"date": "2026-05-20 10:00:00 +0000", "qty": 200}],
            "2026-05-20T10:30:00Z",
        ),
        now=now,
    )

    assert result["daily"]["2026-05-19"]["steps"] == 0
    assert result["daily"]["2026-05-20"]["steps"] == 200


@pytest.mark.asyncio
async def test_health_auto_export_empty_snapshot_clears_existing_day(mock_settings):
    from app.services.apple_health import (
        convert_health_auto_export,
        ingest_apple_health_payload,
    )

    now = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)

    def export(points, generated_at):
        return convert_health_auto_export(
            {
                "data": {
                    "metrics": [
                        {"name": "step_count", "units": "count", "data": points}
                    ]
                }
            },
            telegram_user_id=999,
            automation_period="today",
            snapshot_timezone="UTC",
            snapshot_generated_at=generated_at,
            now=now,
        )

    pool = FakePool()
    await ingest_apple_health_payload(
        pool,
        export(
            [{"date": "2026-05-20 09:00:00 +0000", "qty": 1000}],
            "2026-05-20T09:30:00Z",
        ),
        now=now,
    )
    result = await ingest_apple_health_payload(
        pool,
        export([], "2026-05-20T10:30:00Z"),
        now=now,
    )

    assert result["daily"]["2026-05-20"]["steps"] == 0


@pytest.mark.asyncio
async def test_health_auto_export_active_energy_kj_is_stored_as_kcal(mock_settings):
    from app.services.apple_health import (
        convert_health_auto_export,
        ingest_apple_health_payload,
    )

    now = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
    payload = convert_health_auto_export(
        {
            "data": {
                "metrics": [
                    {
                        "name": "active_energy",
                        "units": "kJ",
                        "data": [
                            {
                                "date": "2026-05-20 10:00:00 +0000",
                                "qty": 418.4,
                            }
                        ],
                    }
                ]
            }
        },
        telegram_user_id=999,
        automation_period="today",
        snapshot_timezone="UTC",
        snapshot_generated_at="2026-05-20T11:00:00Z",
        now=now,
    )

    result = await ingest_apple_health_payload(FakePool(), payload, now=now)

    assert result["daily"]["2026-05-20"]["active_energy_kcal"] == 100.0


def test_health_auto_export_preserves_observed_offset_per_family_date(mock_settings):
    from app.services.apple_health import convert_health_auto_export

    converted = convert_health_auto_export(
        {
            "data": {
                "metrics": [
                    {
                        "name": "step_count",
                        "units": "count",
                        "data": [
                            {"date": "2026-07-11 09:00:00 -0700", "qty": 4321}
                        ],
                    }
                ]
            }
        },
        telegram_user_id=42,
        automation_period="today",
        snapshot_timezone="-07:00",
        snapshot_generated_at="2026-07-11T17:00:00Z",
        now=datetime(2026, 7, 12, tzinfo=timezone.utc),
    )

    assert converted["snapshot"]["timezone"] == "-07:00"
    assert converted["snapshot"]["coveredTimezonesByFamilyDate"] == {
        "steps": {"2026-07-11": "-07:00"}
    }


def test_sanitized_request_summary_never_copies_nested_or_unbounded_values(
    mock_settings,
):
    from app.services.apple_health import _sanitized_request_summary

    summary = _sanitized_request_summary(
        {
            "sourceType": {"raw": ["sensitive health payload"]},
            "dataType": "x" * 500,
            "syncTimestamp": ["not", "a", "scalar"],
            "metrics": [{}, {}],
        }
    )

    assert summary == {"dataType": "x" * 128, "metrics_count": 2}


def test_convert_health_auto_export_rejects_invalid_points(mock_settings):
    from app.services.apple_health import (
        AppleHealthIngestionError,
        convert_health_auto_export,
    )

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

    with pytest.raises(
        AppleHealthIngestionError,
        match="requires qty and timestamp",
    ):
        convert_health_auto_export(
            hae_payload,
            telegram_user_id=42,
            automation_period="today",
            snapshot_timezone="+03:00",
            snapshot_generated_at="2026-05-20T00:00:00Z",
            now=datetime(2026, 5, 20, tzinfo=timezone.utc),
        )


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
                headers={
                    "Content-Type": "application/json",
                    "automation-period": "today",
                    "automation-aggregation": "none",
                    "X-Health-Tracker-HAE-Mode": (
                        "complete-unbatched-unaggregated-single-metric-v1"
                    ),
                    "X-Health-Tracker-Timezone": "UTC",
                    "X-Health-Tracker-Generated-At": datetime.now(
                        timezone.utc
                    ).isoformat(),
                },
            )

    assert resp.status_code == 200
    # HAE conversion synthesizes schema-v3 period coverage; both same-second
    # raw points are received and aggregated into one day.
    body_json = resp.json()
    assert body_json["records_received"] == 2
    assert body_json["records_aggregated"] == 2
    assert body_json["aggregate_rows_updated"] == 1
    # Both step samples SUM into the day's total (no raw dedup).
    assert pool.aggregate_for(datetime.now(timezone.utc).date())[AGG_STEPS] == 6200


@pytest.mark.asyncio
async def test_health_auto_export_grouped_aggregation_fails_before_ingest(
    mock_settings,
):
    from app.main import app

    payload = {
        "data": {
            "metrics": [
                {
                    "name": "step_count",
                    "units": "count",
                    "data": [
                        {
                            "date": datetime.now(timezone.utc).strftime(
                                "%Y-%m-%d %H:%M:%S +0000"
                            ),
                            "qty": 1000,
                        }
                    ],
                }
            ]
        }
    }
    pool = FakePool()

    with patch("app.routers.apple_health.get_pool", return_value=pool):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v1/health/apple-health/sync?userId=999&token=user-secret",
                content=_json_body(payload),
                headers={
                    "Content-Type": "application/json",
                    "automation-period": "today",
                    "automation-aggregation": "hours",
                    "X-Health-Tracker-HAE-Mode": (
                        "complete-unbatched-unaggregated-single-metric-v1"
                    ),
                    "X-Health-Tracker-Timezone": "UTC",
                    "X-Health-Tracker-Generated-At": datetime.now(
                        timezone.utc
                    ).isoformat(),
                },
            )

    assert response.status_code == 400
    assert "unaggregated" in response.json()["detail"]
    assert pool.metric_families == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "generated_at",
    [None, "2026-07-13T12:00:00", "2999-01-01T00:00:00Z"],
)
async def test_health_auto_export_requires_valid_causal_export_time(
    mock_settings,
    generated_at,
):
    from app.main import app

    now = datetime.now(timezone.utc)
    payload = {
        "data": {
            "metrics": [
                {
                    "name": "step_count",
                    "units": "count",
                    "data": [
                        {
                            "date": now.strftime("%Y-%m-%d %H:%M:%S +0000"),
                            "qty": 1000,
                        }
                    ],
                }
            ]
        }
    }
    headers = {
        "Content-Type": "application/json",
        "automation-period": "today",
        "automation-aggregation": "none",
        "X-Health-Tracker-HAE-Mode": (
            "complete-unbatched-unaggregated-single-metric-v1"
        ),
        "X-Health-Tracker-Timezone": "UTC",
    }
    if generated_at is not None:
        headers["X-Health-Tracker-Generated-At"] = generated_at
    pool = FakePool()

    with patch("app.routers.apple_health.get_pool", return_value=pool):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v1/health/apple-health/sync?userId=999&token=user-secret",
                content=_json_body(payload),
                headers=headers,
            )

    assert response.status_code == 400
    assert pool.metric_families == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    ["multiple_metrics", "aggregated_sleep"],
)
async def test_health_auto_export_ambiguous_payload_fails_before_ingest(
    mock_settings,
    case,
):
    from app.main import app

    metric = {"name": "step_count", "units": "count", "data": []}
    if case == "multiple_metrics":
        metrics = [metric, {"name": "heart_rate", "units": "bpm", "data": []}]
    elif case == "aggregated_sleep":
        metrics = [
            {
                "name": "sleep_analysis",
                "units": "hr",
                "data": [
                    {
                        "date": datetime.now(timezone.utc).isoformat(),
                        "sleepStart": datetime.now(timezone.utc).isoformat(),
                        "sleepEnd": datetime.now(timezone.utc).isoformat(),
                        "asleep": 7.5,
                    }
                ],
            }
        ]
    pool = FakePool()

    with patch("app.routers.apple_health.get_pool", return_value=pool):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v1/health/apple-health/sync?userId=999&token=user-secret",
                content=_json_body({"data": {"metrics": metrics}}),
                headers={
                    "Content-Type": "application/json",
                    "automation-period": "today",
                    "automation-aggregation": "none",
                    "X-Health-Tracker-HAE-Mode": (
                        "complete-unbatched-unaggregated-single-metric-v1"
                    ),
                    "X-Health-Tracker-Timezone": "UTC",
                    "X-Health-Tracker-Generated-At": datetime.now(
                        timezone.utc
                    ).isoformat(),
                },
            )

    assert response.status_code == 400
    assert pool.metric_families == {}


@pytest.mark.asyncio
async def test_health_auto_export_since_last_sync_cannot_overwrite_daily_total(
    mock_settings,
):
    from app.main import app

    iso_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S +0000")
    pool = FakePool()

    def payload(qty):
        return {
            "data": {
                "metrics": [
                    {
                        "name": "step_count",
                        "units": "count",
                        "data": [{"date": iso_now, "qty": qty}],
                    }
                ]
            }
        }

    with patch("app.routers.apple_health.get_pool", return_value=pool):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            complete = await client.post(
                "/api/v1/health/apple-health/sync?userId=999&token=user-secret",
                content=_json_body(payload(1000)),
                headers={
                    "Content-Type": "application/json",
                    "automation-period": "today",
                    "automation-aggregation": "none",
                    "X-Health-Tracker-HAE-Mode": (
                        "complete-unbatched-unaggregated-single-metric-v1"
                    ),
                    "X-Health-Tracker-Timezone": "UTC",
                    "X-Health-Tracker-Generated-At": datetime.now(
                        timezone.utc
                    ).isoformat(),
                },
            )
            incremental = await client.post(
                "/api/v1/health/apple-health/sync?userId=999&token=user-secret",
                content=_json_body(payload(50)),
                headers={
                    "Content-Type": "application/json",
                    "automation-period": "lastsync",
                    "automation-aggregation": "none",
                    "X-Health-Tracker-HAE-Mode": (
                        "complete-unbatched-unaggregated-single-metric-v1"
                    ),
                    "X-Health-Tracker-Timezone": "UTC",
                    "X-Health-Tracker-Generated-At": datetime.now(
                        timezone.utc
                    ).isoformat(),
                },
            )

    assert complete.status_code == 200
    assert incremental.status_code == 400
    assert "complete, unbatched" in incremental.json()["detail"]
    assert pool.aggregate_for(datetime.now(timezone.utc).date())[AGG_STEPS] == 1000


@pytest.mark.asyncio
async def test_health_auto_export_requires_unbatched_attestation_header(mock_settings):
    from app.main import app

    payload = {"data": {"metrics": []}}
    pool = FakePool()

    with patch("app.routers.apple_health.get_pool", return_value=pool):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v1/health/apple-health/sync?userId=999&token=user-secret",
                content=_json_body(payload),
                headers={
                    "Content-Type": "application/json",
                    "automation-period": "today",
                },
            )

    assert response.status_code == 400
    assert "complete, unbatched" in response.json()["detail"]


@pytest.mark.asyncio
async def test_health_auto_export_non_list_points_map_to_400(mock_settings):
    from app.main import app

    payload = {
        "data": {
            "metrics": [
                {
                    "name": "step_count",
                    "units": "count",
                    "data": 1,
                }
            ]
        }
    }
    pool = FakePool()

    with patch("app.routers.apple_health.get_pool", return_value=pool):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v1/health/apple-health/sync?userId=999&token=user-secret",
                content=_json_body(payload),
                headers={
                    "Content-Type": "application/json",
                    "automation-period": "today",
                    "automation-aggregation": "none",
                    "X-Health-Tracker-HAE-Mode": (
                        "complete-unbatched-unaggregated-single-metric-v1"
                    ),
                    "X-Health-Tracker-Timezone": "UTC",
                    "X-Health-Tracker-Generated-At": datetime.now(
                        timezone.utc
                    ).isoformat(),
                },
            )

    assert response.status_code == 400
    assert response.json() == {
        "detail": "Health Auto Export metric data must be a list"
    }


@pytest.mark.asyncio
async def test_health_auto_export_conversion_error_maps_to_400(mock_settings):
    from app.main import app

    payload = {
        "timezone": "Not/A-Timezone",
        "data": {
            "metrics": [
                {
                    "name": "step_count",
                    "units": "count",
                    "data": [
                        {
                            "date": datetime.now(timezone.utc).isoformat(),
                            "qty": 1,
                        }
                    ],
                }
            ]
        },
    }
    pool = FakePool()

    with patch("app.routers.apple_health.get_pool", return_value=pool):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v1/health/apple-health/sync?userId=999&token=user-secret",
                content=_json_body(payload),
                headers={
                    "Content-Type": "application/json",
                    "automation-period": "today",
                    "automation-aggregation": "none",
                    "X-Health-Tracker-HAE-Mode": (
                        "complete-unbatched-unaggregated-single-metric-v1"
                    ),
                    "X-Health-Tracker-Timezone": "Not/A-Timezone",
                    "X-Health-Tracker-Generated-At": datetime.now(
                        timezone.utc
                    ).isoformat(),
                },
            )

    assert response.status_code == 400
    assert "timezone" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Ingestion completeness accounting + parsed-data summary
# ---------------------------------------------------------------------------

_SYNTHETIC_PAYLOAD_PATH = Path(__file__).resolve().parent / "fixtures" / "apple-health-payload.json"


def _load_synthetic_shortcut_payload() -> dict:
    payload = json.loads(_SYNTHETIC_PAYLOAD_PATH.read_text(encoding="utf-8"))
    payload["userId"] = 999
    return payload


@pytest.mark.asyncio
async def test_ingest_accounts_for_every_record_in_synthetic_shortcut_payload(mock_settings):
    """Every metric in the synthetic export is aggregated into a family row;
    the same-second samples that used to collide on the raw natural key now all
    contribute to their day's totals (raw retention is 0)."""
    from app.services.apple_health import ingest_apple_health_payload

    payload = _load_synthetic_shortcut_payload()
    pool = FakePool()
    expected_received = len(payload["metrics"])
    expected_counts = {
        metric_type: sum(1 for metric in payload["metrics"] if metric["type"] == metric_type)
        for metric_type in {metric["type"] for metric in payload["metrics"]}
    }
    expected_family_rows = sum(
        len(dates) for dates in payload["snapshot"]["coveredDatesByFamily"].values()
    )

    # The samples span 2026-07-09..2026-07-11 in +03:00; anchor "now" just after
    # so no metric trips the 30-day-old / future-dated validation guards.
    result = await ingest_apple_health_payload(
        pool,
        payload,
        now=datetime(2026, 7, 12, 0, 0, tzinfo=timezone.utc),
    )

    assert result["records_received"] == expected_received
    assert result["records_aggregated"] == expected_received
    assert result["records_failed"] == 0
    assert result["raw_stored"] == 0

    # One aggregate upsert per covered family/day; no health_data writes at all.
    assert result["aggregate_rows_updated"] == expected_family_rows
    assert result["covered_dates"] == ["2026-07-09", "2026-07-10", "2026-07-11"]
    assert len(pool.aggregate_upserts()) == 3
    assert not [q for q, _ in pool.executed if "INSERT INTO health_data" in q]

    # Every received sample is accounted for in a day's samples_received.
    total_received = sum(
        args[AGG_SAMPLES_RECEIVED] for args in pool.aggregate_upserts()
    )
    assert total_received == expected_received

    # Same-second distinct samples all contribute rather than dropping one:
    # Two same-second step samples on 2026-07-11 both contribute.
    assert pool.aggregate_for(date(2026, 7, 11))[AGG_STEPS] == 325

    assert result["records_by_type"] == expected_counts
    assert result["unmapped_metric_types"] == []


@pytest.mark.asyncio
async def test_ingest_logs_completeness_breakdown_in_import_log(mock_settings):
    """apple_health_import_logs captures received/processed/failed columns (9
    columns, no records_skipped) and the by-type breakdown in response_body."""
    from app.services.apple_health import ingest_apple_health_payload

    payload = _load_synthetic_shortcut_payload()
    pool = FakePool()
    expected_received = len(payload["metrics"])
    expected_family_rows = sum(
        len(dates) for dates in payload["snapshot"]["coveredDatesByFamily"].values()
    )

    await ingest_apple_health_payload(
        pool,
        payload,
        now=datetime(2026, 7, 12, 0, 0, tzinfo=timezone.utc),
    )

    import_logs = _executed(pool, "INSERT INTO apple_health_import_logs")
    assert len(import_logs) == 1
    _, log_args = import_logs[0]
    # 9 columns: user_id, sync_id, http_status, received, processed, failed,
    #            error, request_body, response_body (no records_skipped column).
    assert len(log_args) == 9
    assert log_args[:7] == (7, 3, 200, expected_received, expected_received, 0, None)
    response_body = json.loads(log_args[8])
    assert response_body["records_aggregated"] == expected_received
    assert response_body["raw_stored"] == 0
    assert response_body["aggregate_rows_updated"] == expected_family_rows
    assert response_body["records_by_type"] == {
        metric_type: sum(1 for metric in payload["metrics"] if metric["type"] == metric_type)
        for metric_type in {metric["type"] for metric in payload["metrics"]}
    }
    assert f"{expected_received} samples received" in response_body["summary"]


@pytest.mark.asyncio
async def test_ingest_returns_parsed_data_summary(mock_settings):
    from app.services.apple_health import ingest_apple_health_payload

    pool = FakePool()
    payload = _envelope(
        [
            {"type": "step_count", "value": 100, "unit": "count", "timestamp": "2026-05-20T10:00:00+00:00"},
            {"type": "step_count", "value": 200, "unit": "count", "timestamp": "2026-05-20T10:01:00+00:00"},
            {"type": "heart_rate", "value": 70, "unit": "count/min", "timestamp": "2026-05-20T10:02:00+00:00"},
        ],
        ["2026-05-20"],
    )

    result = await ingest_apple_health_payload(
        pool,
        payload,
        now=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
    )

    assert result["records_by_type"] == {"step_count": 2, "heart_rate": 1}
    assert result["summary"] == (
        "3 samples received, 3 aggregated; 2 daily family rows updated, raw stored: 0: "
        "2 steps, 1 HR samples"
    )


@pytest.mark.asyncio
async def test_ingest_flags_metric_types_the_aggregator_does_not_map(mock_settings):
    """Unmapped types are counted for diagnostics but never persisted into an
    unrelated metric-family row."""
    from app.services.apple_health import ingest_apple_health_payload

    pool = FakePool()
    payload = _envelope(
        [
            {"type": "step_count", "value": 100, "unit": "count", "timestamp": "2026-05-20T10:00:00+00:00"},
            {"type": "blood_glucose", "value": 5.4, "unit": "mmol/L", "timestamp": "2026-05-20T10:05:00+00:00"},
        ],
        ["2026-05-20"],
    )

    result = await ingest_apple_health_payload(
        pool,
        payload,
        now=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
    )

    assert result["records_received"] == 2
    assert result["records_aggregated"] == 1
    assert result["records_by_type"] == {"step_count": 1, "blood_glucose": 1}
    assert result["unmapped_metric_types"] == ["blood_glucose"]
    assert "unsupported and not stored: blood_glucose" in result["summary"]


def test_build_ingestion_summary_formats_counts_and_aggregate_rows(mock_settings):
    from app.services.apple_health import build_ingestion_summary

    summary = build_ingestion_summary(
        {"step_count": 515, "active_energy": 509, "sleep_analysis": 215},
        received=1239,
        aggregated=1239,
        failed=0,
        aggregate_rows=3,
    )
    assert summary == (
        "1239 samples received, 1239 aggregated; 3 daily family rows updated, raw stored: 0: "
        "515 steps, 509 active energy, 215 sleep"
    )


def test_build_ingestion_summary_handles_clean_import_and_unmapped(mock_settings):
    from app.services.apple_health import build_ingestion_summary

    summary = build_ingestion_summary(
        {"step_count": 10, "blood_glucose": 2},
        received=12,
        aggregated=12,
        failed=0,
        aggregate_rows=1,
        unmapped_types=["blood_glucose"],
    )
    assert summary == (
        "12 samples received, 12 aggregated; 1 daily family row updated, raw stored: 0: "
        "10 steps, 2 blood glucose "
        "(unsupported and not stored: blood_glucose)"
    )


@pytest.mark.asyncio
async def test_apple_health_webhook_sends_parsed_summary_to_telegram(mock_settings):
    from app.main import app

    payload = _envelope(
        [
            {"type": "step_count", "value": 5000, "unit": "count",
             "timestamp": datetime.now(timezone.utc).isoformat()},
            {"type": "sleep_analysis", "value": "0", "unit": "s",
             "timestamp": (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat(),
             "end": datetime.now(timezone.utc).isoformat(), "stage": "Core"},
        ],
        _today_covered(),
    )
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
    assert "Отримано 2 семплів, агреговано 2" in message
    assert "Рядків сімейств оновлено: 2" in message
    assert "1 кроки" in message
    assert "1 сон" in message


@pytest.mark.asyncio
async def test_webhook_survives_summary_notification_failure_with_metrics(
    mock_settings,
):
    from app.main import app

    payload = _envelope(
        [
            {"type": "step_count", "value": 5000, "unit": "count",
             "timestamp": datetime.now(timezone.utc).isoformat()},
        ],
        _today_covered(),
    )
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
    assert resp.json()["records_aggregated"] == 1
    send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_sleep_in_bed_and_awake_same_start_merge_into_daily_aggregate(mock_settings):
    """The 'In Bed' envelope + 'Awake' segment share a start timestamp with
    identical value/unit. Both contribute to the day's sleep breakdown, and the
    aggregated sleep_seconds is the union of the overlapping intervals (the 8h
    In-Bed envelope), not the sum — so a night is never double-counted nor
    starved down to the 20-minute Awake blip.
    """
    from app.services.apple_health import (
        _merged_interval_seconds,
        ingest_apple_health_payload,
    )

    sleep_start = "2026-07-11T05:00:00+00:00"
    payload = _envelope(
        [
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
        ["2026-07-11"],
    )

    pool = FakePool()
    result = await ingest_apple_health_payload(
        pool,
        payload,
        now=datetime(2026, 7, 12, 0, 0, tzinfo=timezone.utc),
    )

    # Both segments contribute to the day's breakdown — neither dropped.
    assert result["records_aggregated"] == 2
    assert result["records_by_type"] == {"sleep_analysis": 2}
    assert result["daily"]["2026-07-11"]["records_by_type"] == {"sleep_analysis": 2}

    # With no asleep-stage interval, the fallback is In Bed minus Awake.
    agg = pool.aggregate_for(date(2026, 7, 11))
    assert agg[AGG_SLEEP_SECONDS] == 7 * 3600 + 40 * 60
    assert result["daily"]["2026-07-11"]["sleep_hours"] == 7.7

    # The merge helper it relies on, exercised directly.
    start = datetime(2026, 7, 11, 5, 0, tzinfo=timezone.utc)
    assert _merged_interval_seconds([
        (start, start + timedelta(minutes=20)),
        (start, start + timedelta(hours=8)),
    ]) == 8 * 3600
