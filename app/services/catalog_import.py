"""FatSecret diary history → My Products (plan §14 "required P1 flow").

- Starts automatically after FatSecret is connected (last N local days).
- Resumable: a checkpoint is persisted after every day; failures/rate limits
  leave the job ``partial`` with a retry time; nothing is ever deleted
  because of an empty or partial response (AC-23).
- One ``(fatsecret, food_id)`` → one product card; several servings are
  kept as known serving ids. Catalog population never creates a meal.
- Exclusions and archived cards are respected; pinned defaults and personal
  overrides are never touched (AC-21).
- Only IDs and the user's own diary labels are stored durably; provider
  names/nutrition follow the ≤24 h cache policy.
"""
from __future__ import annotations

import json as _json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from app.services import food_catalog as catalog
from app.services.fatsecret_api import (
    FatSecretAPIError,
    FatSecretAuthError,
    date_from_fatsecret,
    fatsecret_date,
    fetch_food_entries,
    get_favorite_foods,
    get_most_eaten,
    get_recently_eaten,
)
from app.timeutils import resolve_timezone

logger = logging.getLogger(__name__)

DAYS_PER_RUN = 31
LEASE_SECONDS = 600
REFRESH_OVERLAP_DAYS = 3


class ImportError_(ValueError):
    pass


async def start_import(
    conn: Any,
    user_id: int,
    *,
    days: Optional[int] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    mode: str = "auto",
    kind: str = "initial",
) -> dict:
    """Create (or return the already active) import job for the user."""
    if mode not in ("auto", "selective"):
        raise ImportError_("mode_invalid")
    tz_name = await conn.fetchval("SELECT timezone FROM users WHERE id = $1", user_id)
    today = datetime.now(resolve_timezone(tz_name)).date()
    if date_from is None or date_to is None:
        span = max(1, min(int(days or 30), 365))
        date_to = today
        date_from = today - timedelta(days=span - 1)
    if date_from > date_to:
        raise ImportError_("range_invalid")
    if date_to > today:
        date_to = today
    if (date_to - date_from).days > 365 * 3:
        raise ImportError_("range_too_large")
    row = await conn.fetchrow(
        """INSERT INTO catalog_import_jobs (user_id, kind, mode, date_from, date_to, days_total)
           VALUES ($1, $2, $3, $4, $5, $6)
           ON CONFLICT (user_id) WHERE status IN ('pending', 'running', 'partial')
           DO NOTHING
           RETURNING *""",
        user_id, kind, mode, date_from, date_to, (date_to - date_from).days + 1,
    )
    if row is None:
        row = await conn.fetchrow(
            """SELECT * FROM catalog_import_jobs
               WHERE user_id = $1 AND status IN ('pending', 'running', 'partial')""",
            user_id,
        )
        return {**dict(row), "already_active": True}
    return {**dict(row), "already_active": False}


async def get_job(conn: Any, user_id: int, job_id: int) -> Optional[dict]:
    row = await conn.fetchrow(
        "SELECT * FROM catalog_import_jobs WHERE id = $1 AND user_id = $2", job_id, user_id,
    )
    return dict(row) if row else None


async def latest_jobs(conn: Any, user_id: int, limit: int = 5) -> list[dict]:
    rows = await conn.fetch(
        "SELECT * FROM catalog_import_jobs WHERE user_id = $1 ORDER BY id DESC LIMIT $2",
        user_id, limit,
    )
    return [dict(r) for r in rows]


async def cancel_job(conn: Any, user_id: int, job_id: int) -> bool:
    status = await conn.execute(
        """UPDATE catalog_import_jobs SET status = 'cancelled', lease_until = NULL
           WHERE id = $1 AND user_id = $2 AND status IN ('pending', 'running', 'partial')""",
        job_id, user_id,
    )
    return status.endswith(" 1")


