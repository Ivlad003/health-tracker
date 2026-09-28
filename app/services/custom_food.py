"""Personal foods created when search and history have nothing.

The user types kcal per 100 g. OpenAI estimates protein, fat and carbs and
does not replace that energy figure. One FatSecret ``food.create`` attempt
follows; a refusal stays on the product until the user retries from history.
"""
from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any, Optional

from openai import AsyncOpenAI

from app.config import settings
from app.services import food_catalog as catalog
from app.services.food_catalog import normalize_alias
from app.services.food_nutrition import (
    NutritionBasis,
    choose_gram_serving,
    fatsecret_units_for_grams,
    q1,
    to_decimal,
)

logger = logging.getLogger(__name__)

_client = AsyncOpenAI(api_key=settings.openai_api_key)


def parse_macro_estimate(raw: str) -> Optional[dict[str, Decimal]]:
    """Three non-negative decimals per 100 g. Anything else is a failed estimate."""
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    out: dict[str, Decimal] = {}
    for key in ("protein_g", "fat_g", "carbs_g"):
        value = to_decimal(payload.get(key))
        if value is None or value < 0 or value > 100:
            return None
        out[key] = q1(value)
    return out


async def estimate_macros(name: str, kcal_per_100g: Decimal) -> Optional[dict[str, Decimal]]:
    """Ask the model for protein, fat and carbs. The caller's kcal stays put."""
    prompt = (
        "Estimate protein_g, fat_g and carbs_g per 100 g for the food below. "
        "The energy figure is fixed and must not be changed. "
        "Reply with JSON only: {\"protein_g\": number, \"fat_g\": number, \"carbs_g\": number}. "
        f"Food: {name}. energy_kcal per 100 g: {kcal_per_100g}."
    )
    try:
        response = await _client.chat.completions.create(
            model=settings.openai_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=120,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or ""
    except Exception:
        logger.warning("Macro estimate failed", exc_info=True)
        return None
    return parse_macro_estimate(raw)


async def find_personal_named(conn: Any, user_id: int, name: str) -> Optional[int]:
    target = normalize_alias(name)
    if not target:
        return None
    rows = await conn.fetch(
        """SELECT id, name FROM food_products
           WHERE owner_user_id = $1 AND status = 'active' AND provider = 'manual'""",
        user_id,
    )
    for row in rows:
        if normalize_alias(row["name"]) == target:
            return int(row["id"])
    return None


def _basis(kcal: Decimal, macros: Optional[dict[str, Decimal]]) -> NutritionBasis:
    macros = macros or {}
    return NutritionBasis(
        basis_quantity=Decimal(100),
        basis_unit="g",
        grams_per_basis=Decimal(100),
        energy_kcal=kcal,
        protein_g=macros.get("protein_g"),
        fat_g=macros.get("fat_g"),
        carbs_g=macros.get("carbs_g"),
    )


async def _set_state(conn: Any, product_id: int, state: Optional[str], food_id: Optional[str] = None,
                      serving_id: Optional[str] = None) -> None:
    await conn.execute(
        """UPDATE food_products
           SET custom_fs_state = $2,
               custom_fs_food_id = COALESCE($3, custom_fs_food_id),
               custom_fs_serving_id = COALESCE($4, custom_fs_serving_id)
           WHERE id = $1""",
        product_id, state, food_id, serving_id,
    )


async def save_manual_nutrition(
    conn: Any, user_id: int, product_id: int, kcal: Decimal, macros: Optional[dict[str, Decimal]],
) -> None:
    await catalog.add_nutrition_revision(
        conn, product_id, _basis(kcal, macros), source="manual",
        owner_user_id=user_id, created_by_user_id=user_id,
    )


async def create_personal(
    conn: Any, user_id: int, name: str, kcal: Decimal, macros: Optional[dict[str, Decimal]],
) -> int:
    created = await catalog.create_personal_product(
        conn, user_id, name=name, basis=_basis(kcal, macros), origin="manual", source="manual",
    )
    state = "needs_macros" if not macros else None
    if state:
        await _set_state(conn, created["product_id"], state)
    return int(created["product_id"])


async def publish_custom_food(pool: Any, ctx: Any, product_id: int, entry_id: Optional[int]) -> str:
    """One attempt to create the FatSecret food and queue the diary line.

    Returns ``created``, ``needs_macros`` or ``refused``. Does not raise.
    """
    from app.services.fatsecret_api import create_custom_food, get_user_food

    if not ctx.fs_token or not ctx.fs_secret:
        async with pool.acquire() as conn:
            await _set_state(conn, product_id, "refused")
        return "refused"
    async with pool.acquire() as conn:
        product = await conn.fetchrow(
            """SELECT name, custom_fs_food_id, custom_fs_serving_id, custom_fs_state
               FROM food_products WHERE id = $1 AND owner_user_id = $2""",
            product_id, ctx.user_id,
        )
        nutrition = await catalog.current_nutrition(conn, product_id, ctx.user_id)
    if product is None or nutrition is None or nutrition.get("energy_kcal") is None:
        return "needs_macros"
    macros_ready = all(nutrition.get(key) is not None for key in ("protein_g", "fat_g", "carbs_g"))
    if not macros_ready:
        estimate = await estimate_macros(product["name"], Decimal(nutrition["energy_kcal"]))
        if estimate is None:
            async with pool.acquire() as conn:
                await _set_state(conn, product_id, "needs_macros")
            return "needs_macros"
        async with pool.acquire() as conn:
            await save_manual_nutrition(
                conn, ctx.user_id, product_id, Decimal(nutrition["energy_kcal"]), estimate,
            )
            nutrition = await catalog.current_nutrition(conn, product_id, ctx.user_id)

    food_id = product["custom_fs_food_id"]
    serving_id = product["custom_fs_serving_id"]
    if not food_id:
        food_id, error = await create_custom_food(
            ctx.fs_token, ctx.fs_secret,
            name=product["name"],
            calories=q1(Decimal(nutrition["energy_kcal"])),
            fat=q1(Decimal(nutrition["fat_g"])),
            carbohydrate=q1(Decimal(nutrition["carbs_g"])),
            protein=q1(Decimal(nutrition["protein_g"])),
        )
        if not food_id:
            logger.info("FatSecret custom food refused for product %s: %s", product_id, error)
            async with pool.acquire() as conn:
                await _set_state(conn, product_id, "refused")
            return "refused"
    if not serving_id:
        details = await get_user_food(ctx.fs_token, ctx.fs_secret, food_id)
        serving = choose_gram_serving((details or {}).get("servings") or [])
        serving_id = serving["serving_id"] if serving else None
        async with pool.acquire() as conn:
            await _set_state(conn, product_id, "created", food_id, serving_id)
        if serving is None or entry_id is None:
            return "created"
    else:
        serving = None
        async with pool.acquire() as conn:
            await _set_state(conn, product_id, "created", food_id, serving_id)

    if entry_id is None:
        return "created"
    async with pool.acquire() as conn:
        entry = await conn.fetchrow(
            "SELECT grams, revision, sync_status FROM food_entries WHERE id = $1 AND user_id = $2",
            entry_id, ctx.user_id,
        )
    if entry is None or entry["sync_status"] == "synced":
        return "created"
    if serving is None:
        details = await get_user_food(ctx.fs_token, ctx.fs_secret, food_id)
        serving = choose_gram_serving((details or {}).get("servings") or [])
    if serving is None:
        return "created"
    try:
        units = fatsecret_units_for_grams(serving, Decimal(entry["grams"]))
    except Exception:
        logger.warning("Custom food serving is not a mass", exc_info=True)
        return "created"
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """UPDATE food_entries
                   SET remote_provider = 'fatsecret', remote_food_id = $2,
                       remote_serving_id = $3, remote_units = $4,
                       sync_status = 'pending', version = version + 1
                   WHERE id = $1 AND user_id = $5 AND entry_status = 'committed'""",
                entry_id, food_id, serving["serving_id"], units, ctx.user_id,
            )
            await conn.execute(
                """INSERT INTO food_sync_outbox (user_id, food_entry_id, entry_revision, operation)
                   VALUES ($1, $2, $3, 'create')
                   ON CONFLICT (food_entry_id, entry_revision, operation) DO NOTHING""",
                ctx.user_id, entry_id, entry["revision"],
            )
    return "created"
