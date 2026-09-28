"""Telegram food flows (text/voice, barcode, label, plate) without PTB types.

Every function returns a :class:`BotReply`; ``telegram_bot`` turns it into a
message with an inline keyboard. Callback data is short
(``fd:<action>:<draft>:<version>:<item>:<arg>`` / ``fe:undo:<entry>:<version>``)
and ownership + version are checked server-side on every callback.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from app.config import settings
from app.i18n import t
from app.services import feature_flags
from app.services import food_catalog as catalog
from app.services import food_logging as ledger
from app.services.food_catalog import VersionConflict
from app.services.food_logging import LedgerError, UserContext
from app.services.food_nutrition import NutritionError, q1, to_decimal, validate_grams
from app.services.food_resolver import FoodQuery, TIER_EXPLICIT, resolve

logger = logging.getLogger(__name__)


@dataclass
class BotReply:
    text: str
    buttons: list[list[tuple[str, str]]] = field(default_factory=list)
    webapp_url: Optional[str] = None
    draft_id: Optional[int] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _choice_label(cand: dict, grams: Any, lang: str) -> str:
    """Name plus energy for the grams the user already stated."""
    name = cand.get("label") or "?"
    if cand.get("brand"):
        name = f"{name} ({cand['brand']})"
    per = to_decimal(cand.get("kcal_per_100g"))
    weight = to_decimal(grams)
    if per is None or weight is None:
        kcal = "?"
    else:
        kcal = _fmt(q1(per * weight / Decimal(100)))
    return f"{name[:42]} · {kcal} {t('food_kcal_short', lang)}"[:60]


def _fmt(value: Any) -> str:
    d = to_decimal(value)
    if d is None:
        return "—"
    d = q1(d)
    return str(int(d)) if d == d.to_integral_value() else str(d)


def webapp_link(fragment: str = "") -> Optional[str]:
    url = settings.effective_webapp_url
    if not url.startswith("https://"):
        return None
    return url + (f"#{fragment}" if fragment else "")


def _source_label(source: Optional[str], lang: str) -> str:
    key = {
        "fatsecret": "food_source_fatsecret", "off": "food_source_off",
        "label": "food_source_label", "manual": "food_source_manual", "recipe": "food_source_manual",
    }.get(source or "", "food_source_manual")
    return t(key, lang)


def _sync_suffix(status: Optional[str], lang: str) -> str:
    return t(f"food_sync_{status}", lang) if status in (
        "synced", "pending", "failed", "unknown", "not_supported",
    ) else ""


def _why(selected: dict, lang: str) -> str:
    source = selected.get("source")
    alias = selected.get("alias") or ""
    if source == "default_rule":
        return t("food_why_default", lang, alias=alias)
    if source == "learned_alias":
        return t("food_why_learned", lang, alias=alias)
    if source == "confirmed_history":
        return t("food_why_confirmed", lang)
    return ""


def _selected_from_candidate(cand: dict, item_text: str) -> dict:
    return {
        "product_id": cand.get("product_id"),
        "provider": cand.get("provider"),
        "external_id": cand.get("external_id"),
        "label": cand.get("label"),
        "serving_id": cand.get("serving_id"),
        "source": cand.get("source"),
        "alias": item_text,
    }


async def _flag(pool: Any, key: str) -> bool:
    try:
        return await feature_flags.is_enabled(pool, key)
    except Exception:
        return False


def _item_prompt(draft: dict, lang: str) -> BotReply:
    """Ask for whatever the first unresolved item needs."""
    for item in draft["items"]:
        idx = item["index"]
        if item.get("status") == "needs_label" and not item.get("selected"):
            label = item.get("label")
            if label:
                return _label_reply(draft, item, lang)
            return BotReply(
                t("food_barcode_unknown", lang, code=item.get("barcode") or "?"),
                buttons=[[(t("food_btn_cancel", lang), f"fd:x:{draft['id']}:{draft['version']}:0:0")]],
                draft_id=draft["id"],
            )
        if item.get("status") == "needs_kcal":
            return BotReply(
                t("food_need_kcal", lang, name=item.get("text") or "?"),
                buttons=[[(t("food_btn_cancel", lang), f"fd:x:{draft['id']}:{draft['version']}:0:0")]],
                draft_id=draft["id"],
            )
        if item.get("grams") in (None, "") and not item.get("selected"):
            presets = [p for p in (item.get("presets") or [100, 150, 200])][:5]
            rows = [[(f"{p} g", f"fd:g:{draft['id']}:{draft['version']}:{idx}:{p}") for p in presets]]
            rows.append([(t("food_btn_cancel", lang), f"fd:x:{draft['id']}:{draft['version']}:0:0")])
            return BotReply(
                t("food_need_grams", lang, name=item.get("text") or "?"),
                rows, draft_id=draft["id"],
            )
        if not item.get("selected"):
            cands = item.get("candidates") or []
            if not cands:
                return BotReply(
                    t("food_nothing_found", lang, text=item.get("text") or "?"),
                    buttons=[[(t("food_btn_cancel", lang), f"fd:x:{draft['id']}:{draft['version']}:0:0")]],
                    draft_id=draft["id"],
                )
            rows = []
            for n, cand in enumerate(cands[:4]):
                rows.append([(_choice_label(cand, item.get("grams"), lang),
                              f"fd:p:{draft['id']}:{draft['version']}:{idx}:{n}")])
            rows.append([(t("food_btn_cancel", lang), f"fd:x:{draft['id']}:{draft['version']}:0:0")])
            return BotReply(t("food_choose", lang, text=item.get("text") or "?"), rows, draft_id=draft["id"])
        if item.get("grams") in (None, ""):
            presets = [p for p in (item.get("presets") or [100, 150, 200])][:5]
            rows = [[(f"{p} g", f"fd:g:{draft['id']}:{draft['version']}:{idx}:{p}") for p in presets]]
            rows.append([(t("food_btn_cancel", lang), f"fd:x:{draft['id']}:{draft['version']}:0:0")])
            return BotReply(
                t("food_need_grams", lang, name=item["selected"].get("label") or item.get("text") or "?"),
                rows, draft_id=draft["id"],
            )
    return BotReply(t("food_draft_closed", lang), draft_id=draft["id"])


def _label_reply(draft: dict, item: dict, lang: str) -> BotReply:
    label = item["label"]
    nutrients = label.get("nutrients") or {}
    missing = label.get("missing") or []
    basis_text = {"100g": "100 g", "100ml": "100 ml", "serving": t("food_per_serving", lang)}.get(
        label.get("basis_label") or "", "?")
    text = t(
        "food_label_extracted", lang,
        name=label.get("name") or item.get("barcode") or "?",
        basis=basis_text,
        kcal=_fmt(nutrients.get("energy_kcal")),
        protein=_fmt(nutrients.get("protein_g")),
        fat=_fmt(nutrients.get("fat_g")),
        carbs=_fmt(nutrients.get("carbs_g")),
        missing=t("food_label_missing", lang, fields=", ".join(missing)) if missing else "",
    )
    rows = []
    if label.get("usable"):
        rows.append([(t("food_btn_save_label", lang), f"fd:l:{draft['id']}:{draft['version']}:{item['index']}:0")])
    elif label.get("basis_label") == "100ml":
        text += "\n" + t("food_label_per_ml", lang)
    else:
        text += "\n" + t("food_label_unusable", lang)
    rows.append([(t("food_btn_cancel", lang), f"fd:x:{draft['id']}:{draft['version']}:0:0")])
    return BotReply(text, rows, webapp_url=webapp_link(f"/drafts/{draft['id']}"), draft_id=draft["id"])


async def render_committed(pool: Any, ctx: UserContext, entries: list[dict], goal: int,
                           reasons: Optional[list[dict]] = None) -> BotReply:
    lang = ctx.language
    lines = [t("food_added_header", lang)]
    for entry in entries:
        line = t(
            "food_entry_line", lang, name=entry["food_name"], grams=_fmt(entry.get("grams")),
            kcal=_fmt(entry.get("calories")),
        )
        line += f" · {_source_label(entry.get('nutrition_source'), lang)}"
        line += _sync_suffix(entry.get("sync_status"), lang)
        lines.append(line)
    for why in reasons or []:
        if why:
            lines.append(f"  ↳ {why}")
    try:
        view = await ledger.daily_view(pool, ctx)
        total = t("food_daily_total", lang, total=view.total_rounded, goal=goal)
        if view.partial:
            total += t("food_partial", lang)
        lines.append("")
        lines.append(total)
        if view.expired:
            lines.append(t("reconnect_header", lang) + "  🥗 FatSecret → /connect_fatsecret")
    except Exception:
        logger.warning("Daily view failed after commit", exc_info=True)
    buttons = []
    if entries and len(entries) <= 3:
        buttons.append([
            (f"{t('food_btn_undo', lang)} {e['food_name'][:18]}" if len(entries) > 1 else t("food_btn_undo", lang),
             f"fe:u:{e['id']}:{e['version']}")
            for e in entries
        ])
        buttons.extend(
            [(t("food_btn_pin", lang), f"fe:p:{e['id']}:{e['version']}")]
            for e in entries if e.get("product_id")
        )
    link = webapp_link(f"/diary/{entries[0]['local_date']}") if entries else None
    return BotReply("\n".join(lines), buttons, webapp_url=link)


async def _sync_now(pool: Any, entries: list[dict]) -> list[dict]:
    """Try the remote write right away (bounded); local success regardless."""
    ids = [e["id"] for e in entries if e.get("sync_status") == "pending"]
    if not ids:
        return entries
    from app.services.food_sync import process_outbox

    try:
        await asyncio.wait_for(
            process_outbox(pool, entry_ids=ids, limit=len(ids)),
            timeout=settings.http_timeout_seconds * 2,
        )
    except Exception:
        logger.warning("Immediate FatSecret sync failed; outbox will retry", exc_info=True)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, sync_status, version FROM food_entries WHERE id = ANY($1::int[])", ids,
        )
    status = {r["id"]: (r["sync_status"], r["version"]) for r in rows}
    for entry in entries:
        if entry["id"] in status:
            entry["sync_status"], entry["version"] = status[entry["id"]]
    return entries


async def _goal(pool: Any, ctx: UserContext) -> int:
    async with pool.acquire() as conn:
        value = await conn.fetchval("SELECT daily_calorie_goal FROM users WHERE id = $1", ctx.user_id)
    return int(value or 2000)


async def _commit_and_render(pool: Any, ctx: UserContext, draft: dict) -> BotReply:
    lang = ctx.language
    try:
        draft, entries = await ledger.commit_draft(pool, ctx, draft["id"], expected_version=draft["version"])
    except LedgerError as exc:
        code = str(exc)
        if code == "nutrition_missing" or code == "basis_not_mass_based":
            return await _offer_search_instead(
                pool, ctx, draft, item_index=getattr(exc, "item_index", None),
            )
        if code in ("grams_invalid", "grams_not_positive", "grams_too_large"):
            return BotReply(t("food_grams_invalid", lang), draft_id=draft["id"])
        raise
    except VersionConflict:
        async with pool.acquire() as conn:
            fresh = await ledger.get_draft(conn, draft["id"], ctx.user_id)
        if fresh and fresh["state"] == "committed":
            entries = await ledger._entries_by_ids(pool, ctx.user_id, fresh["committed_entry_ids"])
            return await render_committed(pool, ctx, entries, await _goal(pool, ctx))
        return BotReply(t("food_draft_outdated", lang), draft_id=draft["id"])
    entries = await _sync_now(pool, entries)
    reasons = [_why(item["selected"], lang) for item in draft["items"] if item.get("selected")]
    return await render_committed(pool, ctx, entries, await _goal(pool, ctx), reasons)


async def _offer_search_instead(
    pool: Any, ctx: UserContext, draft: dict, item_index: Optional[int] = None,
) -> BotReply:
    """A saved card with no per-gram calories is replaced by FatSecret search hits.

    Other foods in the same draft stay as they are.
    """
    from app.services.food_resolver import FoodQuery, search_candidates

    lang = ctx.language
    items = draft["items"]
    target = None
    if item_index is not None:
        target = next((item for item in items if item.get("index") == item_index), None)
    if target is None or not target.get("selected"):
        target = next((item for item in items if item.get("selected")), None)
    name = "?"
    if target:
        name = (target.get("selected") or {}).get("label") or target.get("text") or "?"
    if target is None:
        return BotReply(
            t("food_nutrition_missing", lang, name=name),
            webapp_url=webapp_link(f"/drafts/{draft['id']}"), draft_id=draft["id"],
        )
    failed = target.get("selected") or {}
    query = FoodQuery.from_item({"name_original": target.get("text") or name})
    found = await search_candidates(query, language=ctx.language)
    candidates = []
    for cand in found:
        if failed.get("product_id") and cand.product_id == failed.get("product_id"):
            continue
        if failed.get("external_id") and cand.external_id == str(failed.get("external_id")):
            continue
        candidates.append(cand.to_json())
        if len(candidates) >= 3:
            break
    target["selected"] = None
    target["candidates"] = candidates
    target["status"] = "needs_product"
    target["reason"] = "nutrition_missing"
    async with pool.acquire() as conn:
        try:
            draft = await ledger.save_draft(conn, draft, items=items)
        except (VersionConflict, LedgerError):
            return BotReply(t("food_draft_outdated", lang), draft_id=draft["id"])
    if not candidates:
        return BotReply(
            t("food_nutrition_missing", lang, name=name),
            webapp_url=webapp_link(f"/drafts/{draft['id']}"), draft_id=draft["id"],
        )
    reply = _item_prompt(draft, lang)
    reply.text = t("food_saved_card_unusable", lang, name=name) + "\n\n" + reply.text
    return reply


async def _advance(pool: Any, ctx: UserContext, draft: dict) -> BotReply:
    if ledger.evaluate_items(draft["items"]) == "ready":
        return await _commit_and_render(pool, ctx, draft)
    return _item_prompt(draft, ctx.language)


# ---------------------------------------------------------------------------
# Text / voice
# ---------------------------------------------------------------------------

def _grams_from_item(item: dict) -> Optional[Decimal]:
    explicit = item.get("quantity_explicit")
    value = item.get("quantity_g")
    if value in (None, "") or explicit is False:
        return None
    try:
        return validate_grams(value)
    except NutritionError:
        return None


async def _with_portion_kcal(pool: Any, user_id: int, candidates: list[dict]) -> list[dict]:
    async with pool.acquire() as conn:
        for cand in candidates:
            if cand.get("kcal_per_100g") or not cand.get("product_id"):
                continue
            nutrition = await catalog.current_nutrition(conn, cand["product_id"], user_id)
            basis_g = to_decimal((nutrition or {}).get("grams_per_basis"))
            energy = to_decimal((nutrition or {}).get("energy_kcal"))
            if basis_g and energy is not None and basis_g > 0:
                cand["kcal_per_100g"] = str(q1(energy * 100 / basis_g))
    return candidates


async def build_item(pool: Any, ctx: UserContext, index: int, raw: dict, *, history: bool,
                      slots: bool = False) -> dict:
    query = FoodQuery.from_item(raw)
    grams = _grams_from_item(raw)
    async with pool.acquire() as conn:
        resolution = await resolve(
            conn, ctx.user_id, query,
            review_all=ctx.prefs.recording_policy == "review_all",
            history_enabled=history,
            language=ctx.language,
            slots=slots,
            allow_search=not (slots and grams is None),
        )
    if slots and grams is None and resolution.decision == "auto" and resolution.selected:
        portion = resolution.selected.suggested_portion_g
        if portion is not None:
            grams = portion
    item = {
        "index": index,
        "text": query.text,
        "name_en": query.name_en,
        "brand": query.brand,
        "preparation": query.preparation,
        "fat_pct": str(query.fat_pct) if query.fat_pct is not None else None,
        "grams": str(grams) if grams is not None else None,
        "quantity_source": "reused" if (
            grams is not None and _grams_from_item(raw) is None
        ) else ("explicit" if grams is not None else None),
        "candidates": [c.to_json() for c in resolution.candidates[:4]],
        "selected": None,
        "reason": resolution.reason,
        "presets": ctx.prefs.gram_presets,
        "learn": True,
    }
    item["candidates"] = await _with_portion_kcal(pool, ctx.user_id, item["candidates"])
    if resolution.selected is not None:
        item["selected"] = _selected_from_candidate(resolution.selected.to_json(), query.text)
    elif slots and grams is None:
        item["status"] = "needs_weight"
        item["reason"] = "awaiting_grams"
        item["candidates"] = []
        return item
    elif slots and grams is not None and resolution.decision == "none":
        from app.services.custom_food import find_personal_named

        async with pool.acquire() as conn:
            existing = await find_personal_named(conn, ctx.user_id, query.text)
        if existing is not None:
            item["selected"] = {
                "product_id": existing, "provider": "manual", "external_id": None,
                "label": query.text, "serving_id": None, "source": "manual", "alias": query.text,
            }
        else:
            item["status"] = "needs_kcal"
            return item
    if item.get("selected") and item.get("grams") not in (None, ""):
        item["status"] = "ready"
    elif item.get("selected"):
        item["status"] = "needs_weight"
    else:
        item["status"] = "needs_product"
    return item


async def handle_food_items(
    pool: Any,
    ctx: UserContext,
    food_items: list[dict],
    *,
    chat_id: int,
    message_id: int,
    origin: str = "bot_text",
) -> BotReply:
    history = await _flag(pool, "food_history")
    meal_types = [i.get("meal_type") for i in food_items if i.get("meal_type") in ledger.MEAL_TYPES]
    meal_type = meal_types[0] if meal_types else ledger.default_meal_type(datetime.now(ctx.tz))

    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id FROM food_log_drafts WHERE chat_id = $1 AND message_id = $2", chat_id, message_id,
        )
    if existing is not None:
        # Replayed Telegram update: never create a second meal.
        async with pool.acquire() as conn:
            draft = await ledger.get_draft(conn, existing["id"], ctx.user_id)
        if draft and draft["state"] == "committed":
            entries = await ledger._entries_by_ids(pool, ctx.user_id, draft["committed_entry_ids"])
            return await render_committed(pool, ctx, entries, await _goal(pool, ctx))
        if draft:
            return _item_prompt(draft, ctx.language)

    items = [
        await build_item(pool, ctx, n, raw, history=history, slots=True)
        for n, raw in enumerate(food_items[:10])
    ]
    async with pool.acquire() as conn:
        draft, _created = await ledger.create_draft(
            conn, ctx.user_id, origin=origin, items=items, meal_type=meal_type,
            local_date=ctx.today(), chat_id=chat_id, message_id=message_id,
        )
    return await _advance(pool, ctx, draft)


async def _refill_slots(pool: Any, ctx: UserContext, item: dict) -> None:
    """Resolve the two buttons once the grams are known."""
    raw = {
        "name_original": item.get("text"),
        "name_en": item.get("name_en"),
        "brand": item.get("brand"),
        "preparation": item.get("preparation"),
        "fat_pct": item.get("fat_pct"),
        "quantity_g": item.get("grams"),
        "quantity_explicit": True,
    }
    built = await build_item(
        pool, ctx, item["index"], raw, history=await _flag(pool, "food_history"), slots=True,
    )
    for key in ("candidates", "selected", "reason", "status", "learn"):
        item[key] = built.get(key)


async def _log_custom_kcal(pool: Any, ctx: UserContext, draft: dict, item: dict, kcal: Decimal) -> BotReply:
    """Create or reuse a personal product, log the portion, try FatSecret once."""
    from app.services.custom_food import create_personal, publish_custom_food

    lang = ctx.language
    name = item.get("text") or "?"
    async with pool.acquire() as conn:
        async with conn.transaction():
            product_id = await create_personal(conn, ctx.user_id, name, kcal, None)
    item["selected"] = {
        "product_id": product_id, "provider": "manual", "external_id": None,
        "label": name, "serving_id": None, "source": "manual", "alias": name,
    }
    item["learn"] = True
    item["status"] = "ready"
    async with pool.acquire() as conn:
        try:
            draft = await ledger.save_draft(conn, draft, items=draft["items"])
        except VersionConflict:
            return BotReply(t("food_draft_outdated", lang), draft_id=draft["id"])
        except LedgerError:
            return BotReply(t("food_draft_closed", lang), draft_id=draft["id"])
    reply = await _advance(pool, ctx, draft)
    async with pool.acquire() as conn:
        fresh = await ledger.get_draft(conn, draft["id"], ctx.user_id)
    entries = []
    if fresh and fresh.get("state") == "committed":
        entries = await ledger._entries_by_ids(pool, ctx.user_id, fresh.get("committed_entry_ids") or [])
    entry = next((row for row in entries if row.get("product_id") == product_id), None)
    state = await publish_custom_food(pool, ctx, product_id, entry["id"] if entry else None)
    async with pool.acquire() as conn:
        nutrition = await catalog.current_nutrition(conn, product_id, ctx.user_id)
    if nutrition and all(nutrition.get(key) is not None for key in ("protein_g", "fat_g", "carbs_g")):
        reply.text += "\n" + t(
            "food_estimate", lang,
            protein=_fmt(nutrition["protein_g"]), fat=_fmt(nutrition["fat_g"]),
            carbs=_fmt(nutrition["carbs_g"]),
        )
    else:
        reply.text += "\n" + t("food_estimate_missing", lang)
    if state == "refused":
        reply.text += "\n" + t("food_fs_refused", lang)
    elif state == "created" and entry:
        await _sync_now(pool, [{"id": entry["id"], "sync_status": "pending"}])
    return reply


async def apply_reply_text(pool: Any, ctx: UserContext, draft: dict, text: str) -> Optional[BotReply]:
    """A reply to a draft message: grams, or kcal per 100 g for a new product.

    A bare number is accepted here because the reply targets one draft
    (AC-04). Returns None when the text does not answer the open question,
    so the caller treats it as a new request.
    """
    lang = ctx.language
    items = draft["items"]
    kcal_item = next((i for i in items if i.get("status") == "needs_kcal"), None)
    if kcal_item is not None:
        kcal = to_decimal(str(text).strip().replace(",", ".").split()[0])
        if kcal is None or kcal <= 0 or kcal > 2000:
            return None
        return await _log_custom_kcal(pool, ctx, draft, kcal_item, kcal)
    try:
        from app.services.food_nutrition import parse_quantity_text

        grams = parse_quantity_text(text, allow_bare_number=True)
    except NutritionError as exc:
        if str(exc) == "volume_needs_density":
            name = next((i.get("text") for i in draft["items"]), "?")
            return BotReply(t("food_volume", lang, name=name), draft_id=draft["id"])
        return BotReply(t("food_grams_invalid", lang), draft_id=draft["id"])
    if grams is None:
        return None
    target = next((i for i in items if i.get("selected") and i.get("grams") in (None, "")), None)
    if target is None:
        target = next((i for i in items if i.get("grams") in (None, "")), None)
    if target is None:
        return BotReply(t("food_draft_closed", lang), draft_id=draft["id"])
    target["grams"] = str(grams)
    target["quantity_source"] = "explicit"
    if target.get("reason") == "awaiting_grams" or (
        not target.get("selected") and not target.get("candidates")
    ):
        await _refill_slots(pool, ctx, target)
    else:
        target["status"] = "ready" if target.get("selected") else target.get("status")
    async with pool.acquire() as conn:
        try:
            draft = await ledger.save_draft(conn, draft, items=items)
        except VersionConflict:
            return BotReply(t("food_draft_outdated", lang), draft_id=draft["id"])
        except LedgerError:
            return BotReply(t("food_draft_closed", lang), draft_id=draft["id"])
    return await _advance(pool, ctx, draft)


async def _pin_entry(
    pool: Any, ctx: UserContext, entry_id: int, version: int, *, replace_rule: Optional[int],
) -> BotReply:
    """Pin the entry's dish name to its product. Asks before replacing another pin."""
    lang = ctx.language
    async with pool.acquire() as conn:
        entry = await ledger.get_entry(conn, ctx.user_id, entry_id)
    if entry is None or entry.get("entry_status") != "committed" or not entry.get("product_id"):
        return BotReply(t("food_draft_closed", lang))
    if entry["version"] != version and replace_rule is None:
        return BotReply(t("food_draft_outdated", lang))
    alias = entry["food_name"]
    grams = to_decimal(entry.get("grams"))
    norm = catalog.normalize_alias(alias)
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            """SELECT id, version, product_id FROM food_default_rules
               WHERE user_id = $1 AND alias_normalized = $2 AND enabled AND origin = 'manual'""",
            ctx.user_id, norm,
        )
        if existing and existing["product_id"] != entry["product_id"] and replace_rule != existing["id"]:
            return BotReply(
                t("food_pin_replace", lang, name=alias),
                buttons=[
                    [(t("food_btn_replace", lang), f"fe:r:{entry_id}:{entry['version']}:{existing['id']}")],
                    [(t("food_btn_keep", lang), f"fe:k:{entry_id}:{entry['version']}")],
                ],
            )
        if existing and existing["product_id"] == entry["product_id"]:
            return BotReply(t("food_pin_saved", lang, name=alias))
        try:
            await catalog.set_default_rule(
                conn, ctx.user_id, alias, entry["product_id"],
                suggested_portion_g=grams,
                replace_rule_id=existing["id"] if existing else None,
                expected_version=existing["version"] if existing else None,
            )
        except VersionConflict:
            return BotReply(t("food_draft_outdated", lang))
        except catalog.CatalogError:
            return BotReply(t("food_draft_closed", lang))
    return BotReply(t("food_pin_saved", lang, name=alias))


