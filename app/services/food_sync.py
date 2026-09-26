"""FatSecret outbox worker and reconciliation (plan §7.2–7.4, AC-09/AC-10).

- Rows are claimed with ``FOR UPDATE SKIP LOCKED`` and a lease.
- A create is dispatched at most once per revision: a crashed/expired
  ``sending`` lease or an ambiguous answer becomes ``unknown`` and is
  *reconciled* against the remote diary before any retry.
- Edits/deletes are safe to repeat and are retried with backoff.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from typing import Any, Optional

from app.services.fatsecret_api import (
    FatSecretAuthError,
    FoodEntryWriteResult,
    create_food_entry,
    delete_food_entry,
    edit_food_entry,
    fatsecret_date,
    fetch_food_entries,
)

logger = logging.getLogger(__name__)

LEASE_SECONDS = 120
MAX_ATTEMPTS = 5
RECONCILE_MIN_AGE = timedelta(minutes=2)
RECONCILE_ABSENT_AFTER = timedelta(minutes=15)


def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(3600, 30 * (2 ** max(0, attempts - 1))))


async def _claim(conn: Any, *, limit: int, entry_ids: Optional[list[int]]) -> list[dict]:
    # Expired 'sending' creates are ambiguous → unknown (never re-sent blindly).
    await conn.execute(
        """UPDATE food_sync_outbox SET status = 'unknown', last_error = 'lease_expired'
           WHERE status = 'sending' AND operation = 'create' AND lease_until < NOW()"""
    )
    await conn.execute(
        """UPDATE food_sync_outbox SET status = 'pending'
           WHERE status = 'sending' AND operation <> 'create' AND lease_until < NOW()"""
    )
    rows = await conn.fetch(
        """UPDATE food_sync_outbox o
           SET status = 'sending', attempts = attempts + 1,
               lease_until = NOW() + make_interval(secs => $1), dispatched_at = NOW()
           WHERE o.id IN (
               SELECT id FROM food_sync_outbox
               WHERE status = 'pending' AND next_attempt_at <= NOW()
                 AND ($3::int[] IS NULL OR food_entry_id = ANY($3::int[]))
               ORDER BY id
               FOR UPDATE SKIP LOCKED
               LIMIT $2)
           RETURNING o.*""",
        LEASE_SECONDS, limit, entry_ids,
    )
    return [dict(r) for r in rows]


async def _load(conn: Any, op: dict) -> Optional[dict]:
    row = await conn.fetchrow(
        """SELECT fe.id, fe.user_id, fe.food_name, fe.meal_type::text AS meal_type, fe.local_date,
                  fe.remote_food_id, fe.remote_serving_id, fe.remote_units, fe.remote_entry_id,
                  fe.entry_status, fe.revision,
                  u.fatsecret_access_token, u.fatsecret_access_secret
           FROM food_entries fe JOIN users u ON u.id = fe.user_id
           WHERE fe.id = $1""",
        op["food_entry_id"],
    )
    return dict(row) if row else None


async def _finish(conn: Any, op: dict, status: str, *, error: Optional[str] = None,
                  remote_entry_id: Optional[str] = None, retry: bool = False) -> None:
    if retry:
        await conn.execute(
            """UPDATE food_sync_outbox SET status = 'pending', last_error = $2,
                      next_attempt_at = NOW() + make_interval(secs => $3), lease_until = NULL
               WHERE id = $1""",
            op["id"], error, int(_backoff(op["attempts"]).total_seconds()),
        )
        return
    await conn.execute(
        """UPDATE food_sync_outbox SET status = $2, last_error = $3,
                  remote_entry_id = COALESCE($4, remote_entry_id), lease_until = NULL
           WHERE id = $1""",
        op["id"], status, error, remote_entry_id,
    )


async def _apply_create_result(conn: Any, op: dict, entry: dict, result: FoodEntryWriteResult) -> str:
    if result.ok:
        await _finish(conn, op, "succeeded", remote_entry_id=result.remote_entry_id)
        await _link_remote(conn, entry, result.remote_entry_id)
        return "succeeded"
    if result.status == "unknown":
        await _finish(conn, op, "unknown", error=result.error)
        await conn.execute(
            "UPDATE food_entries SET sync_status = 'unknown' WHERE id = $1 AND entry_status = 'committed'",
            entry["id"],
        )
        return "unknown"
    retry = not result.auth_error and op["attempts"] < MAX_ATTEMPTS
    await _finish(conn, op, "failed", error=result.error, retry=retry)
    if not retry:
        await conn.execute(
            "UPDATE food_entries SET sync_status = 'failed' WHERE id = $1 AND entry_status = 'committed'",
            entry["id"],
        )
    return "retry" if retry else "failed"


async def _link_remote(conn: Any, entry: dict, remote_entry_id: str) -> None:
    """Store the acknowledged remote id. If the user voided the entry
    meanwhile, queue the remote delete instead of resurrecting it."""
    row = await conn.fetchrow(
        """UPDATE food_entries
           SET remote_entry_id = $2, remote_provider = 'fatsecret',
               sync_status = CASE WHEN entry_status = 'voided' THEN 'delete_pending' ELSE 'synced' END
           WHERE id = $1
           RETURNING entry_status, revision, user_id""",
        entry["id"], remote_entry_id,
    )
    if row and row["entry_status"] == "voided":
        await conn.execute(
            """INSERT INTO food_sync_outbox (user_id, food_entry_id, entry_revision, operation)
               VALUES ($1, $2, $3, 'delete')
               ON CONFLICT (food_entry_id, entry_revision, operation) DO UPDATE
                   SET status = 'pending', next_attempt_at = NOW()
                   WHERE food_sync_outbox.status IN ('succeeded', 'failed')""",
            row["user_id"], entry["id"], row["revision"],
        )


async def _process_one(pool: Any, op: dict) -> str:
    async with pool.acquire() as conn:
        entry = await _load(conn, op)
        if entry is None:
            await _finish(conn, op, "cancelled", error="entry_missing")
            return "cancelled"
        token, secret = entry["fatsecret_access_token"], entry["fatsecret_access_secret"]
        if not token or not secret:
            await _finish(conn, op, "failed", error="not_connected")
            await conn.execute(
                """UPDATE food_entries SET sync_status = CASE
                       WHEN entry_status = 'voided' THEN 'delete_failed' ELSE 'failed' END
                   WHERE id = $1""",
                entry["id"],
            )
            return "failed"

    operation = op["operation"]
    if operation == "create":
        if entry["entry_status"] == "voided":
            async with pool.acquire() as conn:
                await _finish(conn, op, "cancelled", error="voided_before_send")
            return "cancelled"
        if entry["remote_entry_id"]:
            # Replacement: the old remote entry must be deleted first, or the
            # day would contain both (double counting).
            async with pool.acquire() as conn:
                await conn.execute(
                    """UPDATE food_sync_outbox SET status = 'pending', attempts = attempts - 1,
                              last_error = 'awaiting_delete', lease_until = NULL,
                              next_attempt_at = NOW() + INTERVAL '30 seconds'
                       WHERE id = $1""",
                    op["id"],
                )
            return "retry"
        result = await create_food_entry(
            token, secret, entry["remote_food_id"], entry["food_name"],
            entry["remote_serving_id"], entry["remote_units"], meal_type=entry["meal_type"],
            date=fatsecret_date(entry["local_date"]),
        )
        async with pool.acquire() as conn:
            async with conn.transaction():
                return await _apply_create_result(conn, op, entry, result)

    if operation == "edit":
        if not entry["remote_entry_id"]:
            async with pool.acquire() as conn:
                await _finish(conn, op, "pending", error="awaiting_remote_id", retry=True)
            return "retry"
        result = await edit_food_entry(
            token, secret, entry["remote_entry_id"], serving_id=entry["remote_serving_id"],
            number_of_units=entry["remote_units"], meal_type=entry["meal_type"],
            entry_name=entry["food_name"],
        )
        async with pool.acquire() as conn:
            if result.ok:
                await _finish(conn, op, "succeeded")
                await conn.execute(
                    """UPDATE food_entries SET sync_status = 'synced'
                       WHERE id = $1 AND revision = $2 AND entry_status = 'committed'""",
                    entry["id"], op["entry_revision"],
                )
                return "succeeded"
            retry = not result.auth_error and op["attempts"] < MAX_ATTEMPTS
            await _finish(conn, op, "failed", error=result.error, retry=retry)
            if not retry:
                await conn.execute(
                    "UPDATE food_entries SET sync_status = 'failed' WHERE id = $1 AND revision = $2",
                    entry["id"], op["entry_revision"],
                )
            return "retry" if retry else "failed"

    # delete
    async with pool.acquire() as conn:
        create_state = await conn.fetchval(
            """SELECT status FROM food_sync_outbox
               WHERE food_entry_id = $1 AND operation = 'create'
               ORDER BY id DESC LIMIT 1""",
            entry["id"],
        )
    remote_id = entry["remote_entry_id"]
    if not remote_id:
        async with pool.acquire() as conn:
            if create_state in ("unknown", "sending", "pending"):
                await _finish(conn, op, "pending", error="awaiting_create_reconcile", retry=True)
                return "retry"
            await _finish(conn, op, "succeeded", error="nothing_remote")
            await conn.execute(
                "UPDATE food_entries SET sync_status = 'local_only' WHERE id = $1", entry["id"],
            )
            return "succeeded"
    result = await delete_food_entry(token, secret, remote_id)
    async with pool.acquire() as conn:
        if result.ok:
            await _finish(conn, op, "succeeded")
            # A delete that belongs to a product/date replacement is followed
            # by a create for the same revision; clear the old link.
            await conn.execute(
                """UPDATE food_entries
                   SET remote_entry_id = CASE WHEN remote_entry_id = $2 THEN NULL ELSE remote_entry_id END,
                       sync_status = CASE WHEN entry_status = 'voided' THEN 'deleted' ELSE sync_status END
                   WHERE id = $1""",
                entry["id"], remote_id,
            )
            return "succeeded"
        retry = not result.auth_error and op["attempts"] < MAX_ATTEMPTS
        await _finish(conn, op, "failed", error=result.error, retry=retry)
        if not retry:
            await conn.execute(
                """UPDATE food_entries SET sync_status = 'delete_failed'
                   WHERE id = $1 AND entry_status = 'voided'""",
                entry["id"],
            )
        return "retry" if retry else "failed"


async def process_outbox(pool: Any, *, limit: int = 20, entry_ids: Optional[list[int]] = None) -> dict:
    """Run due outbox operations. Safe to call concurrently from several
    replicas (SKIP LOCKED + leases)."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            ops = await _claim(conn, limit=limit, entry_ids=entry_ids)
    # Deletes after creates of the same entry so a replacement keeps order.
    ops.sort(key=lambda o: (o["food_entry_id"], {"delete": 0, "edit": 1, "create": 2}[o["operation"]]))
    stats: dict[str, int] = {}
    for op in ops:
        try:
            outcome = await _process_one(pool, op)
        except Exception:
            logger.exception("Outbox op %s failed unexpectedly", op["id"])
            async with pool.acquire() as conn:
                if op["operation"] == "create":
                    # We cannot know whether the request was sent.
                    await _finish(conn, op, "unknown", error="worker_exception")
                else:
                    await _finish(conn, op, "failed", error="worker_exception", retry=True)
            outcome = "error"
        stats[outcome] = stats.get(outcome, 0) + 1
    if ops:
        logger.info("Food outbox processed: %s", stats)
    return stats


