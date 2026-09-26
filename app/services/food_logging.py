"""Food ledger: drafts, idempotent commits, revisions and the daily view.

Plan §5 (draft lifecycle), §7 (reliable writes + daily totals), FR-08..FR-13,
FR-19. The bot and the Web App both call these functions.

Invariants:
- A confirmed entry is committed locally together with its outbox operation
  in ONE transaction; local success never depends on FatSecret.
- Replays (duplicate Telegram updates, double Confirm, Web retries) hit the
  same idempotency key / draft commit key and create one event.
- Unknown nutrition is NULL; totals that include unknown/ambiguous parts are
  reported as ``partial``.
- The daily view is the union of local events and remote diary entries
  linked by remote id: a linked entry counts once.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Optional
from zoneinfo import ZoneInfo

from app.config import settings
from app.services import food_catalog as catalog
from app.services.food_catalog import VersionConflict
from app.services.food_nutrition import (
    NutritionError,
    Portion,
    calculate_portion,
    choose_gram_serving,
    fatsecret_units_for_grams,
    q1,
    to_decimal,
    validate_grams,
)
from app.services.preferences import FoodPreferences, get_preferences
from app.timeutils import resolve_timezone

logger = logging.getLogger(__name__)

OPEN_DRAFT_STATES = ("received", "recognizing", "needs_product", "needs_weight", "needs_label", "ready")
MEAL_TYPES = ("breakfast", "lunch", "dinner", "snack")
ORIGINS = ("bot_text", "bot_voice", "bot_photo", "web_manual", "web_upload")


class LedgerError(ValueError):
    """Business error with an i18n/API code in ``args[0]``."""


# ---------------------------------------------------------------------------
# User context
# ---------------------------------------------------------------------------

@dataclass
class UserContext:
    user_id: int
    tz: ZoneInfo
    fs_token: str = ""
    fs_secret: str = ""
    prefs: FoodPreferences = field(default_factory=FoodPreferences)
    language: str = "uk"
    telegram_user_id: Optional[int] = None

    @property
    def fs_connected(self) -> bool:
        return bool(self.fs_token and self.fs_secret)

    @property
    def export_enabled(self) -> bool:
        return self.fs_connected and self.prefs.fatsecret_export

    def today(self) -> date:
        return datetime.now(self.tz).date()


async def load_user_context(conn: Any, user_id: int) -> UserContext:
    row = await conn.fetchrow(
        """SELECT id, telegram_user_id, timezone, language,
                  fatsecret_access_token, fatsecret_access_secret
           FROM users WHERE id = $1""",
        user_id,
    )
    if row is None:
        raise LedgerError("user_not_found")
    prefs, _ = await get_preferences(conn, user_id)
    return UserContext(
        user_id=user_id,
        tz=resolve_timezone(row["timezone"]),
        fs_token=row["fatsecret_access_token"] or "",
        fs_secret=row["fatsecret_access_secret"] or "",
        prefs=prefs,
        language=row["language"] or "uk",
        telegram_user_id=row["telegram_user_id"],
    )


def default_meal_type(local_now: datetime) -> str:
    hour = local_now.hour
    if 4 <= hour < 11:
        return "breakfast"
    if 11 <= hour < 16:
        return "lunch"
    if 16 <= hour < 21:
        return "dinner"
    return "snack"


def validate_local_date(value: Any, ctx: UserContext) -> date:
    """Local calendar date: ISO string/date, not more than 1 day in the future."""
    if value is None:
        return ctx.today()
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value)
        except ValueError as exc:
            raise LedgerError("date_invalid") from exc
    if not isinstance(value, date):
        raise LedgerError("date_invalid")
    today = ctx.today()
    if value > today + timedelta(days=1) or value < today - timedelta(days=3650):
        raise LedgerError("date_out_of_range")
    return value


# ---------------------------------------------------------------------------
# Item preparation (may call FatSecret: never inside a transaction)
# ---------------------------------------------------------------------------

@dataclass
class PreparedItem:
    product_id: int
    label: str
    grams: Decimal
    quantity_source: str
    portion: Optional[Portion]
    nutrition_version_id: Optional[int]
    nutrition_source: Optional[str]
    nutrition_expires_at: Optional[datetime]
    preparation: Optional[str]
    remote: Optional[dict] = None
    alias_text: Optional[str] = None
    serving_id: Optional[str] = None


async def _fatsecret_servings(conn: Any, product_id: int) -> list[dict]:
    rows = await conn.fetch(
        """SELECT serving_id, grams_per_basis, fatsecret_units_per_basis, serving_description
           FROM food_nutrition_versions
           WHERE product_id = $1 AND source = 'fatsecret' AND is_current
             AND serving_id IS NOT NULL AND expires_at > NOW()""",
        product_id,
    )
    return [
        {
            "serving_id": r["serving_id"],
            "metric_serving_amount": r["grams_per_basis"],
            "metric_serving_unit": "g",
            "number_of_units": r["fatsecret_units_per_basis"] or 1,
            "description": r["serving_description"] or "",
        }
        for r in rows
    ]


async def prepare_item(
    conn: Any,
    ctx: UserContext,
    *,
    product_id: int,
    grams: Any,
    quantity_source: str = "explicit",
    serving_id: Optional[str] = None,
    alias_text: Optional[str] = None,
    label: Optional[str] = None,
) -> PreparedItem:
    grams_d = validate_grams(grams)
    product = await catalog.get_product(conn, product_id, ctx.user_id)
    if product is None:
        raise LedgerError("product_not_found")
    if product.get("membership_state") in ("excluded", "archived"):
        raise LedgerError("product_excluded")
    serving_id = serving_id or product.get("preferred_serving_id")

    nutrition = await catalog.current_nutrition(conn, product_id, ctx.user_id, serving_id)
    remote = None
    if product["provider"] == "fatsecret":
        servings = await _fatsecret_servings(conn, product_id)
        if nutrition is None or not servings:
            try:
                await catalog.refresh_fatsecret_product(conn, product_id, product["external_id"])
            except Exception:
                logger.warning("FatSecret refresh failed for product %s", product_id, exc_info=True)
            nutrition = await catalog.current_nutrition(conn, product_id, ctx.user_id, serving_id)
            servings = await _fatsecret_servings(conn, product_id)
            product = await catalog.get_product(conn, product_id, ctx.user_id) or product
        preferred = next((s for s in servings if s["serving_id"] == serving_id), None)
        writable = preferred or choose_gram_serving(servings)
        if writable is not None:
            try:
                remote = {
                    "food_id": product["external_id"],
                    "serving_id": writable["serving_id"],
                    "units": fatsecret_units_for_grams(writable, grams_d),
                }
                serving_id = writable["serving_id"]
            except NutritionError:
                remote = None

    if nutrition is None:
        raise LedgerError("nutrition_missing")
    try:
        portion = calculate_portion(catalog.row_to_basis(nutrition), grams_d)
    except NutritionError as exc:
        raise LedgerError(str(exc)) from exc
    return PreparedItem(
        product_id=product_id,
        label=(label or product["label"])[:255],
        grams=grams_d,
        quantity_source=quantity_source,
        portion=portion,
        nutrition_version_id=nutrition["id"],
        nutrition_source=nutrition["source"],
        nutrition_expires_at=nutrition.get("expires_at"),
        preparation=product.get("preparation"),
        remote=remote,
        alias_text=alias_text,
        serving_id=serving_id,
    )


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------

_ENTRY_COLUMNS = """id, food_name, calories, protein, fat, carbs, grams, meal_type::text AS meal_type,
    local_date, origin, entry_status, sync_status, remote_entry_id, product_id, revision,
    version, nutrition_source, created_at"""


async def _insert_entry(
    conn: Any,
    ctx: UserContext,
    item: PreparedItem,
    *,
    meal_type: str,
    local_date: date,
    origin: str,
    idempotency_key: str,
    draft_id: Optional[int],
) -> tuple[dict, bool]:
    if meal_type not in MEAL_TYPES:
        meal_type = "snack"
    if item.remote and ctx.export_enabled:
        sync_status = "pending"
    elif ctx.export_enabled:
        sync_status = "not_supported"
    else:
        sync_status = "local_only"
    p = item.portion
    row = await conn.fetchrow(
        f"""INSERT INTO food_entries
               (user_id, food_name, calories, protein, fat, carbs, fiber,
                serving_size, serving_unit, grams, quantity_source, meal_type, logged_at,
                local_date, timezone, origin, entry_status, idempotency_key, draft_id,
                product_id, nutrition_version_id, nutrition_source, nutrition_expires_at,
                preparation, remote_provider, remote_food_id, remote_serving_id, remote_units,
                sync_status, source_text, fatsecret_food_id)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'g', $8, $9, $10::meal_type, NOW(),
                   $11, $12, $13, 'committed', $14, $15, $16, $17, $18, $19, $20,
                   $21, $22, $23, $24, $25, $26, $22)
           ON CONFLICT (user_id, idempotency_key) WHERE idempotency_key IS NOT NULL
           DO NOTHING
           RETURNING {_ENTRY_COLUMNS}""",
        ctx.user_id, item.label,
        p.energy_kcal if p else None, p.protein_g if p else None,
        p.fat_g if p else None, p.carbs_g if p else None, p.fiber_g if p else None,
        item.grams, item.quantity_source, meal_type, local_date, ctx.tz.key, origin,
        idempotency_key, draft_id, item.product_id, item.nutrition_version_id,
        item.nutrition_source,
        item.nutrition_expires_at if item.nutrition_source == "fatsecret" else None,
        item.preparation,
        "fatsecret" if item.remote else None,
        item.remote["food_id"] if item.remote else None,
        item.remote["serving_id"] if item.remote else None,
        item.remote["units"] if item.remote else None,
        sync_status, item.alias_text,
    )
    if row is None:
        existing = await conn.fetchrow(
            f"SELECT {_ENTRY_COLUMNS} FROM food_entries WHERE user_id = $1 AND idempotency_key = $2",
            ctx.user_id, idempotency_key,
        )
        return dict(existing), False
    entry = dict(row)
    if sync_status == "pending":
        await conn.execute(
            """INSERT INTO food_sync_outbox (user_id, food_entry_id, entry_revision, operation)
               VALUES ($1, $2, 1, 'create')
               ON CONFLICT (food_entry_id, entry_revision, operation) DO NOTHING""",
            ctx.user_id, entry["id"],
        )
    # My Products: confirmed use (respects exclusions and the auto-add setting).
    membership_origin = "web" if origin.startswith("web") else "bot"
    if ctx.prefs.catalog_auto_add:
        if not await catalog.is_excluded(conn, ctx.user_id, item.product_id):
            await catalog.upsert_membership(
                conn, ctx.user_id, item.product_id, membership_origin,
                display_name=item.label, serving_id=item.serving_id, confirmed=True,
            )
    else:
        await conn.execute(
            """UPDATE user_product_memberships
               SET confirmed_count = confirmed_count + 1, last_used_at = NOW()
               WHERE user_id = $1 AND product_id = $2 AND state = 'active'""",
            ctx.user_id, item.product_id,
        )
    if item.alias_text and not await catalog.is_excluded(conn, ctx.user_id, item.product_id):
        await catalog.learn_alias(conn, ctx.user_id, item.alias_text, item.product_id, item.serving_id)
    return entry, True


async def commit_items(
    pool: Any,
    ctx: UserContext,
    items: list[PreparedItem],
    *,
    meal_type: str,
    local_date: date,
    origin: str,
    idempotency_prefix: str,
    draft_id: Optional[int] = None,
) -> list[dict]:
    """Commit prepared items atomically (entries + outbox + membership)."""
    if origin not in ORIGINS:
        raise LedgerError("origin_invalid")
    async with pool.acquire() as conn:
        async with conn.transaction():
            entries = []
            for index, item in enumerate(items):
                entry, _created = await _insert_entry(
                    conn, ctx, item, meal_type=meal_type, local_date=local_date, origin=origin,
                    idempotency_key=f"{idempotency_prefix}:{index}", draft_id=draft_id,
                )
                entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# Drafts
# ---------------------------------------------------------------------------

def _loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


def draft_from_row(row: Any) -> dict:
    data = dict(row)
    data["items"] = _loads(data.get("items"), [])
    data["media"] = _loads(data.get("media"), [])
    return data


def evaluate_items(items: list[dict]) -> str:
    """Draft state from its items: product, weight or label still missing."""
    if not items:
        return "needs_product"
    for item in items:
        if item.get("status") == "needs_label" and not item.get("selected"):
            return "needs_label"
    for item in items:
        if not item.get("selected"):
            return "needs_product"
    for item in items:
        if item.get("grams") in (None, ""):
            return "needs_weight"
    return "ready"


async def create_draft(
    conn: Any,
    user_id: int,
    *,
    origin: str,
    items: list[dict],
    meal_type: Optional[str],
    local_date: Optional[date],
    chat_id: Optional[int] = None,
    message_id: Optional[int] = None,
    media_group_id: Optional[str] = None,
    commit_key: Optional[str] = None,
    media: Optional[list] = None,
) -> tuple[dict, bool]:
    """Create a draft; a replayed Telegram message returns the existing one.

    Returns ``(draft, created)``.
    """
    if origin not in ORIGINS:
        raise LedgerError("origin_invalid")
    if commit_key is None:
        if chat_id is not None and message_id is not None:
            commit_key = f"tg:{chat_id}:{message_id}"
        else:
            raise LedgerError("commit_key_required")
    state = evaluate_items(items)
    row = await conn.fetchrow(
        """INSERT INTO food_log_drafts
               (user_id, chat_id, message_id, media_group_id, origin, state, items, media,
                meal_type, local_date, commit_key, expires_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9::meal_type, $10, $11,
                   NOW() + make_interval(hours => $12))
           ON CONFLICT DO NOTHING
           RETURNING *""",
        user_id, chat_id, message_id, media_group_id, origin, state,
        json.dumps(items, default=str), json.dumps(media or [], default=str),
        meal_type if meal_type in MEAL_TYPES else None, local_date, commit_key,
        settings.draft_ttl_hours,
    )
    if row is not None:
        return draft_from_row(row), True
    existing = await conn.fetchrow(
        """SELECT * FROM food_log_drafts
           WHERE user_id = $1 AND (commit_key = $2
                 OR (chat_id = $3 AND message_id = $4)
                 OR ($5::text IS NOT NULL AND media_group_id = $5))
           ORDER BY id LIMIT 1""",
        user_id, commit_key, chat_id, message_id, media_group_id,
    )
    if existing is None:
        raise LedgerError("draft_conflict")
    return draft_from_row(existing), False


async def get_draft(conn: Any, draft_id: int, user_id: int) -> Optional[dict]:
    row = await conn.fetchrow(
        "SELECT * FROM food_log_drafts WHERE id = $1 AND user_id = $2", draft_id, user_id,
    )
    return draft_from_row(row) if row else None


async def save_draft(
    conn: Any,
    draft: dict,
    *,
    items: Optional[list[dict]] = None,
    state: Optional[str] = None,
    reply_message_id: Optional[int] = None,
    meal_type: Optional[str] = None,
    local_date: Optional[date] = None,
    media: Optional[list] = None,
    error: Optional[str] = None,
) -> dict:
    """Optimistic update: fails with VersionConflict if someone else changed it."""
    new_items = items if items is not None else draft["items"]
    new_state = state or evaluate_items(new_items)
    row = await conn.fetchrow(
        """UPDATE food_log_drafts
           SET items = $4::jsonb, state = $5,
               reply_message_id = COALESCE($6, reply_message_id),
               meal_type = COALESCE($7::meal_type, meal_type),
               local_date = COALESCE($8, local_date),
               media = COALESCE($9::jsonb, media),
               error = $10,
               version = version + 1
           WHERE id = $1 AND user_id = $2 AND version = $3
             AND state NOT IN ('committed', 'cancelled', 'expired')
           RETURNING *""",
        draft["id"], draft["user_id"], draft["version"], json.dumps(new_items, default=str),
        new_state, reply_message_id, meal_type if meal_type in MEAL_TYPES else None, local_date,
        json.dumps(media, default=str) if media is not None else None, error,
    )
    if row is None:
        current = await conn.fetchrow(
            "SELECT version, state FROM food_log_drafts WHERE id = $1", draft["id"],
        )
        if current is None:
            raise LedgerError("draft_not_found")
        if current["state"] in ("committed", "cancelled", "expired"):
            raise LedgerError(f"draft_{current['state']}")
        raise VersionConflict(current["version"])
    return draft_from_row(row)


async def set_reply_message(conn: Any, draft_id: int, user_id: int, reply_message_id: int) -> None:
    """Link the bot's reply to the draft (not a user edit: no version bump)."""
    await conn.execute(
        "UPDATE food_log_drafts SET reply_message_id = $3 WHERE id = $1 AND user_id = $2",
        draft_id, user_id, reply_message_id,
    )