async def _claim_job(pool: Any, job_id: Optional[int] = None) -> Optional[dict]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE catalog_import_jobs j
               SET status = 'running', attempts = attempts + 1,
                   lease_until = NOW() + make_interval(secs => $1)
               WHERE j.id = (
                   SELECT id FROM catalog_import_jobs
                   WHERE ($2::int IS NULL OR id = $2)
                     AND ((status IN ('pending', 'partial') AND next_attempt_at <= NOW())
                          OR (status = 'running' AND lease_until < NOW()))
                   ORDER BY next_attempt_at, id
                   FOR UPDATE SKIP LOCKED
                   LIMIT 1)
               RETURNING j.*""",
            LEASE_SECONDS, job_id,
        )
    return dict(row) if row else None


async def _ingest_day(conn: Any, job: dict, day: date, entries: list[dict]) -> tuple[int, int]:
    """Upsert products/memberships (auto) or candidates (selective) for a day."""
    by_food: dict[str, dict] = {}
    for entry in entries:
        food_id = entry.get("food_id")
        if not food_id or not str(food_id).isdigit() or str(food_id) == "0":
            continue
        agg = by_food.setdefault(str(food_id), {"servings": set(), "count": 0, "label": None})
        agg["count"] += 1
        if entry.get("serving_id") and entry["serving_id"] != "0":
            agg["servings"].add(entry["serving_id"])
        # food_entry_name is the user's own diary label → durable display name.
        agg["label"] = agg["label"] or (entry.get("name") or None)

    found = added = 0
    for food_id, agg in by_food.items():
        product_id = await catalog.upsert_fatsecret_product(conn, food_id)
        found += 1
        servings = sorted(agg["servings"])
        if job["mode"] == "selective":
            await conn.execute(
                """INSERT INTO catalog_import_candidates
                       (job_id, user_id, product_id, serving_ids, occurrences, last_seen_date, label, state)
                   VALUES ($1, $2, $3, to_jsonb($4::text[]), $5, $6, $7,
                           CASE WHEN EXISTS (SELECT 1 FROM user_product_memberships m
                                             WHERE m.user_id = $2 AND m.product_id = $3)
                                THEN 'already_member' ELSE 'pending' END)
                   ON CONFLICT (job_id, product_id) DO UPDATE SET
                       label = COALESCE(catalog_import_candidates.label, EXCLUDED.label),
                       occurrences = catalog_import_candidates.occurrences + EXCLUDED.occurrences,
                       last_seen_date = GREATEST(catalog_import_candidates.last_seen_date,
                                                 EXCLUDED.last_seen_date),
                       serving_ids = (SELECT COALESCE(jsonb_agg(DISTINCT v), '[]'::jsonb)
                                      FROM jsonb_array_elements(catalog_import_candidates.serving_ids
                                                                || EXCLUDED.serving_ids) v)""",
                job["id"], job["user_id"], product_id, servings, agg["count"], day,
                (agg["label"] or "")[:255] or None,
            )
            continue
        membership = None
        for index, serving_id in enumerate(servings or [None]):
            membership = await catalog.upsert_membership(
                conn, job["user_id"], product_id, "fatsecret_history",
                display_name=agg["label"], serving_id=serving_id,
                history_date=day, history_occurrences=agg["count"] if index == 0 else 0,
            )
        if membership and membership["created"]:
            added += 1
    return found, added


async def _fetch_enrichment(token: str, secret: str) -> list[dict]:
    """Recent / most-eaten / favourites (network; outside any transaction)."""
    foods: list[dict] = []
    for fetch in (get_favorite_foods, get_most_eaten, get_recently_eaten):
        try:
            foods.extend(await fetch(token, secret))
        except Exception as exc:  # optional enrichment
            logger.info("History enrichment %s skipped: %s", fetch.__name__, exc.__class__.__name__)
    return foods


async def _enrich(conn: Any, job: dict, foods: list[dict]) -> None:
    """Ordering + discovery only; respects exclusions like the diary scan."""
    for rank, food in enumerate(foods, start=1):
        product_id = await catalog.upsert_fatsecret_product(
            conn, food["food_id"], name=food.get("name"), brand=food.get("brand"),
        )
        if job["mode"] == "auto":
            await catalog.upsert_membership(
                conn, job["user_id"], product_id, "fatsecret_history",
                serving_id=food.get("serving_id") or None,
            )
        await conn.execute(
            """UPDATE user_product_memberships
               SET provider_rank = LEAST(COALESCE(provider_rank, $3), $3)
               WHERE user_id = $1 AND product_id = $2""",
            job["user_id"], product_id, rank,
        )


async def run_job(pool: Any, job_id: Optional[int] = None) -> Optional[dict]:
    """Process one due job for up to DAYS_PER_RUN days (newest first)."""
    job = await _claim_job(pool, job_id)
    if job is None:
        return None
    async with pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT fatsecret_access_token, fatsecret_access_secret FROM users WHERE id = $1",
            job["user_id"],
        )
    token = user["fatsecret_access_token"] if user else None
    secret = user["fatsecret_access_secret"] if user else None
    if not token or not secret:
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE catalog_import_jobs SET status = 'failed', last_error = 'not_connected',
                          lease_until = NULL WHERE id = $1""",
                job["id"],
            )
        return {**job, "status": "failed"}

    day = (job["checkpoint_date"] - timedelta(days=1)) if job["checkpoint_date"] else job["date_to"]
    processed = 0
    while day >= job["date_from"] and processed < DAYS_PER_RUN:
        try:
            entries = await fetch_food_entries(token, secret, fatsecret_date(day))
        except FatSecretAuthError:
            async with pool.acquire() as conn:
                await conn.execute(
                    """UPDATE catalog_import_jobs SET status = 'failed', last_error = 'auth',
                              lease_until = NULL WHERE id = $1""",
                    job["id"],
                )
            return {**job, "status": "failed"}
        except Exception as exc:
            code = f"api_{exc.code}" if isinstance(exc, FatSecretAPIError) else exc.__class__.__name__
            async with pool.acquire() as conn:
                await conn.execute(
                    """UPDATE catalog_import_jobs
                       SET status = 'partial', last_error = $2, lease_until = NULL,
                           next_attempt_at = NOW() + make_interval(mins => LEAST(60, 5 * attempts))
                       WHERE id = $1""",
                    job["id"], code[:128],
                )
            logger.warning("Catalog import job %s paused at %s: %s", job["id"], day, code)
            return {**job, "status": "partial"}
        async with pool.acquire() as conn:
            async with conn.transaction():
                found, added = await _ingest_day(conn, job, day, entries)
                await conn.execute(
                    """UPDATE catalog_import_jobs
                       SET checkpoint_date = $2, days_done = days_done + 1,
                           entries_seen = entries_seen + $3,
                           products_found = products_found + $4,
                           products_added = products_added + $5,
                           lease_until = NOW() + make_interval(secs => $6)
                       WHERE id = $1""",
                    job["id"], day, len(entries), found, added, LEASE_SECONDS,
                )
        job["checkpoint_date"] = day
        processed += 1
        day -= timedelta(days=1)

    async with pool.acquire() as conn:
        if day >= job["date_from"]:
            await conn.execute(
                """UPDATE catalog_import_jobs SET status = 'partial', lease_until = NULL,
                          next_attempt_at = NOW(), last_error = NULL
                   WHERE id = $1""",
                job["id"],
            )
            return {**job, "status": "partial"}
        foods = await _fetch_enrichment(token, secret)
        async with conn.transaction():
            await _enrich(conn, job, foods)
            row = await conn.fetchrow(
                """UPDATE catalog_import_jobs
                   SET status = 'completed', completed_at = NOW(), lease_until = NULL, last_error = NULL
                   WHERE id = $1 RETURNING *""",
                job["id"],
            )
    return dict(row)


