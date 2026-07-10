from pathlib import Path

import pytest

from app.db_preflight import (
    AppleHealthSchemaError,
    REQUIRED_APPLE_HEALTH_INDEXES,
    REQUIRED_APPLE_HEALTH_TABLES,
    apply_apple_health_migration,
    run_preflight,
    verify_apple_health_schema,
)


class FakeConnection:
    def __init__(self, tables=None, indexes=None):
        self.tables = set(tables or [])
        self.indexes = set(indexes or [])
        self.executed = []
        self.closed = False

    async def execute(self, sql):
        self.executed.append(sql)

    async def fetch(self, query, names):
        if "information_schema.tables" in query:
            return [{"table_name": name} for name in self.tables.intersection(names)]
        if "pg_indexes" in query:
            return [{"indexname": name} for name in self.indexes.intersection(names)]
        return []

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_verify_apple_health_schema_passes_when_required_objects_exist():
    conn = FakeConnection(
        tables=REQUIRED_APPLE_HEALTH_TABLES,
        indexes=REQUIRED_APPLE_HEALTH_INDEXES,
    )

    await verify_apple_health_schema(conn)


@pytest.mark.asyncio
async def test_verify_apple_health_schema_fails_with_sanitized_missing_objects():
    conn = FakeConnection(tables={"health_data"}, indexes={"idx_health_data_user_id"})

    with pytest.raises(AppleHealthSchemaError) as exc_info:
        await verify_apple_health_schema(conn)

    message = str(exc_info.value)
    assert "apple_health_sync" in message
    assert "idx_apple_health_sync_secret_key" in message
    assert "postgresql://" not in message


@pytest.mark.asyncio
async def test_apply_apple_health_migration_reads_sql_file(tmp_path):
    migration = tmp_path / "migration.sql"
    migration.write_text("SELECT 1;", encoding="utf-8")
    conn = FakeConnection()

    await apply_apple_health_migration(conn, migration_path=Path(migration))

    assert conn.executed == ["SELECT 1;"]


@pytest.mark.asyncio
async def test_run_preflight_applies_migration_before_verifying(monkeypatch):
    conn = FakeConnection(
        tables=REQUIRED_APPLE_HEALTH_TABLES,
        indexes=REQUIRED_APPLE_HEALTH_INDEXES,
    )

    async def fake_connect(dsn):
        assert dsn == "postgresql://test:test@localhost:5432/test"
        return conn

    monkeypatch.setattr("app.db_preflight.asyncpg.connect", fake_connect)

    await run_preflight(apply_migration=True)

    assert any("CREATE TABLE IF NOT EXISTS apple_health_sync" in sql for sql in conn.executed)
    assert conn.closed is True