async def pending_weight_draft(pool: Any, ctx: UserContext) -> tuple[Optional[dict], int]:
    async with pool.acquire() as conn:
        drafts = await ledger.open_drafts(conn, ctx.user_id, states=("needs_weight",))
    return (drafts[0] if len(drafts) == 1 else None), len(drafts)


async def pending_kcal_drafts(pool: Any, ctx: UserContext) -> list[dict]:
    async with pool.acquire() as conn:
        drafts = await ledger.open_drafts(conn, ctx.user_id)
    return [d for d in drafts if any(i.get("status") == "needs_kcal" for i in d["items"])]


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

async def handle_callback(pool: Any, ctx: UserContext, data: str) -> BotReply:
    lang = ctx.language
    parts = (data or "").split(":")
    if len(parts) >= 4 and parts[0] == "fe" and parts[1] in ("u", "p", "r", "k"):
        try:
            entry_id, version = int(parts[2]), int(parts[3])
        except ValueError:
            return BotReply(t("food_draft_closed", lang))
        if parts[1] == "k":
            return BotReply(t("food_pin_kept", lang))
        if parts[1] == "u":
            try:
                entry = await ledger.void_entry(pool, ctx, entry_id, expected_version=version)
            except VersionConflict:
                return BotReply(t("food_draft_outdated", lang))
            except LedgerError:
                return BotReply(t("food_draft_closed", lang))
            if entry.get("sync_status") == "delete_pending":
                await _sync_delete(pool, entry_id)
            return BotReply(t("food_undone", lang, name=entry["food_name"]))
        return await _pin_entry(
            pool, ctx, entry_id, version,
            replace_rule=int(parts[4]) if parts[1] == "r" and len(parts) > 4 else None,
        )

    if len(parts) != 6 or parts[0] != "fd":
        return BotReply(t("food_draft_closed", lang))
    action = parts[1]
    try:
        draft_id, version, index, arg = int(parts[2]), int(parts[3]), int(parts[4]), parts[5]
    except ValueError:
        return BotReply(t("food_draft_closed", lang))
    async with pool.acquire() as conn:
        draft = await ledger.get_draft(conn, draft_id, ctx.user_id)  # ownership check
    if draft is None:
        return BotReply(t("food_draft_closed", lang))
    if draft["state"] == "committed":
        entries = await ledger._entries_by_ids(pool, ctx.user_id, draft["committed_entry_ids"])
        return await render_committed(pool, ctx, entries, await _goal(pool, ctx))
    if draft["state"] not in ledger.OPEN_DRAFT_STATES:
        return BotReply(t("food_draft_closed", lang))
    if draft["version"] != version:
        reply = _item_prompt(draft, lang)
        reply.text = t("food_draft_outdated", lang) + "\n\n" + reply.text
        return reply
    if action == "x":
        async with pool.acquire() as conn:
            await ledger.cancel_draft(conn, draft_id, ctx.user_id)
        return BotReply(t("food_cancelled", lang))
    items = draft["items"]
    if not 0 <= index < len(items):
        return BotReply(t("food_draft_closed", lang))
    item = items[index]

    if action == "p":
        cands = item.get("candidates") or []
        try:
            cand = cands[int(arg)]
        except (ValueError, IndexError):
            return BotReply(t("food_draft_closed", lang))
        selected = _selected_from_candidate(cand, item.get("text") or "")
        if not selected.get("product_id"):
            if selected.get("provider") != "fatsecret" or not selected.get("external_id"):
                return BotReply(t("food_draft_closed", lang))
            async with pool.acquire() as conn:
                selected["product_id"] = await catalog.upsert_fatsecret_product(
                    conn, selected["external_id"], name=cand.get("label"), brand=cand.get("brand"),
                )
                if item.get("text"):
                    selected["label"] = item["text"]
        if cand.get("barcode_gtin"):
            item["barcode"] = cand["barcode_gtin"]
        item["selected"] = selected
        item["status"] = "ready" if item.get("grams") not in (None, "") else "needs_weight"
    elif action == "g":
        try:
            grams = validate_grams(arg)
        except NutritionError:
            return BotReply(t("food_grams_invalid", lang))
        item["grams"] = str(grams)
        item["quantity_source"] = "explicit"
        if item.get("reason") == "awaiting_grams" or (
            not item.get("selected") and not item.get("candidates")
        ):
            await _refill_slots(pool, ctx, item)
        else:
            item["status"] = "ready" if item.get("selected") else item.get("status")
    elif action == "l":
        label = item.get("label") or {}
        if not label.get("usable"):
            return _label_reply(draft, item, lang)
        try:
            basis = catalog.basis_from_payload({
                "basis_quantity": label["basis"]["basis_quantity"],
                "basis_unit": label["basis"]["basis_unit"],
                "grams_per_basis": label["basis"].get("grams_per_basis"),
                **(label.get("nutrients") or {}),
            })
        except (NutritionError, KeyError, TypeError):
            return BotReply(t("food_label_unusable", lang))
        name = label.get("name") or (f"EAN {item['barcode']}" if item.get("barcode") else t("food_unnamed", lang))
        async with pool.acquire() as conn:
            async with conn.transaction():
                created = await catalog.create_personal_product(
                    conn, ctx.user_id, name=name, brand=label.get("brand"), provider="label",
                    basis=basis, barcode=item.get("barcode"),
                    barcode_symbology=item.get("barcode_symbology"), origin="label", source="label",
                    preparation="as_sold",
                )
        item["selected"] = {
            "product_id": created["product_id"], "provider": "label", "external_id": None,
            "label": name, "serving_id": None, "source": "label", "alias": item.get("text") or "",
        }
        item["learn"] = bool(item.get("text"))
        item["status"] = "ready" if item.get("grams") not in (None, "") else "needs_weight"
    else:
        return BotReply(t("food_draft_closed", lang))

    async with pool.acquire() as conn:
        try:
            draft = await ledger.save_draft(conn, draft, items=items)
        except VersionConflict:
            fresh = await ledger.get_draft(conn, draft_id, ctx.user_id)
            reply = _item_prompt(fresh, lang) if fresh else BotReply(t("food_draft_closed", lang))
            reply.text = t("food_draft_outdated", lang) + "\n\n" + reply.text
            return reply
        except LedgerError:
            return BotReply(t("food_draft_closed", lang))
    reply = await _advance(pool, ctx, draft)
    if action == "l":
        reply.text = t("food_label_saved", lang, name=item["selected"]["label"]) + "\n" + reply.text
    return reply


