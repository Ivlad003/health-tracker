"""Product identities, My Products membership, default rules and caches.

Plan §6, §14, FR-14/15/16. All functions take an asyncpg pool *or*
connection (both expose fetch/fetchrow/fetchval/execute) so callers can run
them inside their own transaction.

Storage policy (plan §3): FatSecret IDs are permanent; FatSecret names and
nutrition are cached until ``provider_cached_until`` / ``expires_at``
(≤ 24 h) and purged. User-authored text (display names, aliases, label
values) and Open Food Facts revisions are durable, with provenance.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable, Optional

from app.config import settings
from app.services.food_nutrition import (
    NutritionBasis,
    basis_from_fatsecret_serving,
    fatsecret_units_for_grams,
    make_basis,
    per_100g,
    q1,
    to_decimal,
)

PREPARATIONS = ("raw", "cooked", "as_sold", "prepared", "unknown")
AUTOMATIC_ORIGINS = ("fatsecret_history", "bot", "label", "barcode")


class CatalogError(ValueError):
    """Ownership / validation / version errors (code in ``args[0]``)."""


class VersionConflict(CatalogError):
    def __init__(self, current: Optional[int] = None):
        super().__init__("version_conflict")
        self.current = current


# ---------------------------------------------------------------------------
# Text normalization
# ---------------------------------------------------------------------------

_QTY_RE = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:кг|kg|г|гр|грам\w*|g|gr|grams?|oz|мл|ml|л|l|шт|pcs?)?\b",
    re.IGNORECASE,
)
_PUNCT_RE = re.compile(r"[^\w%\s]", re.UNICODE)


def normalize_alias(text: Optional[str]) -> str:
    """Lowercase, strip quantities/punctuation, unify apostrophes and spaces."""
    if not text:
        return ""
    value = unicodedata.normalize("NFKC", str(text)).lower()
    value = value.replace("ё", "е").replace("’", "'").replace("ʼ", "'").replace("`", "'")
    value = value.replace("'", "")
    value = _QTY_RE.sub(" ", value)
    value = _PUNCT_RE.sub(" ", value)
    return " ".join(value.split())[:255]


def tokens(text: Optional[str]) -> list[str]:
    return [t for t in normalize_alias(text).split() if len(t) > 1 or t.isdigit()]


_FAT_RE = re.compile(r"(\d{1,2}(?:[.,]\d{1,2})?)\s*%")


def extract_fat_pct(text: Optional[str]) -> Optional[Decimal]:
    if not text:
        return None
    match = _FAT_RE.search(str(text))
    return to_decimal(match.group(1)) if match else None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cache_until() -> datetime:
    return _now() + timedelta(hours=settings.fatsecret_cache_hours)


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------

async def upsert_fatsecret_product(
    conn: Any, food_id: str, *, name: Optional[str] = None, brand: Optional[str] = None,
) -> int:
    """Shared FatSecret identity; provider name/brand cached with an expiry."""
    if not food_id or not str(food_id).isdigit():
        raise CatalogError("fatsecret_food_id_invalid")
    return await conn.fetchval(
        """INSERT INTO food_products (provider, external_id, provider_name, provider_brand,
                                      provider_cached_until)
           VALUES ('fatsecret', $1, $2, $3, $4)
           ON CONFLICT (provider, external_id)
               WHERE owner_user_id IS NULL AND external_id IS NOT NULL
           DO UPDATE SET
               provider_name = COALESCE(EXCLUDED.provider_name, food_products.provider_name),
               provider_brand = CASE WHEN EXCLUDED.provider_name IS NULL
                                     THEN food_products.provider_brand
                                     ELSE EXCLUDED.provider_brand END,
               provider_cached_until = COALESCE(EXCLUDED.provider_cached_until,
                                                food_products.provider_cached_until)
           RETURNING id""",
        str(food_id), name or None, brand or None, _cache_until() if name else None,
    )


def _basis_args(basis: NutritionBasis) -> tuple:
    return (
        basis.basis_quantity, basis.basis_unit, basis.grams_per_basis,
        basis.energy_kcal, basis.protein_g, basis.fat_g, basis.carbs_g,
        basis.fiber_g, basis.sugar_g, basis.salt_g,
    )


async def store_fatsecret_servings(conn: Any, product_id: int, servings: Iterable[dict]) -> int:
    """Cache structured mass servings for ≤ FATSECRET_CACHE_HOURS. Returns count."""
    stored = 0
    expires = _cache_until()
    for serving in servings:
        serving_id = str(serving.get("serving_id") or "")
        if serving_id in ("", "0"):
            continue
        basis = basis_from_fatsecret_serving(serving)
        if basis is None:
            continue
        units_per_basis = None
        try:
            units_per_basis = fatsecret_units_for_grams(serving, basis.grams_per_basis)
        except Exception:
            units_per_basis = None
        await conn.execute(
            """INSERT INTO food_nutrition_versions
                   (product_id, basis_quantity, basis_unit, grams_per_basis,
                    energy_kcal, protein_g, fat_g, carbs_g, fiber_g, sugar_g, salt_g,
                    serving_id, serving_description, fatsecret_units_per_basis,
                    source, storage_policy, expires_at, retrieved_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14,
                       'fatsecret', 'provider_cache', $15, NOW())
               ON CONFLICT (product_id, COALESCE(owner_user_id, 0), COALESCE(serving_id, ''))
                   WHERE is_current
               DO UPDATE SET
                   basis_quantity = EXCLUDED.basis_quantity, basis_unit = EXCLUDED.basis_unit,
                   grams_per_basis = EXCLUDED.grams_per_basis, energy_kcal = EXCLUDED.energy_kcal,
                   protein_g = EXCLUDED.protein_g, fat_g = EXCLUDED.fat_g,
                   carbs_g = EXCLUDED.carbs_g, fiber_g = EXCLUDED.fiber_g,
                   sugar_g = EXCLUDED.sugar_g, salt_g = EXCLUDED.salt_g,
                   serving_description = EXCLUDED.serving_description,
                   fatsecret_units_per_basis = EXCLUDED.fatsecret_units_per_basis,
                   expires_at = EXCLUDED.expires_at, retrieved_at = NOW()""",
            product_id, *_basis_args(basis), serving_id,
            str(serving.get("description") or "")[:255] or None,
            units_per_basis, expires,
        )
        stored += 1
    return stored


async def refresh_fatsecret_product(
    conn: Any,
    product_id: int,
    food_id: str,
    *,
    access_token: Optional[str] = None,
    access_secret: Optional[str] = None,
) -> dict:
    """Fetch ``food.get`` and refresh cached name + servings. Network call:
    never run it while holding a transaction open.

    The user's token is tried first so foods that exist only in their diary
    (public ``food.get`` answers 106) still refresh.
    """
    from app.services.fatsecret_api import FatSecretAPIError, get_food_details

    details = None
    if access_token and access_secret:
        try:
            details = await get_food_details(
                food_id, access_token=access_token, access_secret=access_secret,
            )
        except FatSecretAPIError:
            details = None
    if details is None:
        details = await get_food_details(food_id)
    await conn.execute(
        """UPDATE food_products
           SET provider_name = $2, provider_brand = $3, provider_cached_until = $4
           WHERE id = $1""",
        product_id, details.get("name") or None, details.get("brand") or None, _cache_until(),
    )
    await store_fatsecret_servings(conn, product_id, details.get("servings") or [])
    return details


async def create_starter_product(
    conn: Any,
    *,
    name: str,
    created_by_user_id: int,
    brand: Optional[str] = None,
    preparation: str = "unknown",
) -> int:
    """Shared (owner-less) starter product; nutrition is added as a revision."""
    return await conn.fetchval(
        """INSERT INTO food_products (provider, name, brand, preparation, is_starter,
                                      created_by_user_id)
           VALUES ('manual', $1, $2, $3, TRUE, $4) RETURNING id""",
        name, brand, preparation, created_by_user_id,
    )


async def create_personal_product(
    conn: Any,
    user_id: int,
    *,
    name: str,
    basis: Optional[NutritionBasis],
    provider: str = "manual",
    brand: Optional[str] = None,
    preparation: str = "unknown",
    barcode: Optional[str] = None,
    barcode_symbology: Optional[str] = None,
    origin: str = "manual",
    source: Optional[str] = None,
    usual_portion_g: Optional[Decimal] = None,
) -> dict:
    """User-owned product (+ durable nutrition revision + active membership).

    Creating a product never creates a consumption event (FR-14).
    """
    name = (name or "").strip()
    if not name or len(name) > 255:
        raise CatalogError("name_invalid")
    if preparation not in PREPARATIONS:
        raise CatalogError("preparation_invalid")
    if provider not in ("manual", "label", "recipe"):
        raise CatalogError("provider_invalid")
    product_id = await conn.fetchval(
        """INSERT INTO food_products (owner_user_id, provider, name, brand, preparation,
                                      barcode, barcode_symbology, created_by_user_id)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $1)
           RETURNING id""",
        user_id, provider, name, (brand or "").strip() or None, preparation,
        barcode, barcode_symbology,
    )
    version_id = None
    if basis is not None:
        version_id = await add_nutrition_revision(
            conn, product_id, basis, source=source or provider, owner_user_id=user_id,
            created_by_user_id=user_id,
        )
    await upsert_membership(
        conn, user_id, product_id, origin, display_name=name, explicit=True,
        usual_portion_g=usual_portion_g,
    )
    return {"product_id": product_id, "nutrition_version_id": version_id}


async def add_nutrition_revision(
    conn: Any,
    product_id: int,
    basis: NutritionBasis,
    *,
    source: str,
    owner_user_id: Optional[int],
    created_by_user_id: Optional[int],
    source_revision: Optional[str] = None,
) -> int:
    """Durable revision; previous current revision in the same scope is kept
    (past meals keep referencing it) but no longer current (FR-12/16)."""
    await conn.execute(
        """UPDATE food_nutrition_versions SET is_current = FALSE
           WHERE product_id = $1 AND COALESCE(owner_user_id, 0) = COALESCE($2::int, 0)
             AND serving_id IS NULL AND is_current""",
        product_id, owner_user_id,
    )
    return await conn.fetchval(
        """INSERT INTO food_nutrition_versions
               (product_id, owner_user_id, basis_quantity, basis_unit, grams_per_basis,
                energy_kcal, protein_g, fat_g, carbs_g, fiber_g, sugar_g, salt_g,
                source, source_revision, storage_policy, created_by_user_id)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, 'durable', $15)
           RETURNING id""",
        product_id, owner_user_id, *_basis_args(basis), source, source_revision,
        created_by_user_id,
    )


def row_to_basis(row: Any) -> NutritionBasis:
    return NutritionBasis(
        basis_quantity=row["basis_quantity"],
        basis_unit=row["basis_unit"],
        grams_per_basis=row["grams_per_basis"],
        energy_kcal=row["energy_kcal"],
        protein_g=row["protein_g"],
        fat_g=row["fat_g"],
        carbs_g=row["carbs_g"],
        fiber_g=row["fiber_g"],
        sugar_g=row["sugar_g"],
        salt_g=row["salt_g"],
    )


async def current_nutrition(
    conn: Any, product_id: int, user_id: int, serving_id: Optional[str] = None,
) -> Optional[dict]:
    """Best current mass-based nutrition visible to ``user_id``.

    Order: the user's own durable revision → shared durable revision (OFF,
    starter) → unexpired FatSecret cache (requested serving first).
    Expired provider caches are never used.
    """
    row = await conn.fetchrow(
        """SELECT * FROM food_nutrition_versions
           WHERE product_id = $1 AND is_current
             AND (owner_user_id = $2 OR owner_user_id IS NULL)
             AND (expires_at IS NULL OR expires_at > NOW())
             AND grams_per_basis IS NOT NULL
           ORDER BY (owner_user_id IS NOT NULL) DESC,
                    (storage_policy = 'durable') DESC,
                    (serving_id IS NOT DISTINCT FROM $3::text) DESC,
                    (grams_per_basis = 1) DESC,
                    (grams_per_basis = 100) DESC,
                    id DESC
           LIMIT 1""",
        product_id, user_id, serving_id,
    )
    return dict(row) if row else None


async def get_product(conn: Any, product_id: int, user_id: int) -> Optional[dict]:
    """Product visible to the user (own personal or shared), with display name."""
    row = await conn.fetchrow(
        """SELECT p.*, m.id AS membership_id, m.state AS membership_state,
                  m.display_name, m.preferred_serving_id, m.usual_portion_g,
                  m.known_serving_ids, m.version AS membership_version,
                  m.confirmed_count, m.overrides
           FROM food_products p
           LEFT JOIN user_product_memberships m ON m.product_id = p.id AND m.user_id = $2
           WHERE p.id = $1 AND (p.owner_user_id IS NULL OR p.owner_user_id = $2)""",
        product_id, user_id,
    )
    if row is None:
        return None
    data = dict(row)
    data["label"] = display_label(data)
    return data


def display_label(row: dict) -> str:
    """User-authored name first; expired provider names are never shown."""
    cached_until = row.get("provider_cached_until")
    provider_fresh = cached_until is not None and cached_until > _now()
    for value in (
        row.get("display_name"),
        row.get("name"),
        row.get("provider_name") if provider_fresh else None,
    ):
        if value:
            return str(value)
    if row.get("provider") == "fatsecret":
        return f"FatSecret #{row.get('external_id')}"
    return "?"


# ---------------------------------------------------------------------------
# Membership (My Products)
# ---------------------------------------------------------------------------

async def upsert_membership(
    conn: Any,
    user_id: int,
    product_id: int,
    origin: str,
    *,
    display_name: Optional[str] = None,
    serving_id: Optional[str] = None,
    confirmed: bool = False,
    history_date: Optional[Any] = None,
    history_occurrences: int = 0,
    explicit: bool = False,
    usual_portion_g: Optional[Decimal] = None,
) -> dict:
    """Insert or update membership.

    Automatic origins (history import, bot auto-add) never resurrect an
    excluded or archived card, never replace a user-authored display name and
    never touch pinned defaults. ``explicit=True`` (user action) restores.
    Returns ``{"id", "state", "created"}``.
    """
    serving_json = json.dumps([serving_id] if serving_id and serving_id != "0" else [])
    row = await conn.fetchrow(
        """INSERT INTO user_product_memberships
               (user_id, product_id, origin, display_name, preferred_serving_id,
                known_serving_ids, confirmed_count, history_count, last_used_at,
                history_last_date, usual_portion_g)
           VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8,
                   CASE WHEN $7 > 0 THEN NOW() END, $9, $10)
           ON CONFLICT (user_id, product_id) DO UPDATE SET
               display_name = COALESCE(user_product_memberships.display_name, EXCLUDED.display_name),
               known_serving_ids = (
                   SELECT COALESCE(jsonb_agg(DISTINCT v), '[]'::jsonb)
                   FROM jsonb_array_elements(user_product_memberships.known_serving_ids
                                             || EXCLUDED.known_serving_ids) AS v
               ),
               preferred_serving_id = COALESCE(
                   CASE WHEN $7 > 0 THEN EXCLUDED.preferred_serving_id END,
                   user_product_memberships.preferred_serving_id,
                   EXCLUDED.preferred_serving_id),
               confirmed_count = user_product_memberships.confirmed_count + EXCLUDED.confirmed_count,
               history_count = user_product_memberships.history_count + EXCLUDED.history_count,
               last_used_at = CASE WHEN $7 > 0 THEN NOW()
                                   ELSE user_product_memberships.last_used_at END,
               history_last_date = GREATEST(user_product_memberships.history_last_date,
                                            EXCLUDED.history_last_date),
               usual_portion_g = COALESCE(EXCLUDED.usual_portion_g,
                                          user_product_memberships.usual_portion_g),
               state = CASE WHEN $11 THEN 'active' ELSE user_product_memberships.state END,
               excluded_at = CASE WHEN $11 THEN NULL ELSE user_product_memberships.excluded_at END,
               version = user_product_memberships.version + 1
           RETURNING id, state, (xmax = 0) AS created""",
        user_id, product_id, origin, (display_name or "").strip()[:255] or None,
        serving_id if serving_id and serving_id != "0" else None, serving_json,
        1 if confirmed else 0, int(history_occurrences), history_date, usual_portion_g, explicit,
    )
    return dict(row)


async def _set_membership_state(
    conn: Any, user_id: int, product_id: int, state: str, expected_version: Optional[int],
) -> dict:
    row = await conn.fetchrow(
        """UPDATE user_product_memberships
           SET state = $3::varchar,
               excluded_at = CASE WHEN $3::varchar = 'excluded' THEN NOW() ELSE NULL END,
               version = version + 1
           WHERE user_id = $1 AND product_id = $2
             AND ($4::int IS NULL OR version = $4)
           RETURNING id, state, version""",
        user_id, product_id, state, expected_version,
    )
    if row is None:
        exists = await conn.fetchval(
            "SELECT version FROM user_product_memberships WHERE user_id = $1 AND product_id = $2",
            user_id, product_id,
        )
        if exists is None:
            raise CatalogError("not_found")
        raise VersionConflict(exists)
    if state != "active":
        # Removing/archiving disables default rules for this product (AC-22).
        await conn.execute(
            """UPDATE food_default_rules SET enabled = FALSE, version = version + 1
               WHERE user_id = $1 AND product_id = $2 AND enabled""",
            user_id, product_id,
        )
    return dict(row)


async def exclude_product(conn: Any, user_id: int, product_id: int, expected_version: Optional[int] = None) -> dict:
    """"Remove from My Products": a durable user-owned exclusion. Does not delete
    FatSecret diary entries, shared products or past meals."""
    exists = await conn.fetchval(
        "SELECT 1 FROM user_product_memberships WHERE user_id = $1 AND product_id = $2",
        user_id, product_id,
    )
    if not exists:
        if not await product_visible(conn, user_id, product_id):
            raise CatalogError("not_found")
        await conn.execute(
            """INSERT INTO user_product_memberships (user_id, product_id, origin, state, excluded_at)
               VALUES ($1, $2, 'manual', 'excluded', NOW())
               ON CONFLICT (user_id, product_id) DO NOTHING""",
            user_id, product_id,
        )
        return {"state": "excluded"}
    return await _set_membership_state(conn, user_id, product_id, "excluded", expected_version)


async def restore_product(conn: Any, user_id: int, product_id: int, expected_version: Optional[int] = None) -> dict:
    return await _set_membership_state(conn, user_id, product_id, "active", expected_version)


async def archive_product(conn: Any, user_id: int, product_id: int, expected_version: Optional[int] = None) -> dict:
    return await _set_membership_state(conn, user_id, product_id, "archived", expected_version)


async def product_visible(conn: Any, user_id: int, product_id: int) -> bool:
    return bool(await conn.fetchval(
        """SELECT 1 FROM food_products
           WHERE id = $1 AND (owner_user_id IS NULL OR owner_user_id = $2)""",
        product_id, user_id,
    ))


async def is_excluded(conn: Any, user_id: int, product_id: int) -> bool:
    return bool(await conn.fetchval(
        """SELECT 1 FROM user_product_memberships
           WHERE user_id = $1 AND product_id = $2 AND state IN ('excluded', 'archived')""",
        user_id, product_id,
    ))


async def update_membership_fields(
    conn: Any,
    user_id: int,
    product_id: int,
    expected_version: int,
    *,
    display_name: Optional[str] = None,
    preparation: Optional[str] = None,
    usual_portion_g: Optional[Decimal] = None,
    preferred_serving_id: Optional[str] = None,
) -> dict:
    """Personal field-level overrides (kept across history refreshes)."""
    if preparation is not None and preparation not in PREPARATIONS:
        raise CatalogError("preparation_invalid")
    overrides = {
        k: v for k, v in (
            ("display_name", display_name), ("preparation", preparation),
            ("usual_portion_g", str(usual_portion_g) if usual_portion_g is not None else None),
        ) if v is not None
    }
    row = await conn.fetchrow(
        """UPDATE user_product_memberships
           SET display_name = COALESCE($4, display_name),
               preparation = COALESCE($5, preparation),
               usual_portion_g = COALESCE($6, usual_portion_g),
               preferred_serving_id = COALESCE($7, preferred_serving_id),
               overrides = overrides || jsonb_build_object('fields', $8::jsonb,
                                                           'updated_at', NOW()::text,
                                                           'source', 'user'),
               version = version + 1
           WHERE user_id = $1 AND product_id = $2 AND version = $3
           RETURNING id, version""",
        user_id, product_id, expected_version, display_name, preparation, usual_portion_g,
        preferred_serving_id, json.dumps(overrides),
    )
    if row is None:
        current = await conn.fetchval(
            "SELECT version FROM user_product_memberships WHERE user_id = $1 AND product_id = $2",
            user_id, product_id,
        )
        if current is None:
            raise CatalogError("not_found")
        raise VersionConflict(current)
    return dict(row)


# ---------------------------------------------------------------------------
# Default rules / learned aliases
# ---------------------------------------------------------------------------

async def set_default_rule(
    conn: Any,
    user_id: int,
    alias: str,
    product_id: int,
    *,
    serving_id: Optional[str] = None,
    preparation: Optional[str] = None,
    suggested_portion_g: Optional[Decimal] = None,
    priority: int = 0,
    replace_rule_id: Optional[int] = None,
    expected_version: Optional[int] = None,
) -> dict:
    """Pin ``alias → product`` (manual rule; may precede any consumption).

    Replaces the previous active manual rule for the alias only when the
    caller passes its id + version (explicit replacement, FR-15).
    """
    norm = normalize_alias(alias)
    if not norm:
        raise CatalogError("alias_invalid")
    if preparation is not None and preparation not in PREPARATIONS:
        raise CatalogError("preparation_invalid")
    if not await product_visible(conn, user_id, product_id):
        raise CatalogError("not_found")
    if await is_excluded(conn, user_id, product_id):
        raise CatalogError("product_excluded")
    existing = await conn.fetchrow(
        """SELECT id, version FROM food_default_rules
           WHERE user_id = $1 AND alias_normalized = $2 AND enabled AND origin = 'manual'""",
        user_id, norm,
    )
    if existing is not None:
        if replace_rule_id != existing["id"] or expected_version != existing["version"]:
            raise VersionConflict(existing["version"])
        await conn.execute(
            "UPDATE food_default_rules SET enabled = FALSE, version = version + 1 WHERE id = $1",
            existing["id"],
        )
    row = await conn.fetchrow(
        """INSERT INTO food_default_rules
               (user_id, alias_normalized, alias_display, product_id, serving_id, preparation,
                suggested_portion_g, origin, priority)
           VALUES ($1, $2, $3, $4, $5, $6, $7, 'manual', $8)
           RETURNING id, version""",
        user_id, norm, alias.strip()[:255], product_id, serving_id, preparation,
        suggested_portion_g, priority,
    )
    await upsert_membership(conn, user_id, product_id, "manual", explicit=False)
    return dict(row)


async def disable_rule(conn: Any, user_id: int, rule_id: int, expected_version: int) -> dict:
    row = await conn.fetchrow(
        """UPDATE food_default_rules SET enabled = FALSE, version = version + 1
           WHERE id = $1 AND user_id = $2 AND version = $3
           RETURNING id, version""",
        rule_id, user_id, expected_version,
    )
    if row is None:
        current = await conn.fetchval(
            "SELECT version FROM food_default_rules WHERE id = $1 AND user_id = $2", rule_id, user_id,
        )
        if current is None:
            raise CatalogError("not_found")
        raise VersionConflict(current)
    return dict(row)


async def learn_alias(conn: Any, user_id: int, alias: str, product_id: int, serving_id: Optional[str]) -> None:
    """Remember a real user selection (FR-06). Never touches manual rules."""
    norm = normalize_alias(alias)
    if not norm:
        return
    await conn.execute(
        """INSERT INTO food_default_rules
               (user_id, alias_normalized, alias_display, product_id, serving_id, origin, use_count)
           VALUES ($1, $2, $3, $4, $5, 'learned', 1)
           ON CONFLICT (user_id, alias_normalized, product_id) WHERE origin = 'learned'
           DO UPDATE SET use_count = food_default_rules.use_count + 1,
                         serving_id = COALESCE(EXCLUDED.serving_id, food_default_rules.serving_id),
                         enabled = TRUE""",
        user_id, norm, alias.strip()[:255], product_id, serving_id,
    )


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

async def list_products(
    conn: Any,
    user_id: int,
    *,
    query: Optional[str] = None,
    state: str = "active",
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """My Products page: membership + product + current nutrition per 100 g."""
    if state not in ("active", "excluded", "archived"):
        raise CatalogError("state_invalid")
    rows = await conn.fetch(
        """SELECT p.id AS product_id, p.provider, p.external_id, p.barcode, p.name, p.brand,
                  p.preparation AS product_preparation, p.provider_name, p.provider_brand,
                  p.provider_cached_until, p.owner_user_id,
                  m.id AS membership_id, m.origin, m.state, m.display_name, m.preparation,
                  m.usual_portion_g, m.preferred_serving_id, m.known_serving_ids,
                  m.confirmed_count, m.history_count, m.last_used_at, m.history_last_date,
                  m.provider_rank, m.version,
                  n.energy_kcal, n.protein_g, n.fat_g, n.carbs_g, n.grams_per_basis,
                  n.source AS nutrition_source, n.expires_at AS nutrition_expires_at,
                  (SELECT count(*) FROM food_default_rules r
                   WHERE r.user_id = m.user_id AND r.product_id = p.id
                     AND r.enabled AND r.origin = 'manual') AS default_rules
           FROM user_product_memberships m
           JOIN food_products p ON p.id = m.product_id
           LEFT JOIN LATERAL (
               SELECT * FROM food_nutrition_versions v
               WHERE v.product_id = p.id AND v.is_current
                 AND (v.owner_user_id = m.user_id OR v.owner_user_id IS NULL)
                 AND (v.expires_at IS NULL OR v.expires_at > NOW())
                 AND v.grams_per_basis IS NOT NULL
               ORDER BY (v.owner_user_id IS NOT NULL) DESC, (v.storage_policy = 'durable') DESC,
                        (v.grams_per_basis = 100) DESC, v.id DESC
               LIMIT 1
           ) n ON TRUE
           WHERE m.user_id = $1 AND m.state = $2
             AND ($3::text IS NULL
                  OR m.display_name ILIKE '%' || $3 || '%'
                  OR p.name ILIKE '%' || $3 || '%'
                  OR (p.provider_cached_until > NOW() AND p.provider_name ILIKE '%' || $3 || '%')
                  OR p.barcode = $3)
           ORDER BY m.confirmed_count DESC, m.last_used_at DESC NULLS LAST,
                    m.provider_rank ASC NULLS LAST, m.history_count DESC, m.id DESC
           LIMIT $4 OFFSET $5""",
        user_id, state, (query or "").strip() or None, max(1, min(limit, 200)), max(0, offset),
    )
    out = []
    for row in rows:
        data = dict(row)
        data["label"] = display_label(data)
        kcal_100 = None
        if data.get("energy_kcal") is not None and data.get("grams_per_basis"):
            kcal_100 = q1(Decimal(data["energy_kcal"]) * 100 / Decimal(data["grams_per_basis"]))
        data["kcal_per_100g"] = kcal_100
        data["incomplete"] = kcal_100 is None
        if not (data.get("provider_cached_until") and data["provider_cached_until"] > _now()):
            data["provider_name"] = None
            data["provider_brand"] = None
        if isinstance(data.get("known_serving_ids"), str):
            data["known_serving_ids"] = json.loads(data["known_serving_ids"])
        out.append(data)
    return out


# ---------------------------------------------------------------------------
# Retention / purge
# ---------------------------------------------------------------------------

async def purge_expired_provider_data(conn: Any) -> dict:
    """Enforce the FatSecret ≤ 24 h cache rule everywhere it is stored."""
    versions = await conn.execute(
        """DELETE FROM food_nutrition_versions
           WHERE storage_policy = 'provider_cache' AND expires_at < NOW()"""
    )
    products = await conn.execute(
        """UPDATE food_products
           SET provider_name = NULL, provider_brand = NULL, provider_cached_until = NULL
           WHERE provider_cached_until IS NOT NULL AND provider_cached_until < NOW()"""
    )
    entries = await conn.execute(
        """UPDATE food_entries
           SET calories = NULL, protein = NULL, fat = NULL, carbs = NULL, fiber = NULL,
               nutrition_expires_at = NULL
           WHERE nutrition_expires_at IS NOT NULL AND nutrition_expires_at < NOW()"""
    )
    drafts = await conn.execute(
        """UPDATE food_log_drafts
           SET state = 'expired', items = '[]'::jsonb, media = '[]'::jsonb
           WHERE state NOT IN ('committed', 'cancelled', 'expired', 'failed')
             AND expires_at < NOW()"""
    )
    # Drafts may embed provider snapshots in candidates: scrub closed ones.
    scrubbed = await conn.execute(
        """UPDATE food_log_drafts SET items = '[]'::jsonb, media = '[]'::jsonb
           WHERE state IN ('committed', 'cancelled', 'failed')
             AND updated_at < NOW() - make_interval(hours => $1)
             AND items <> '[]'::jsonb""",
        settings.fatsecret_cache_hours,
    )
    lookups = await conn.execute("DELETE FROM external_lookup_cache WHERE expires_at < NOW()")
    return {
        "versions": versions, "products": products, "entries": entries,
        "drafts": drafts, "scrubbed": scrubbed, "lookups": lookups,
    }


def kcal_per_100g(basis: Optional[NutritionBasis]) -> Optional[Decimal]:
    if basis is None:
        return None
    per = per_100g(basis)
    return q1(per.energy_kcal) if per and per.energy_kcal is not None else None


def basis_from_payload(payload: dict) -> NutritionBasis:
    """Validated basis from API/user input (label edits, manual products)."""
    return make_basis(
        basis_quantity=payload.get("basis_quantity", 100),
        basis_unit=payload.get("basis_unit", "g"),
        grams_per_basis=payload.get("grams_per_basis"),
        energy_kcal=payload.get("energy_kcal"),
        energy_kj=payload.get("energy_kj"),
        protein_g=payload.get("protein_g"),
        fat_g=payload.get("fat_g"),
        carbs_g=payload.get("carbs_g"),
        fiber_g=payload.get("fiber_g"),
        sugar_g=payload.get("sugar_g"),
        salt_g=payload.get("salt_g"),
    )