async def cancel_draft(conn: Any, draft_id: int, user_id: int) -> bool:
    status = await conn.execute(
        """UPDATE food_log_drafts SET state = 'cancelled', version = version + 1
           WHERE id = $1 AND user_id = $2 AND state = ANY($3::text[])""",
        draft_id, user_id, list(OPEN_DRAFT_STATES),
    )
    return status.endswith(" 1")


async def find_reply_draft(conn: Any, user_id: int, chat_id: int, reply_to_message_id: int) -> Optional[dict]:
    row = await conn.fetchrow(
        """SELECT * FROM food_log_drafts
           WHERE user_id = $1 AND chat_id = $2
             AND (reply_message_id = $3 OR message_id = $3)
             AND state = ANY($4::text[]) AND expires_at > NOW()
           ORDER BY id DESC LIMIT 1""",
        user_id, chat_id, reply_to_message_id, list(OPEN_DRAFT_STATES),
    )
    return draft_from_row(row) if row else None


async def open_drafts(conn: Any, user_id: int, *, states: Optional[tuple] = None) -> list[dict]:
    rows = await conn.fetch(
        """SELECT * FROM food_log_drafts
           WHERE user_id = $1 AND state = ANY($2::text[]) AND expires_at > NOW()
           ORDER BY id DESC LIMIT 20""",
        user_id, list(states or OPEN_DRAFT_STATES),
    )
    return [draft_from_row(r) for r in rows]