async def _sync_delete(pool: Any, entry_id: int) -> None:
    from app.services.food_sync import process_outbox

    try:
        await asyncio.wait_for(
            process_outbox(pool, entry_ids=[entry_id], limit=2),
            timeout=settings.http_timeout_seconds * 2,
        )
    except Exception:
        logger.warning("Immediate FatSecret delete failed; outbox will retry", exc_info=True)


async def undo_last(pool: Any, ctx: UserContext) -> Optional[str]:
    entry = await ledger.undo_last(pool, ctx)
    if entry is None:
        return None
    if entry.get("sync_status") == "delete_pending":
        await _sync_delete(pool, entry["id"])
    kcal = _fmt(entry.get("calories"))
    return f"{entry['food_name']} ({kcal} kcal)"


# ---------------------------------------------------------------------------
# Photos: barcode → label → plate
# ---------------------------------------------------------------------------

async def resolve_barcode(pool: Any, ctx: UserContext, gtin) -> list[dict]:
    """Exact personal/shared product → Open Food Facts → FatSecret barcode.

    Returns candidate dicts (possibly empty = unknown product).
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT p.*, m.display_name, m.state AS membership_state
               FROM food_products p
               LEFT JOIN user_product_memberships m ON m.product_id = p.id AND m.user_id = $2
               WHERE p.barcode = ANY($1::text[]) AND p.status = 'active'
                 AND (p.owner_user_id = $2 OR p.owner_user_id IS NULL)
               ORDER BY (p.owner_user_id IS NOT NULL) DESC, m.confirmed_count DESC NULLS LAST""",
            list({gtin.code, gtin.gtin13}), ctx.user_id,
        )
        for row in rows:
            data = dict(row)
            if data.get("membership_state") in ("excluded", "archived"):
                continue
            nutrition = await catalog.current_nutrition(conn, data["id"], ctx.user_id)
            if nutrition is None and data["provider"] != "fatsecret":
                continue
            return [_barcode_candidate(data, catalog.display_label(data), nutrition)]

    from app.services import open_food_facts as off

    try:
        product = await off.lookup_barcode(pool, gtin.off_code)
    except Exception:
        logger.warning("Open Food Facts lookup failed", exc_info=True)
        product = None
    if product is not None and product.basis is not None:
        async with pool.acquire() as conn:
            product_id = await off.upsert_off_product(conn, product, barcode=gtin.code, symbology=gtin.symbology)
            nutrition = await catalog.current_nutrition(conn, product_id, ctx.user_id)
        data = {"id": product_id, "provider": "off", "external_id": product.code,
                "brand": product.brand, "barcode": gtin.code}
        return [_barcode_candidate(data, product.name or f"EAN {gtin.code}", nutrition)]

    if await _flag(pool, "fatsecret_barcode"):
        from app.services.fatsecret_api import find_food_by_barcode

        try:
            food = await find_food_by_barcode(gtin.gtin13)
        except Exception:
            logger.warning("FatSecret barcode lookup failed", exc_info=True)
            food = None
        if food and food.get("food_id"):
            async with pool.acquire() as conn:
                product_id = await catalog.upsert_fatsecret_product(
                    conn, food["food_id"], name=food.get("name"), brand=food.get("brand"),
                )
                await catalog.store_fatsecret_servings(conn, product_id, food.get("servings") or [])
                nutrition = await catalog.current_nutrition(conn, product_id, ctx.user_id)
            data = {"id": product_id, "provider": "fatsecret", "external_id": food["food_id"],
                    "brand": food.get("brand"), "barcode": None}
            return [_barcode_candidate(data, food.get("name") or f"EAN {gtin.code}", nutrition)]
    return []


