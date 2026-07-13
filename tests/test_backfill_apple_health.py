from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest


CREATED_AT = datetime(2026, 7, 13, 9, 30, tzinfo=timezone.utc)


class _Transaction:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        self.conn.transaction_entered = True
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.conn.transaction_rolled_back = exc_type is not None
        return False


class BackfillConnection:
    def __init__(self, *, deleted_ids: list[int] | None = None):
        self.raw_rows = [
            {
                "id": 11,
                "metric_type": "step_count",
                "metric_subtype": "activity",
                "value": 125,
                "unit": "count",
                "recorded_at": datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc),
                "duration_seconds": None,
                "additional_data": {},
                "created_at": CREATED_AT,
            }
        ]
        self.deleted_ids = [11] if deleted_ids is None else deleted_ids
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []
        self.execute_calls: list[tuple[str, tuple]] = []
        self.closed = False
        self.transaction_entered = False
        self.transaction_rolled_back = False

    def transaction(self):
        return _Transaction(self)

    async def fetch(self, query, *args):
        self.fetch_calls.append((query, args))
        if "FROM health_data" in query and "SELECT id" in query:
            return self.raw_rows
        if "FROM health_daily_metric_aggregates" in query:
            return [{"metric_date": date(2026, 7, 13), "metric_family": "steps"}]
        if "DELETE FROM health_data" in query:
            return [{"id": raw_id} for raw_id in self.deleted_ids]
        raise AssertionError(f"Unexpected fetch query: {query}")

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query, args))
        if "INSERT INTO health_daily_metric_aggregates" in query:
            return {"id": 101}
        raise AssertionError(f"Unexpected fetchrow query: {query}")

    async def fetchval(self, query, *args):
        if "COUNT(*)" in query and "FROM health_data" in query:
            return 0
        raise AssertionError(f"Unexpected fetchval query: {query}")

    async def execute(self, query, *args):
        self.execute_calls.append((query, args))
        return "OK"

    async def close(self):
        self.closed = True


def test_cli_defaults_to_kyiv_and_nondestructive_mode(mock_settings):
    from app.backfill_apple_health import DEFAULT_BACKFILL_TIMEZONE, parse_args

    args = parse_args([])

    assert DEFAULT_BACKFILL_TIMEZONE == "Europe/Kyiv"
    assert args.timezone == "Europe/Kyiv"
    assert args.delete_raw is False


def test_backfill_normalizes_legacy_active_energy_kj_to_kcal(mock_settings):
    from app.backfill_apple_health import _rows_to_normalized

    rows = [
        {
            "metric_type": "active_energy",
            "metric_subtype": "auto_export",
            "value": Decimal("418.4"),
            "unit": "kJ",
            "recorded_at": datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc),
            "duration_seconds": None,
            "additional_data": {},
        }
    ]

    normalized = _rows_to_normalized(rows)

    assert normalized[0]["value"] == Decimal("100")
    assert normalized[0]["unit"] == "kcal"


@pytest.mark.asyncio
async def test_backfill_writes_v3_legacy_family_and_retains_raw_by_default(mock_settings):
    from app.backfill_apple_health import backfill_user

    conn = BackfillConnection()

    result = await backfill_user(conn, 7)

    raw_query, raw_args = conn.fetch_calls[0]
    assert "SELECT id" in raw_query
    assert "FOR UPDATE" in raw_query
    assert raw_args == (7,)
    assert not any("DELETE FROM health_data" in query for query, _ in conn.fetch_calls)

    insert_query, insert_args = conn.fetchrow_calls[0]
    assert "INSERT INTO health_daily_metric_aggregates" in insert_query
    assert insert_args[1] == "legacy_backfill"
    assert insert_args[2] == date(2026, 7, 13)
    assert insert_args[3] == "steps"
    assert insert_args[4] == "Europe/Kyiv"
    assert insert_args[11] == CREATED_AT
    assert result["deleted"] == 0
    assert result["retained"] == 1


@pytest.mark.asyncio
async def test_destructive_backfill_deletes_only_selected_ids(mock_settings):
    from app.backfill_apple_health import backfill_user

    conn = BackfillConnection()

    result = await backfill_user(conn, 7, delete_raw=True)

    delete_query, delete_args = next(
        (query, args) for query, args in conn.fetch_calls if "DELETE FROM health_data" in query
    )
    assert "id = ANY($2::integer[])" in delete_query
    assert delete_args == (7, [11])
    assert result["deleted"] == 1
    assert result["retained"] == 0


