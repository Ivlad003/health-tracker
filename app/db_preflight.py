from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

import asyncpg

logger = logging.getLogger(__name__)

MIGRATION_PATH = Path(__file__).resolve().parents[1] / "database" / "migrations" / "007_apple_health_connector.sql"

REQUIRED_APPLE_HEALTH_TABLES = {
    "apple_health_sync",
    "health_data",
    "apple_health_import_logs",
}

REQUIRED_APPLE_HEALTH_INDEXES = {
    "idx_apple_health_sync_user_id",
    "idx_apple_health_sync_secret_key",
    "idx_apple_health_sync_is_active",
    "idx_health_data_user_id",
    "idx_health_data_recorded_at",
    "idx_health_data_metric_type",
    "idx_health_data_source",
    "idx_health_data_user_metric",
    "idx_apple_health_import_logs_user_id",
    "idx_apple_health_import_logs_created_at",
    "idx_apple_health_import_logs_sync_id",
}


class AppleHealthSchemaError(RuntimeError):
    """Raised when required Apple Health database objects are absent."""


def _format_missing(missing_tables: set[str], missing_indexes: set[str]) -> str:
    parts: list[str] = []
    if missing_tables:
        parts.append(f"tables={','.join(sorted(missing_tables))}")
    if missing_indexes:
        parts.append(f"indexes={','.join(sorted(missing_indexes))}")
    return "; ".join(parts)


async def apply_apple_health_migration(conn: asyncpg.Connection, migration_path: Path = MIGRATION_PATH) -> None:
    sql = migration_path.read_text(encoding="utf-8")
    await conn.execute(sql)


async def verify_apple_health_schema(conn: asyncpg.Connection) -> None:
    table_rows = await conn.fetch(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_type = 'BASE TABLE'
          AND table_name = ANY($1::text[])
        """,
        sorted(REQUIRED_APPLE_HEALTH_TABLES),
    )
    existing_tables = {row["table_name"] for row in table_rows}

    index_rows = await conn.fetch(
        """
        SELECT indexname
        FROM pg_indexes
        WHERE schemaname = 'public'
          AND indexname = ANY($1::text[])
        """,
        sorted(REQUIRED_APPLE_HEALTH_INDEXES),
    )
    existing_indexes = {row["indexname"] for row in index_rows}

    missing_tables = REQUIRED_APPLE_HEALTH_TABLES - existing_tables
    missing_indexes = REQUIRED_APPLE_HEALTH_INDEXES - existing_indexes
    if missing_tables or missing_indexes:
        raise AppleHealthSchemaError(
            "Apple Health database schema is incomplete: "
            f"{_format_missing(missing_tables, missing_indexes)}. "
            "Run the production database preflight before starting the app."
        )


async def run_preflight(apply_migration: bool) -> None:
    from app.config import settings

    conn = await asyncpg.connect(dsn=settings.database_url)
    try:
        if apply_migration:
            await apply_apple_health_migration(conn)
            logger.info("Apple Health migration applied or already present")
        await verify_apple_health_schema(conn)
        logger.info("Apple Health schema preflight passed")
    finally:
        await conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate production database prerequisites.")
    parser.add_argument(
        "--apply-apple-health-migration",
        action="store_true",
        help="Apply the Apple Health migration before verifying required tables and indexes.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        asyncio.run(run_preflight(apply_migration=args.apply_apple_health_migration))
    except AppleHealthSchemaError as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from exc
    except Exception as exc:
        logger.error("Database preflight failed before app startup: %s", exc.__class__.__name__)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