async def commit_draft(
    pool: Any, ctx: UserContext, draft_id: int, *, expected_version: Optional[int] = None,
) -> tuple[dict, list[dict]]:
    """Commit a ready draft exactly once. Returns ``(draft, entries)``.

    Items are prepared first (possibly refreshing FatSecret data), then a
    transaction locks the draft; a draft already committed by a concurrent
    Confirm returns its existing entries.
    """
    async with pool.acquire() as conn:
        draft = await get_draft(conn, draft_id, ctx.user_id)
    if draft is None:
        raise LedgerError("draft_not_found")
    if draft["state"] == "committed":
        return draft, await _entries_by_ids(pool, ctx.user_id, draft["committed_entry_ids"])
    if draft["state"] in ("cancelled", "expired", "failed"):
        raise LedgerError(f"draft_{draft['state']}")
    if expected_version is not None and draft["version"] != expected_version:
        raise VersionConflict(draft["version"])
    if evaluate_items(draft["items"]) != "ready":
        raise LedgerError("draft_not_ready")

    prepared: list[PreparedItem] = []
    async with pool.acquire() as conn:
        for item in draft["items"]:
            sel = item["selected"]
            prepared.append(await prepare_item(
                conn, ctx,
                product_id=int(sel["product_id"]),
                grams=item["grams"],
                quantity_source=item.get("quantity_source") or "explicit",
                serving_id=sel.get("serving_id"),
                alias_text=item.get("text") if item.get("learn", True) else None,
                label=sel.get("label"),
            ))

    local_date = draft.get("local_date") or ctx.today()
    meal_type = draft.get("meal_type") or default_meal_type(datetime.now(ctx.tz))
    async with pool.acquire() as conn:
        async with conn.transaction():
            locked = await conn.fetchrow(
                "SELECT * FROM food_log_drafts WHERE id = $1 AND user_id = $2 FOR UPDATE",
                draft_id, ctx.user_id,
            )
            locked = draft_from_row(locked)
            if locked["state"] == "committed":
                entries = await _entries_by_ids(conn, ctx.user_id, locked["committed_entry_ids"])
                return locked, entries
            if locked["version"] != draft["version"]:
                raise VersionConflict(locked["version"])
            origin = locked["origin"] if locked["origin"] in ORIGINS else "bot_text"
            entries = []
            for index, item in enumerate(prepared):
                entry, _ = await _insert_entry(
                    conn, ctx, item, meal_type=meal_type, local_date=local_date, origin=origin,
                    idempotency_key=f"{locked['commit_key']}:{index}", draft_id=draft_id,
                )
                entries.append(entry)
            row = await conn.fetchrow(
                """UPDATE food_log_drafts
                   SET state = 'committed', committed_entry_ids = $2::int[], version = version + 1
                   WHERE id = $1 RETURNING *""",
                draft_id, [e["id"] for e in entries],
            )
    return draft_from_row(row), entries