@pytest.mark.asyncio
async def test_destructive_backfill_rolls_back_on_deleted_id_mismatch(mock_settings):
    from app.backfill_apple_health import backfill_user

    conn = BackfillConnection(deleted_ids=[])

    with pytest.raises(RuntimeError, match="selected raw-row IDs"):
        await backfill_user(conn, 7, delete_raw=True)

    assert conn.transaction_rolled_back is True


@pytest.mark.asyncio
async def test_destructive_backfill_refuses_unmapped_raw_rows(mock_settings):
    from app.backfill_apple_health import AppleHealthBackfillError, backfill_user

    conn = BackfillConnection()
    conn.raw_rows[0]["metric_type"] = "unsupported_historical_metric"

    with pytest.raises(AppleHealthBackfillError, match="unsupported raw metric"):
        await backfill_user(conn, 7, delete_raw=True)

    assert conn.transaction_rolled_back is True
    assert not any("DELETE FROM health_data" in query for query, _ in conn.fetch_calls)


@pytest.mark.asyncio
async def test_backfill_rejects_nonfinite_supported_raw_values(mock_settings):
    from app.backfill_apple_health import backfill_user
    from app.services.apple_health import AppleHealthIngestionError

    conn = BackfillConnection()
    conn.raw_rows[0]["value"] = Decimal("NaN")

    with pytest.raises(AppleHealthIngestionError, match="must be finite"):
        await backfill_user(conn, 7)

    assert conn.transaction_rolled_back is True


@pytest.mark.asyncio
async def test_run_backfill_raises_on_per_user_failure_and_closes_connection(
    mock_settings, monkeypatch
):
    import app.backfill_apple_health as backfill

    conn = BackfillConnection()

    async def fake_connect(*, dsn):
        return conn

    async def fake_backfill_all(conn_arg, *, timezone_str, delete_raw):
        assert conn_arg is conn
        return [], [{"user_id": 7, "error": "broken row"}]

    monkeypatch.setattr(backfill.asyncpg, "connect", fake_connect)
    monkeypatch.setattr(backfill, "backfill_all", fake_backfill_all)

    with pytest.raises(backfill.AppleHealthBackfillError, match="user 7"):
        await backfill.run_backfill()

    assert conn.closed is True


@pytest.mark.asyncio
async def test_destructive_run_fails_when_raw_rows_remain(mock_settings, monkeypatch):
    import app.backfill_apple_health as backfill

    conn = BackfillConnection()

    async def fake_connect(*, dsn):
        return conn

    async def fake_backfill_all(conn_arg, *, timezone_str, delete_raw):
        assert delete_raw is True
        return [
            {
                "user_id": 7,
                "raw_rows": 1,
                "aggregate_rows": 1,
                "preserved_existing": 0,
                "deleted": 1,
                "retained": 0,
            }
        ], []

    async def one_raw_row_remains(query, *args):
        assert "source = 'apple_health'" in query
        return 1

    conn.fetchval = one_raw_row_remains
    monkeypatch.setattr(backfill.asyncpg, "connect", fake_connect)
    monkeypatch.setattr(backfill, "backfill_all", fake_backfill_all)

    with pytest.raises(backfill.AppleHealthBackfillError, match="1 raw row"):
        await backfill.run_backfill(delete_raw=True)

    assert conn.closed is True


@pytest.mark.asyncio
async def test_destructive_run_holds_writer_blocking_lock_through_residual_check(
    mock_settings, monkeypatch
):
    import app.backfill_apple_health as backfill

    conn = BackfillConnection()

    async def fake_connect(*, dsn):
        return conn

    async def fake_backfill_all(conn_arg, *, timezone_str, delete_raw):
        assert conn_arg is conn
        assert delete_raw is True
        return [], []

    monkeypatch.setattr(backfill.asyncpg, "connect", fake_connect)
    monkeypatch.setattr(backfill, "backfill_all", fake_backfill_all)

    await backfill.run_backfill(delete_raw=True)

    assert conn.execute_calls[0][0] == (
        "LOCK TABLE health_data IN SHARE ROW EXCLUSIVE MODE"
    )
    assert conn.transaction_entered is True


def test_main_exits_one_when_backfill_fails(mock_settings, monkeypatch):
    import app.backfill_apple_health as backfill

    async def fail_backfill(*, timezone_str, delete_raw):
        raise backfill.AppleHealthBackfillError("migration incomplete")

    monkeypatch.setattr(backfill, "run_backfill", fail_backfill)
    monkeypatch.setattr(
        backfill,
        "parse_args",
        lambda argv=None: SimpleNamespace(timezone="Europe/Kyiv", delete_raw=False),
    )

    with pytest.raises(SystemExit) as exc_info:
        backfill.main()

    assert exc_info.value.code == 1