async def list_candidates(conn: Any, user_id: int, job_id: int, *, state: str = "pending",
                          limit: int = 100, offset: int = 0) -> list[dict]:
    rows = await conn.fetch(
        """SELECT c.id, c.product_id, c.state, c.serving_ids, c.occurrences, c.last_seen_date, c.label AS diary_label,
                  p.external_id, p.provider_name, p.provider_brand, p.provider_cached_until
           FROM catalog_import_candidates c
           JOIN food_products p ON p.id = c.product_id
           WHERE c.job_id = $1 AND c.user_id = $2 AND c.state = $3
           ORDER BY c.occurrences DESC, c.last_seen_date DESC, c.id
           LIMIT $4 OFFSET $5""",
        job_id, user_id, state, max(1, min(limit, 500)), max(0, offset),
    )
    now = datetime.now(timezone.utc)
    out = []
    for row in rows:
        data = dict(row)
        fresh = data["provider_cached_until"] and data["provider_cached_until"] > now
        data["label"] = (data["diary_label"] or (data["provider_name"] if fresh else None)
                         or f"FatSecret #{data['external_id']}")
        if not fresh:
            data["provider_name"] = data["provider_brand"] = None
        out.append(data)
    return out


async def apply_selection(
    conn: Any, user_id: int, job_id: int, *, selected: list[int], skipped: list[int],
    select_all: bool = False,
) -> dict:
    """Selective mode: add chosen products; record explicitly skipped ones as
    excluded. Unreviewed candidates stay pending (not excluded)."""
    job = await get_job(conn, user_id, job_id)
    if job is None:
        raise ImportError_("not_found")
    if select_all:
        selected = [
            r["product_id"] for r in await conn.fetch(
                "SELECT product_id FROM catalog_import_candidates WHERE job_id = $1 AND user_id = $2 AND state = 'pending'",
                job_id, user_id,
            )
        ]
    added = excluded = 0
    for product_id in selected:
        cand = await conn.fetchrow(
            """SELECT serving_ids, occurrences, last_seen_date, label FROM catalog_import_candidates
               WHERE job_id = $1 AND user_id = $2 AND product_id = $3""",
            job_id, user_id, product_id,
        )
        if cand is None:
            continue
        servings = cand["serving_ids"]
        servings = _json.loads(servings) if isinstance(servings, str) else (servings or [])
        for index, serving_id in enumerate(servings or [None]):
            await catalog.upsert_membership(
                conn, user_id, product_id, "fatsecret_history", serving_id=serving_id,
                display_name=cand["label"], history_date=cand["last_seen_date"],
                history_occurrences=cand["occurrences"] if index == 0 else 0, explicit=True,
            )
        await conn.execute(
            "UPDATE catalog_import_candidates SET state = 'added' WHERE job_id = $1 AND product_id = $2",
            job_id, product_id,
        )
        added += 1
    for product_id in skipped:
        exists = await conn.fetchval(
            "SELECT 1 FROM catalog_import_candidates WHERE job_id = $1 AND user_id = $2 AND product_id = $3",
            job_id, user_id, product_id,
        )
        if not exists:
            continue
        await catalog.exclude_product(conn, user_id, product_id)
        await conn.execute(
            "UPDATE catalog_import_candidates SET state = 'excluded' WHERE job_id = $1 AND product_id = $2",
            job_id, product_id,
        )
        excluded += 1
    return {"added": added, "excluded": excluded}


