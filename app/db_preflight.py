from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

import asyncpg

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "database" / "migrations"

# Session-level lock shared by every app replica. The value is the ASCII bytes
# for "APPLEHDB" encoded as a positive signed bigint.
APPLE_HEALTH_MIGRATION_LOCK_KEY = 0x4150504C45484442

# Applied in order. Every migration is written to be idempotent (IF NOT EXISTS /
# DO-block guards), so re-applying on an already-migrated database is a no-op.
# 007 creates the Apple Health raw + shared tables; 009 adds the schema-v2 daily
# aggregate table; 010 adds causally ordered per-family aggregates; 011 widens
# the family CHECK for resting HR, body mass, distance, and exercise time;
# 012 adds user profile columns (BMR); 013 adds health_workouts; 014 hashes
# Apple Health webhook secrets; 015 drops never-written tables (guarded: tables
# with rows are kept); 016 adds the food ledger, catalog, drafts, sync outbox
# and history-import jobs; 017 adds Web App sessions, roles, preferences, goal
# history, feature flags, admin audit and notification de-duplication; 018
# adds Web App initData replay bookkeeping and per-user lookup indexes;
# 019 records whether a personal product was accepted by FatSecret food.create.
# The Docker CMD is the only production migration path, so every schema
# change the app depends on is listed here (despite the historical name).
# Superseded migration 008 (PG15-only raw natural key) is intentionally absent.
APPLE_HEALTH_MIGRATIONS = (
    MIGRATIONS_DIR / "007_apple_health_connector.sql",
    MIGRATIONS_DIR / "009_health_daily_aggregates.sql",
    MIGRATIONS_DIR / "010_health_daily_metric_aggregates.sql",
    MIGRATIONS_DIR / "011_apple_health_extended_families.sql",
    MIGRATIONS_DIR / "012_user_profile.sql",
    MIGRATIONS_DIR / "013_health_workouts.sql",
    MIGRATIONS_DIR / "014_apple_health_secret_hash.sql",
    MIGRATIONS_DIR / "015_drop_unused_tables.sql",
    MIGRATIONS_DIR / "016_food_ledger_catalog.sql",
    MIGRATIONS_DIR / "017_webapp_admin_preferences.sql",
    MIGRATIONS_DIR / "018_webapp_hardening.sql",
    MIGRATIONS_DIR / "019_custom_food_state.sql",
)

# Kept for backward compatibility with callers/tests that imported the single
# path constant.
MIGRATION_PATH = APPLE_HEALTH_MIGRATIONS[0]

REQUIRED_APPLE_HEALTH_TABLES = {
    "apple_health_sync",
    "health_data",
    "apple_health_import_logs",
    "health_daily_aggregates",
    "health_daily_metric_aggregates",
    "health_workouts",
    # 016
    "food_products",
    "food_nutrition_versions",
    "user_product_memberships",
    "food_default_rules",
    "food_log_drafts",
    "food_sync_outbox",
    "catalog_import_jobs",
    "catalog_import_candidates",
    "external_lookup_cache",
    # 017
    "user_preferences",
    "user_goal_history",
    "webapp_sessions",
    "user_roles",
    "feature_flags",
    "admin_audit_log",
    "notification_sends",
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
    "idx_health_daily_aggregates_user_id",
    "idx_health_daily_aggregates_user_date",
    "idx_health_daily_aggregates_source",
    "idx_health_daily_metric_aggregates_user_date",
    "idx_health_daily_metric_aggregates_family_freshness",
    "idx_health_daily_metric_aggregates_collector",
    "idx_health_daily_metric_aggregates_user_family_date",
    "idx_health_workouts_user_started",
    # 016: uniqueness the food ledger relies on (ON CONFLICT targets).
    "uq_food_products_shared_identity",
    "uq_food_products_personal_identity",
    "uq_food_nutrition_current_serving",
    "uq_food_default_rules_manual",
    "uq_food_default_rules_learned",
    "uq_food_log_drafts_message",
    "uq_food_log_drafts_media_group",
    "uq_food_entries_idempotency",
    "uq_food_entries_remote_entry",
    "idx_food_entries_user_local_date",
    "uq_catalog_import_jobs_active",
    # 018: proves the Web App hardening migration ran (webapp_auth uses
    # webapp_sessions.init_data_hash).
    "idx_webapp_sessions_init_data",
    "idx_food_sync_outbox_user_status",
}