async def _entries_by_ids(conn: Any, user_id: int, ids: list[int]) -> list[dict]:
    if not ids:
        return []
    rows = await conn.fetch(
        f"SELECT {_ENTRY_COLUMNS} FROM food_entries WHERE user_id = $1 AND id = ANY($2::int[]) ORDER BY id",
        user_id, list(ids),
    )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Revisions: void / edit / undo
# ---------------------------------------------------------------------------

async def get_entry(conn: Any, user_id: int, entry_id: int) -> Optional[dict]:
    row = await conn.fetchrow(
        f"""SELECT {_ENTRY_COLUMNS}, remote_food_id, remote_serving_id, remote_units, grams,
                   quantity_source, preparation
            FROM food_entries WHERE id = $1 AND user_id = $2""",
        entry_id, user_id,
    )
    return dict(row) if row else None


async def void_entry(
    pool: Any, ctx: UserContext, entry_id: int, *, expected_version: Optional[int] = None,
) -> dict:
    """Undo one entry. Linked remote entries get an outbox delete; a pending
    (never dispatched) create is simply cancelled."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            entry = await conn.fetchrow(
                """SELECT id, entry_status, version, revision, remote_entry_id, sync_status
                   FROM food_entries WHERE id = $1 AND user_id = $2 FOR UPDATE""",
                entry_id, ctx.user_id,
            )
            if entry is None:
                raise LedgerError("entry_not_found")
            if entry["entry_status"] == "voided":
                return await get_entry(conn, ctx.user_id, entry_id)
            if expected_version is not None and entry["version"] != expected_version:
                raise VersionConflict(entry["version"])
            cancelled = await conn.fetchval(
                """UPDATE food_sync_outbox SET status = 'cancelled'
                   WHERE food_entry_id = $1 AND operation = 'create' AND status = 'pending'
                   RETURNING id""",
                entry_id,
            )
            in_flight = await conn.fetchval(
                """SELECT 1 FROM food_sync_outbox
                   WHERE food_entry_id = $1 AND operation = 'create'
                     AND status IN ('sending', 'unknown')""",
                entry_id,
            )
            new_revision = entry["revision"] + 1
            needs_remote_delete = bool(entry["remote_entry_id"]) or bool(in_flight)
            sync_status = "delete_pending" if needs_remote_delete else (
                "local_only" if cancelled else entry["sync_status"]
            )
            await conn.execute(
                """UPDATE food_entries
                   SET entry_status = 'voided', voided_at = NOW(), revision = $2,
                       version = version + 1, sync_status = $3
                   WHERE id = $1""",
                entry_id, new_revision, sync_status,
            )
            if needs_remote_delete:
                await conn.execute(
                    """INSERT INTO food_sync_outbox (user_id, food_entry_id, entry_revision, operation)
                       VALUES ($1, $2, $3, 'delete')
                       ON CONFLICT (food_entry_id, entry_revision, operation) DO NOTHING""",
                    ctx.user_id, entry_id, new_revision,
                )
            return await get_entry(conn, ctx.user_id, entry_id)


async def undo_last(pool: Any, ctx: UserContext) -> Optional[dict]:
    async with pool.acquire() as conn:
        entry_id = await conn.fetchval(
            """SELECT id FROM food_entries
               WHERE user_id = $1 AND entry_status = 'committed'
               ORDER BY created_at DESC, id DESC LIMIT 1""",
            ctx.user_id,
        )
    if entry_id is None:
        return None
    return await void_entry(pool, ctx, entry_id)


async def edit_entry(
    pool: Any,
    ctx: UserContext,
    entry_id: int,
    *,
    expected_version: int,
    grams: Any = None,
    meal_type: Optional[str] = None,
    local_date: Any = None,
    product_id: Optional[int] = None,
    serving_id: Optional[str] = None,
) -> dict:
    """Create a new revision of one entry (never another meal).

    Grams/meal changes on a synced entry → remote edit; product/date changes
    → remote delete + create (FatSecret cannot move/replace an entry).
    """
    async with pool.acquire() as conn:
        current = await get_entry(conn, ctx.user_id, entry_id)
    if current is None:
        raise LedgerError("entry_not_found")
    if current["entry_status"] != "committed":
        raise LedgerError("entry_voided")
    if current["version"] != expected_version:
        raise VersionConflict(current["version"])
    new_product = int(product_id) if product_id else current["product_id"]
    if new_product is None:
        raise LedgerError("legacy_entry_not_editable")
    new_grams = validate_grams(grams) if grams is not None else current["grams"]
    if new_grams is None:
        raise LedgerError("grams_invalid")
    new_meal = meal_type or current["meal_type"]
    if new_meal not in MEAL_TYPES:
        raise LedgerError("meal_type_invalid")
    new_date = validate_local_date(local_date, ctx) if local_date is not None else current["local_date"]

    async with pool.acquire() as conn:
        prepared = await prepare_item(
            conn, ctx, product_id=new_product, grams=new_grams,
            serving_id=serving_id or (current["remote_serving_id"] if new_product == current["product_id"] else None),
            label=None if product_id else current["food_name"],
        )

    replace_remote = (new_product != current["product_id"]) or (new_date != current["local_date"])
    async with pool.acquire() as conn:
        async with conn.transaction():
            locked = await conn.fetchrow(
                """SELECT version, revision, remote_entry_id, sync_status, entry_status
                   FROM food_entries WHERE id = $1 AND user_id = $2 FOR UPDATE""",
                entry_id, ctx.user_id,
            )
            if locked["version"] != expected_version or locked["entry_status"] != "committed":
                raise VersionConflict(locked["version"])
            revision = locked["revision"] + 1
            has_remote = bool(locked["remote_entry_id"])
            in_flight = await conn.fetchval(
                """SELECT 1 FROM food_sync_outbox WHERE food_entry_id = $1
                   AND operation = 'create' AND status IN ('sending', 'unknown')""",
                entry_id,
            )
            if in_flight:
                raise LedgerError("sync_in_progress")
            await conn.execute(
                """UPDATE food_sync_outbox SET status = 'cancelled'
                   WHERE food_entry_id = $1 AND status = 'pending'""",
                entry_id,
            )
            ops: list[str] = []
            if prepared.remote and ctx.export_enabled:
                if has_remote and replace_remote:
                    ops = ["delete", "create"]
                elif has_remote:
                    ops = ["edit"]
                else:
                    ops = ["create"]
            elif has_remote:
                ops = ["delete"]
            sync_status = "pending" if ops and ops != ["delete"] else (
                "delete_pending" if ops == ["delete"] else
                ("not_supported" if ctx.export_enabled else "local_only")
            )
            p = prepared.portion
            await conn.execute(
                """UPDATE food_entries
                   SET product_id = $2, food_name = $3, grams = $4, serving_size = $4,
                       calories = $5, protein = $6, fat = $7, carbs = $8, fiber = $9,
                       meal_type = $10::meal_type, local_date = $11,
                       nutrition_version_id = $12, nutrition_source = $13,
                       nutrition_expires_at = $14, remote_food_id = $15,
                       remote_serving_id = $16, remote_units = $17,
                       remote_provider = CASE WHEN $15::varchar IS NULL THEN remote_provider ELSE 'fatsecret' END,
                       sync_status = $18, revision = $19, version = version + 1
                   WHERE id = $1""",
                entry_id, new_product, prepared.label, new_grams,
                p.energy_kcal, p.protein_g, p.fat_g, p.carbs_g, p.fiber_g,
                new_meal, new_date, prepared.nutrition_version_id, prepared.nutrition_source,
                prepared.nutrition_expires_at if prepared.nutrition_source == "fatsecret" else None,
                prepared.remote["food_id"] if prepared.remote else None,
                prepared.remote["serving_id"] if prepared.remote else None,
                prepared.remote["units"] if prepared.remote else None,
                sync_status, revision,
            )
            for op in ops:
                await conn.execute(
                    """INSERT INTO food_sync_outbox (user_id, food_entry_id, entry_revision, operation)
                       VALUES ($1, $2, $3, $4)
                       ON CONFLICT (food_entry_id, entry_revision, operation) DO NOTHING""",
                    ctx.user_id, entry_id, revision, op,
                )
            return await get_entry(conn, ctx.user_id, entry_id)


# ---------------------------------------------------------------------------
# Daily view
# ---------------------------------------------------------------------------

@dataclass
class DailyView:
    local_date: date
    total_kcal: Decimal
    protein_g: Decimal
    fat_g: Decimal
    carbs_g: Decimal
    entries: list[dict]
    partial: bool
    reasons: list[str]
    remote_ok: bool
    remote_connected: bool
    expired: bool = False

    @property
    def total_rounded(self) -> int:
        return int(self.total_kcal.to_integral_value())

    def to_json(self) -> dict:
        return {
            "local_date": self.local_date.isoformat(),
            "total_kcal": str(q1(self.total_kcal)),
            "protein_g": str(q1(self.protein_g)),
            "fat_g": str(q1(self.fat_g)),
            "carbs_g": str(q1(self.carbs_g)),
            "partial": self.partial,
            "reasons": self.reasons,
            "remote_connected": self.remote_connected,
            "remote_ok": self.remote_ok,
            "entries": [
                {k: (str(v) if isinstance(v, Decimal) else v.isoformat() if isinstance(v, (date, datetime)) else v)
                 for k, v in e.items()}
                for e in self.entries
            ],
        }


def _dec(value: Any) -> Optional[Decimal]:
    return to_decimal(value)


def merge_daily(
    local_rows: list[dict],
    remote_entries: Optional[list[dict]],
    *,
    local_date: date,
    remote_connected: bool,
) -> DailyView:
    """Pure union of local events and remote entries (plan §7.5, AC-10).

    - remote linked to a committed local entry → counted once (remote values
      when synced, local values if a local revision is still pending);
    - remote linked to a voided local entry → excluded (delete pending/failed);
    - remote-only → counted;
    - local definitely not remote (local_only/not_supported/failed/pending
      never dispatched) → counted;
    - local with ambiguous sync (sending/unknown) → counted once, a matching
      unlinked remote entry is suppressed, total marked partial.
    """
    reasons: list[str] = []
    entries: list[dict] = []
    remote_ok = remote_entries is not None
    if remote_connected and not remote_ok:
        reasons.append("provider_unavailable")

    committed = [r for r in local_rows if r["entry_status"] == "committed"]
    voided_remote_ids = {
        str(r["remote_entry_id"]) for r in local_rows
        if r["entry_status"] == "voided" and r.get("remote_entry_id")
    }
    if any(r["entry_status"] == "voided" and r.get("sync_status") == "delete_failed" for r in local_rows):
        reasons.append("remote_delete_failed")
    by_remote_id = {str(r["remote_entry_id"]): r for r in committed if r.get("remote_entry_id")}
    remote_list = list(remote_entries or [])
    remote_ids = {str(e.get("food_entry_id")) for e in remote_list}
    suppressed: set[str] = set()

    for row in committed:
        status = row.get("sync_status")
        if status in ("sending", "unknown") or row.get("outbox_status") in ("sending", "unknown"):
            reasons.append("sync_ambiguous")
            matches = [
                e for e in remote_list
                if str(e.get("food_entry_id")) not in by_remote_id
                and str(e.get("food_entry_id")) not in voided_remote_ids
                and str(e.get("food_entry_id")) not in suppressed
                and str(e.get("food_id")) == str(row.get("remote_food_id") or "")
                and str(e.get("serving_id")) == str(row.get("remote_serving_id") or "")
            ]
            if len(matches) == 1:
                suppressed.add(str(matches[0].get("food_entry_id")))

    for row in committed:
        kcal = _dec(row.get("calories"))
        remote_id = str(row["remote_entry_id"]) if row.get("remote_entry_id") else None
        source = "local"
        if remote_id and remote_ok and remote_id in remote_ids and row.get("sync_status") == "synced":
            remote = next(e for e in remote_list if str(e.get("food_entry_id")) == remote_id)
            kcal = _dec(remote.get("calories"))
            macros = (_dec(remote.get("protein")), _dec(remote.get("fat")), _dec(remote.get("carbohydrate")))
            source = "fatsecret"
        else:
            macros = (_dec(row.get("protein")), _dec(row.get("fat")), _dec(row.get("carbs")))
            if remote_id and remote_ok and remote_id not in remote_ids and row.get("sync_status") == "synced":
                reasons.append("remote_entry_missing")
        if kcal is None:
            reasons.append("nutrition_unavailable")
        entries.append({
            "id": row["id"], "remote_entry_id": remote_id, "name": row.get("food_name"),
            "grams": row.get("grams"), "meal_type": row.get("meal_type"),
            "energy_kcal": kcal, "protein_g": macros[0], "fat_g": macros[1], "carbs_g": macros[2],
            "source": source, "sync_status": row.get("sync_status"), "version": row.get("version"),
            "origin": row.get("origin"),
        })

    for remote in remote_list:
        rid = str(remote.get("food_entry_id"))
        if rid in by_remote_id or rid in voided_remote_ids or rid in suppressed:
            continue
        kcal = _dec(remote.get("calories"))
        if kcal is None:
            reasons.append("nutrition_unavailable")
        entries.append({
            "id": None, "remote_entry_id": rid, "name": remote.get("name"),
            "grams": None, "meal_type": remote.get("meal"),
            "energy_kcal": kcal, "protein_g": _dec(remote.get("protein")),
            "fat_g": _dec(remote.get("fat")), "carbs_g": _dec(remote.get("carbohydrate")),
            "source": "fatsecret_only", "sync_status": "remote", "version": None, "origin": "fatsecret",
        })

    def total(key: str) -> Decimal:
        return sum((e[key] for e in entries if e.get(key) is not None), Decimal(0))

    unique_reasons = list(dict.fromkeys(reasons))
    return DailyView(
        local_date=local_date,
        total_kcal=total("energy_kcal"),
        protein_g=total("protein_g"),
        fat_g=total("fat_g"),
        carbs_g=total("carbs_g"),
        entries=entries,
        partial=bool(unique_reasons),
        reasons=unique_reasons,
        remote_ok=remote_ok,
        remote_connected=remote_connected,
    )


async def daily_view(pool: Any, ctx: UserContext, local_date: Optional[date] = None) -> DailyView:
    """Local ledger ∪ live FatSecret diary for one local calendar date.

    ``pool`` may be a pool or a connection (only fetch/execute are used).
    """
    from app.services.fatsecret_api import (
        FatSecretAuthError, clear_fatsecret_tokens, fatsecret_date, fetch_food_entries,
    )
    import httpx

    local_date = local_date or ctx.today()
    rows = await pool.fetch(
        """SELECT fe.id, fe.food_name, fe.calories, fe.protein, fe.fat, fe.carbs, fe.grams,
                  fe.meal_type::text AS meal_type, fe.entry_status, fe.sync_status,
                  fe.remote_entry_id, fe.remote_food_id, fe.remote_serving_id,
                  fe.version, fe.origin,
                  (SELECT o.status FROM food_sync_outbox o
                   WHERE o.food_entry_id = fe.id AND o.operation = 'create'
                   ORDER BY o.id DESC LIMIT 1) AS outbox_status
           FROM food_entries fe
           WHERE fe.user_id = $1
             AND (fe.local_date = $2
                  OR (fe.local_date IS NULL AND (fe.logged_at AT TIME ZONE $3)::date = $2))
           ORDER BY fe.created_at, fe.id""",
        ctx.user_id, local_date, ctx.tz.key,
    )
    local_rows = [dict(r) for r in (rows or [])]
    remote: Optional[list[dict]] = None
    expired = False
    if ctx.fs_connected:
        try:
            remote = await fetch_food_entries(ctx.fs_token, ctx.fs_secret, fatsecret_date(local_date))
        except FatSecretAuthError:
            expired = True
            await clear_fatsecret_tokens(pool, ctx.user_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                expired = True
                await clear_fatsecret_tokens(pool, ctx.user_id)
            else:
                logger.warning("FatSecret diary unavailable for user_id=%s: HTTP %s",
                               ctx.user_id, exc.response.status_code)
        except Exception:
            logger.warning("FatSecret diary unavailable for user_id=%s", ctx.user_id, exc_info=True)
    view = merge_daily(local_rows, remote, local_date=local_date, remote_connected=ctx.fs_connected)
    view.expired = expired
    return view