def _units_equal(a: Any, b: Any) -> bool:
    try:
        return abs(Decimal(str(a)) - Decimal(str(b))) <= Decimal("0.01")
    except Exception:
        return False


async def reconcile_unknown(pool: Any, *, limit: int = 20, op_ids: Optional[list[int]] = None) -> dict:
    """Resolve ``unknown`` creates by reading the remote diary.

    - exactly one unlinked remote entry with the same food/serving/units, and
      exactly one local unknown entry with that signature → link it;
    - no such entry after RECONCILE_ABSENT_AFTER → safe to retry (pending);
    - several candidates → stay unknown ("ambiguous"), visible to user/admin.
    """
    stats = {"linked": 0, "retry": 0, "ambiguous": 0, "waiting": 0, "error": 0}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT o.*, fe.user_id, fe.local_date, fe.remote_food_id, fe.remote_serving_id,
                      fe.remote_units, fe.entry_status,
                      NOW() - COALESCE(o.dispatched_at, o.created_at) AS age,
                      u.fatsecret_access_token, u.fatsecret_access_secret
               FROM food_sync_outbox o
               JOIN food_entries fe ON fe.id = o.food_entry_id
               JOIN users u ON u.id = fe.user_id
               WHERE o.status = 'unknown' AND o.operation = 'create'
                 AND ($3::int[] IS NOT NULL
                      OR COALESCE(o.dispatched_at, o.created_at) < NOW() - make_interval(secs => $1))
                 AND ($3::int[] IS NULL OR o.id = ANY($3::int[]))
               ORDER BY o.id LIMIT $2""",
            int(RECONCILE_MIN_AGE.total_seconds()), limit, op_ids,
        )
    for row in rows:
        op = dict(row)
        if not op["fatsecret_access_token"]:
            continue
        try:
            remote = await fetch_food_entries(
                op["fatsecret_access_token"], op["fatsecret_access_secret"],
                fatsecret_date(op["local_date"]),
            )
        except FatSecretAuthError:
            stats["error"] += 1
            continue
        except Exception:
            logger.warning("Reconcile: diary read failed for outbox %s", op["id"], exc_info=True)
            stats["error"] += 1
            continue
        async with pool.acquire() as conn:
            async with conn.transaction():
                linked_ids = {
                    str(r["remote_entry_id"]) for r in await conn.fetch(
                        """SELECT remote_entry_id FROM food_entries
                           WHERE user_id = $1 AND remote_entry_id IS NOT NULL""",
                        op["user_id"],
                    )
                }
                matches = [
                    e for e in remote
                    if str(e.get("food_entry_id")) not in linked_ids
                    and str(e.get("food_id")) == str(op["remote_food_id"])
                    and str(e.get("serving_id")) == str(op["remote_serving_id"])
                    and _units_equal(e.get("number_of_units"), op["remote_units"])
                ]
                same_signature = await conn.fetchval(
                    """SELECT count(*) FROM food_entries fe
                       JOIN food_sync_outbox o ON o.food_entry_id = fe.id
                       WHERE fe.user_id = $1 AND fe.local_date = $2 AND fe.remote_entry_id IS NULL
                         AND fe.remote_food_id = $3 AND fe.remote_serving_id = $4
                         AND o.operation = 'create' AND o.status = 'unknown'""",
                    op["user_id"], op["local_date"], op["remote_food_id"], op["remote_serving_id"],
                )
                if len(matches) == 1 and same_signature == 1:
                    remote_id = str(matches[0]["food_entry_id"])
                    await _finish(conn, op, "succeeded", remote_entry_id=remote_id, error="reconciled")
                    await _link_remote(conn, {"id": op["food_entry_id"]}, remote_id)
                    stats["linked"] += 1
                elif not matches and op["age"] >= RECONCILE_ABSENT_AFTER:
                    # Verified absent: a new attempt is not a blind retry.
                    await conn.execute(
                        """UPDATE food_sync_outbox
                           SET status = 'pending', last_error = 'reconciled_absent',
                               next_attempt_at = NOW()
                           WHERE id = $1""",
                        op["id"],
                    )
                    await conn.execute(
                        "UPDATE food_entries SET sync_status = 'pending' WHERE id = $1 AND entry_status = 'committed'",
                        op["food_entry_id"],
                    )
                    stats["retry"] += 1
                elif matches:
                    await conn.execute(
                        "UPDATE food_sync_outbox SET last_error = 'ambiguous' WHERE id = $1", op["id"],
                    )
                    stats["ambiguous"] += 1
                else:
                    stats["waiting"] += 1
    return stats


async def run_outbox_job() -> None:
    from app.database import get_pool

    pool = await get_pool()
    await process_outbox(pool)


async def run_reconcile_job() -> None:
    from app.database import get_pool

    pool = await get_pool()
    stats = await reconcile_unknown(pool)
    if any(stats.values()):
        logger.info("Food outbox reconcile: %s", stats)