# Named UNIQUE constraints the app targets via ON CONFLICT ON CONSTRAINT.
REQUIRED_APPLE_HEALTH_CONSTRAINTS_BY_TABLE = {
    "health_daily_aggregates": {"health_daily_aggregates_natural_key"},
    "health_daily_metric_aggregates": {
        "health_daily_metric_aggregates_natural_key",
        "health_daily_metric_aggregates_total_finite_check",
        "health_daily_metric_aggregates_average_check",
        # Present only after 011; proves the extended families are accepted.
        "health_daily_metric_aggregates_family_check_v2",
    },
    "health_workouts": {"health_workouts_natural_key"},
    "users": {"users_profile_check"},
    "food_entries": {"food_entries_ledger_check"},
    "user_product_memberships": {"user_product_memberships_natural_key"},
    "food_sync_outbox": {"food_sync_outbox_natural_key"},
    "food_log_drafts": {"food_log_drafts_commit_key"},
    "catalog_import_candidates": {"catalog_import_candidates_natural_key"},
    "user_goal_history": {"user_goal_history_natural_key"},
    "webapp_sessions": {"webapp_sessions_token_hash_key"},
}
REQUIRED_APPLE_HEALTH_CONSTRAINTS = set().union(
    *REQUIRED_APPLE_HEALTH_CONSTRAINTS_BY_TABLE.values()
)


class AppleHealthSchemaError(RuntimeError):
    """Raised when required Apple Health database objects are absent."""


def _format_missing(
    missing_tables: set[str],
    missing_indexes: set[str],
    missing_constraints: set[str] | None = None,
) -> str:
    parts: list[str] = []
    if missing_tables:
        parts.append(f"tables={','.join(sorted(missing_tables))}")
    if missing_indexes:
        parts.append(f"indexes={','.join(sorted(missing_indexes))}")
    if missing_constraints:
        parts.append(f"constraints={','.join(sorted(missing_constraints))}")
    return "; ".join(parts)


async def apply_apple_health_migration(
    conn: asyncpg.Connection,
    migration_path: Path = MIGRATION_PATH,
) -> None:
    """Apply a single migration file. Kept for direct/legacy callers and tests."""
    sql = migration_path.read_text(encoding="utf-8")
    await conn.execute(sql)


async def apply_apple_health_migrations(
    conn: asyncpg.Connection,
    migration_paths: tuple[Path, ...] = APPLE_HEALTH_MIGRATIONS,
) -> None:
    """Apply the ordered Apple Health migrations idempotently.

    Each migration file wraps its DDL in its own transaction (BEGIN/COMMIT) and
    uses IF NOT EXISTS guards, so a fresh install, an upgrade from 007, and a
    repeated startup all converge to the same schema. If any statement fails,
    that file's transaction rolls back and the exception propagates — the caller
    aborts startup, leaving no partially migrated schema from the failed file.
    """
    for path in migration_paths:
        await apply_apple_health_migration(conn, migration_path=path)
        logger.info("Applied migration %s", path.name)


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

    constraint_rows = await conn.fetch(
        """
        SELECT cls.relname AS table_name, con.conname
        FROM pg_constraint AS con
        JOIN pg_class AS cls ON cls.oid = con.conrelid
        JOIN pg_namespace AS ns ON ns.oid = cls.relnamespace
        WHERE ns.nspname = 'public'
          AND cls.relname = ANY($1::text[])
          AND con.conname = ANY($2::text[])
        """,
        sorted(REQUIRED_APPLE_HEALTH_CONSTRAINTS_BY_TABLE),
        sorted(REQUIRED_APPLE_HEALTH_CONSTRAINTS),
    )
    existing_constraints = {
        (row["table_name"], row["conname"]) for row in constraint_rows
    }
    required_constraint_pairs = {
        (table_name, constraint_name)
        for table_name, names in REQUIRED_APPLE_HEALTH_CONSTRAINTS_BY_TABLE.items()
        for constraint_name in names
    }

    missing_tables = REQUIRED_APPLE_HEALTH_TABLES - existing_tables
    missing_indexes = REQUIRED_APPLE_HEALTH_INDEXES - existing_indexes
    missing_constraint_pairs = required_constraint_pairs - existing_constraints
    missing_constraints = {
        f"{table_name}.{constraint_name}"
        for table_name, constraint_name in missing_constraint_pairs
    }
    if missing_tables or missing_indexes or missing_constraints:
        raise AppleHealthSchemaError(
            "Apple Health database schema is incomplete: "
            f"{_format_missing(missing_tables, missing_indexes, missing_constraints)}. "
            "Run the production database preflight before starting the app."
        )


async def run_preflight(apply_migration: bool) -> None:
    from app.config import settings

    conn = await asyncpg.connect(dsn=settings.database_url)
    try:
        await conn.execute(
            "SELECT pg_advisory_lock($1::bigint)",
            APPLE_HEALTH_MIGRATION_LOCK_KEY,
        )
        try:
            if apply_migration:
                await apply_apple_health_migrations(conn)
                logger.info("Apple Health migrations applied or already present")
            await verify_apple_health_schema(conn)
            logger.info("Apple Health schema preflight passed")
        finally:
            await conn.execute(
                "SELECT pg_advisory_unlock($1::bigint)",
                APPLE_HEALTH_MIGRATION_LOCK_KEY,
            )
    finally:
        await conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate production database prerequisites.")
    parser.add_argument(
        "--apply-apple-health-migration",
        action="store_true",
        help="Apply the Apple Health migrations before verifying required tables and indexes.",
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