def _barcode_candidate(data: dict, label: str, nutrition: Optional[dict]) -> dict:
    kcal = None
    if nutrition and nutrition.get("energy_kcal") is not None and nutrition.get("grams_per_basis"):
        kcal = q1(Decimal(nutrition["energy_kcal"]) * 100 / Decimal(nutrition["grams_per_basis"]))
    return {
        "tier": TIER_EXPLICIT, "product_id": data["id"], "provider": data["provider"],
        "external_id": data.get("external_id"), "label": label, "brand": data.get("brand"),
        "barcode": data.get("barcode"), "source": "explicit",
        "kcal_per_100g": str(kcal) if kcal is not None else None,
        "nutrition_source": (nutrition or {}).get("source"),
    }


async def _photo_draft(pool: Any, ctx: UserContext, *, chat_id: Optional[int], message_id: Optional[int],
                       media_group_id: Optional[str], reply_to: Optional[int],
                       commit_key: Optional[str] = None, origin: str = "bot_photo",
                       draft_id: Optional[int] = None) -> Optional[dict]:
    async with pool.acquire() as conn:
        if draft_id is not None:
            return await ledger.get_draft(conn, draft_id, ctx.user_id)
        if reply_to and chat_id is not None:
            draft = await ledger.find_reply_draft(conn, ctx.user_id, chat_id, reply_to)
            if draft:
                return draft
        if commit_key is None and media_group_id:
            commit_key = f"tgm:{chat_id}:{media_group_id}"
        draft, _ = await ledger.create_draft(
            conn, ctx.user_id, origin=origin, items=[], meal_type=None,
            local_date=ctx.today(), chat_id=chat_id, message_id=message_id,
            media_group_id=media_group_id, commit_key=commit_key,
        )
    return draft