async def run_due_jobs(limit: int = 3) -> None:
    from app.database import get_pool

    pool = await get_pool()
    for _ in range(limit):
        result = await run_job(pool)
        if result is None:
            break


async def schedule_daily_refresh() -> None:
    """Bounded daily refresh: re-read the last few days (late edits) for users
    who enabled it and have no active job."""
    from app.database import get_pool
    from app.services.preferences import get_preferences

    pool = await get_pool()
    async with pool.acquire() as conn:
        users = await conn.fetch(
            """SELECT u.id FROM users u
               WHERE u.fatsecret_access_token IS NOT NULL AND u.fatsecret_access_token <> ''
                 AND NOT EXISTS (SELECT 1 FROM catalog_import_jobs j WHERE j.user_id = u.id
                                 AND j.status IN ('pending', 'running', 'partial'))"""
        )
        for row in users:
            prefs, _ = await get_preferences(conn, row["id"])
            if not prefs.history_daily_refresh:
                continue
            try:
                await start_import(
                    conn, row["id"], days=REFRESH_OVERLAP_DAYS,
                    mode=prefs.history_import_mode, kind="refresh",
                )
            except Exception:
                logger.warning("Daily refresh scheduling failed for user %s", row["id"], exc_info=True)


def job_to_json(job: dict) -> dict:
    out = {}
    for key, value in job.items():
        if isinstance(value, (date, datetime)):
            out[key] = value.isoformat()
        else:
            out[key] = value
    total = job.get("days_total") or 0
    out["progress"] = round((job.get("days_done") or 0) / total, 3) if total else 0
    out["covered_from"] = job["checkpoint_date"].isoformat() if job.get("checkpoint_date") else None
    out["covered_to"] = job["date_to"].isoformat() if job.get("checkpoint_date") else None
    return out


__all__ = [
    "start_import", "run_job", "apply_selection", "list_candidates", "get_job",
    "latest_jobs", "cancel_job", "run_due_jobs", "schedule_daily_refresh", "job_to_json",
    "date_from_fatsecret",
]