async def handle_photo(
    pool: Any,
    ctx: UserContext,
    image_bytes: bytes,
    *,
    caption: str,
    chat_id: Optional[int],
    message_id: Optional[int],
    media_group_id: Optional[str] = None,
    reply_to: Optional[int] = None,
    file_unique_id: Optional[str] = None,
    commit_key: Optional[str] = None,
    origin: str = "bot_photo",
    draft_id: Optional[int] = None,
    auto_commit: bool = True,
) -> Optional[BotReply]:
    """Barcode photo → exact product; label photo → editable product card;
    plate photo → history-aware candidates (behind a flag)."""
    from app.services.barcode_reader import BarcodeError, decode_barcodes
    from app.services.food_nutrition import parse_quantity_text
    from app.services.food_vision import MediaError, check_upload

    lang = ctx.language
    try:
        check_upload(image_bytes, None)
    except MediaError:
        return BotReply(t("food_image_too_large", lang))
    grams = None
    try:
        grams = parse_quantity_text(caption or "")
    except NutritionError:
        grams = None

    draft = await _photo_draft(
        pool, ctx, chat_id=chat_id, message_id=message_id, media_group_id=media_group_id,
        reply_to=reply_to, commit_key=commit_key, origin=origin, draft_id=draft_id,
    )
    if draft is None:
        return BotReply(t("food_draft_closed", lang))
    decoded = []
    if await _flag(pool, "food_barcode"):
        try:
            decoded = await decode_barcodes(image_bytes, max_pixels=settings.media_max_pixels)
        except BarcodeError as exc:
            if str(exc) == "image_too_large":
                return BotReply(t("food_image_too_large", lang))
            decoded = []
    valid = [d for d in decoded if d.gtin is not None]
    invalid = [d for d in decoded if d.gtin is None]
    if not valid and invalid and not await _flag(pool, "food_vision"):
        reason = invalid[0].error or "barcode_checksum"
        key = "food_barcode_restricted" if reason == "barcode_restricted" else "food_barcode_invalid"
        return BotReply(t(key, lang, reason=reason))

    # Album photos arrive concurrently and share one draft: merge with
    # optimistic retries; expensive work (decode, lookups, vision) is cached.
    cache: dict = {}
    for _attempt in range(4):
        if draft["state"] == "committed":
            return None  # replayed update / late album photo after commit
        if draft["state"] not in ledger.OPEN_DRAFT_STATES:
            return BotReply(t("food_draft_closed", lang))
        media = list(draft.get("media") or [])
        if file_unique_id and any(m.get("id") == file_unique_id for m in media):
            return None  # same photo delivered twice
        media.append({"id": file_unique_id, "message_id": message_id})
        items = [dict(i) for i in draft["items"]]

        if valid:
            known = {i.get("barcode") for i in items}
            new_codes = [d for d in valid if d.gtin.code not in known]
            if len(new_codes) > 1:
                cands = []
                for d in new_codes[:3]:
                    found = cache.get(d.gtin.code)
                    if found is None:
                        found = cache[d.gtin.code] = await resolve_barcode(pool, ctx, d.gtin)
                    for c in found[:1]:
                        cands.append({**c, "barcode_gtin": d.gtin.code})
                items.append(_photo_item(len(items), grams, candidates=cands, reason="barcode_multiple"))
            elif new_codes:
                gtin = new_codes[0].gtin
                found = cache.get(gtin.code)
                if found is None:
                    found = cache[gtin.code] = await resolve_barcode(pool, ctx, gtin)
                # An album may deliver the label photo first: join it.
                label_only = next(
                    (i for i in items if i.get("label") and not i.get("barcode") and not i.get("selected")),
                    None,
                )
                item = label_only or _photo_item(len(items), grams, candidates=[], reason="barcode")
                item["candidates"] = found
                item["barcode"] = gtin.code
                item["barcode_symbology"] = gtin.symbology
                if grams is not None and item.get("grams") in (None, ""):
                    item["grams"] = str(grams)
                    item["quantity_source"] = "explicit"
                if found:
                    # Exact product identity wins; the label stays for reference.
                    if item.get("label"):
                        item["label_alt"] = item.pop("label")
                    item["selected"] = _selected_from_candidate(found[0], "")
                    item["selected"]["nutrition_source"] = found[0].get("nutrition_source")
                    item["learn"] = False
                    item["status"] = "ready" if item.get("grams") not in (None, "") else "needs_weight"
                else:
                    item["status"] = "needs_label"
                if label_only is None:
                    items.append(item)
            elif grams is not None:
                _apply_grams(items, grams)
        else:
            reply = await _vision_step(pool, ctx, draft, items, image_bytes, caption, grams, invalid, cache)
            if isinstance(reply, BotReply):
                return reply

        try:
            draft = await ledger.save_draft(pool, draft, items=items, media=media)
            break
        except VersionConflict:
            draft = await ledger.get_draft(pool, draft["id"], ctx.user_id)
            if draft is None:
                return BotReply(t("food_draft_closed", lang))
        except LedgerError:
            return BotReply(t("food_draft_closed", lang))
    else:
        return _item_prompt(draft, lang)
    if not auto_commit:
        return BotReply("", draft_id=draft["id"])
    return await _advance(pool, ctx, draft)


def _photo_item(index: int, grams: Optional[Decimal], *, candidates: list[dict], reason: str) -> dict:
    return {
        "index": index, "text": "", "name_en": "", "brand": None, "preparation": None,
        "fat_pct": None, "grams": str(grams) if grams is not None else None,
        "quantity_source": "explicit" if grams is not None else None,
        "candidates": candidates, "selected": None, "reason": reason, "learn": False,
        "status": "needs_product",
    }


def _apply_grams(items: list[dict], grams: Decimal) -> None:
    for item in items:
        if item.get("grams") in (None, ""):
            item["grams"] = str(grams)
            item["quantity_source"] = "explicit"
            return


async def _vision_step(pool, ctx, draft, items, image_bytes, caption, grams, invalid,
                       cache: Optional[dict] = None) -> Optional[BotReply]:
    from app.services.food_vision import MediaError, analyze_food_image

    lang = ctx.language
    if not await _flag(pool, "food_vision"):
        if invalid:
            return BotReply(t("food_barcode_invalid", lang, reason=invalid[0].error))
        return BotReply(t("food_barcode_unreadable", lang))
    cache = cache if cache is not None else {}
    try:
        result = cache.get("vision")
        if result is None:
            result = cache["vision"] = await analyze_food_image(
                image_bytes, caption=caption or "", language=lang,
            )
    except MediaError as exc:
        return BotReply(t("food_image_too_large" if str(exc) == "image_too_large" else "food_photo_failed", lang))
    except Exception:
        logger.exception("Vision analysis failed")
        return BotReply(t("food_photo_failed", lang))

    if result.kind == "label" and result.label is not None:
        label_json = result.label.to_json()
        label_json["usable"] = result.label.usable
        target = next((i for i in items if i.get("status") == "needs_label" and not i.get("selected")), None)
        if target is None:
            target = _photo_item(len(items), grams, candidates=[], reason="label")
            items.append(target)
        target["label"] = label_json
        target["status"] = "needs_label"
        if grams is not None and target.get("grams") in (None, ""):
            target["grams"] = str(grams)
            target["quantity_source"] = "explicit"
        return None

    if result.kind == "package":
        name = " ".join(p for p in (result.brand, result.product_name) if p)
        if not name:
            return BotReply(t("food_barcode_unreadable", lang))
        target = next((i for i in items if i.get("status") == "needs_label" and not i.get("selected")), None)
        if target is not None:
            return BotReply(t("food_barcode_unknown", lang, code=target.get("barcode") or "?"))
        raw = {"name_original": result.product_name or name, "name_en": result.product_name or name,
               "brand": result.brand, "quantity_g": str(grams) if grams is not None else None,
               "quantity_explicit": grams is not None}
        item = await build_item(pool, ctx, len(items), raw, history=await _flag(pool, "food_history"))
        # Photo interpretations are never auto-committed (FR-05).
        if item.get("selected") and item["selected"].get("source") not in ("default_rule", "explicit"):
            item["selected"] = None
            item["status"] = "needs_product"
        items.append(item)
        return None

    if result.kind == "plate":
        if not await _flag(pool, "food_plate_photos"):
            return BotReply(t("food_plate_disabled", lang))
        plate = result.plate_items
        if not plate:
            return BotReply(t("food_photo_other", lang))
        if len(plate) > 1 and grams is not None:
            # A whole-plate weight does not determine component proportions.
            names = ", ".join(p.name_original for p in plate)
            return BotReply(t("food_plate_split", lang, grams=_fmt(grams), items=names))
        history = await _flag(pool, "food_history")
        for p in plate:
            raw = {"name_original": p.name_original, "name_en": p.name_en, "preparation": p.preparation,
                   "quantity_g": str(grams) if grams is not None and len(plate) == 1 else None,
                   "quantity_explicit": grams is not None and len(plate) == 1}
            item = await build_item(pool, ctx, len(items), raw, history=history)
            if item.get("selected") and item["selected"].get("source") != "default_rule":
                item["selected"] = None  # confirm plate interpretations explicitly
                item["status"] = "needs_product"
            items.append(item)
        return None

    return BotReply(t("food_photo_other", lang))
