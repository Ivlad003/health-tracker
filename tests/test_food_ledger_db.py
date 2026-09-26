"""Real-PostgreSQL tests for the food ledger, catalog, history import, outbox
and Web App API (plan AC-01, AC-04, AC-08, AC-09, AC-10, AC-12..AC-23).

They DROP and recreate the ``public`` schema: point
``FOOD_TEST_DATABASE_URL`` (or ``APPLE_HEALTH_TEST_DATABASE_URL``) at a
throwaway database only. Skipped when neither is set.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

asyncpg = pytest.importorskip("asyncpg")

MIGRATIONS = Path(__file__).resolve().parents[1] / "database" / "migrations"


def _dsn():
    return os.environ.get("FOOD_TEST_DATABASE_URL") or os.environ.get("APPLE_HEALTH_TEST_DATABASE_URL")


pytestmark = pytest.mark.skipif(_dsn() is None, reason="Set FOOD_TEST_DATABASE_URL to run real-PostgreSQL food tests")


@pytest_asyncio.fixture
async def pool(mock_settings):
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name.startswith("001") or "rollback" in path.name:
                continue
            await conn.execute(path.read_text(encoding="utf-8"))
    finally:
        await conn.close()
    p = await asyncpg.create_pool(_dsn(), min_size=1, max_size=6)
    yield p
    await p.close()


async def _user(pool, tg_id: int, *, fatsecret: bool = False) -> int:
    return await pool.fetchval(
        """INSERT INTO users (telegram_user_id, telegram_username, timezone, language,
                              fatsecret_access_token, fatsecret_access_secret)
           VALUES ($1, 'u', 'Europe/Kyiv', 'uk', $2, $3) RETURNING id""",
        tg_id, "tok" if fatsecret else None, "sec" if fatsecret else None,
    )


async def _ctx(pool, user_id):
    from app.services.food_logging import load_user_context

    return await load_user_context(pool, user_id)


def _basis(kcal="60", protein="4"):
    from app.services.food_nutrition import make_basis

    return make_basis(basis_quantity=100, basis_unit="g", energy_kcal=kcal, protein_g=protein)


def _no_search():
    return patch("app.services.food_resolver.search_candidates", AsyncMock(side_effect=AssertionError("no global search")))


FS_SERVINGS = [
    {"serving_id": "501", "description": "100 g", "metric_serving_amount": "100", "metric_serving_unit": "g",
     "number_of_units": "100", "calories": "110", "protein": "4", "fat": "1", "carbohydrate": "21"},
]


async def _fatsecret_product(pool, food_id="33691", display=None, user_id=None):
    from app.services import food_catalog as catalog

    product_id = await catalog.upsert_fatsecret_product(pool, food_id, name="Buckwheat, cooked")
    await catalog.store_fatsecret_servings(pool, product_id, FS_SERVINGS)
    if user_id:
        await catalog.upsert_membership(pool, user_id, product_id, "fatsecret_history", display_name=display)
    return product_id


# ---------------------------------------------------------------------------
# AC-13 / AC-15 / AC-18: manual product + default → bot reuse
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_manual_product_with_default_is_reused_by_bot(pool):
    from app.services import food_bot
    from app.services import food_catalog as catalog

    uid = await _user(pool, 1001)
    created = await catalog.create_personal_product(
        pool, uid, name="Йогурт Brand A 2%", brand="Brand A", basis=_basis("60"),
    )
    await catalog.set_default_rule(pool, uid, "мій йогурт", created["product_id"])
    assert await pool.fetchval("SELECT count(*) FROM food_entries") == 0  # catalog adds no calories

    ctx = await _ctx(pool, uid)
    with _no_search():
        reply = await food_bot.handle_food_items(
            pool, ctx, [{"name_original": "мій йогурт", "name_en": "yogurt", "quantity_g": 150,
                         "quantity_explicit": True}],
            chat_id=1001, message_id=1,
        )
    row = await pool.fetchrow("SELECT product_id, calories, grams, sync_status FROM food_entries")
    assert row["product_id"] == created["product_id"]
    assert row["calories"] == Decimal("90.0")
    assert row["sync_status"] == "local_only"
    assert "90" in reply.text and reply.buttons  # Undo button

    # A different explicit brand is never overridden by the pinned default.
    with patch("app.services.food_resolver.search_candidates", AsyncMock(return_value=[])):
        reply = await food_bot.handle_food_items(
            pool, ctx, [{"name_original": "йогурт", "brand": "Brand B", "quantity_g": 100,
                         "quantity_explicit": True}],
            chat_id=1001, message_id=2,
        )
    assert await pool.fetchval("SELECT count(*) FROM food_entries") == 1

    # Changing the product's nutrition later does not rewrite the past meal.
    await catalog.add_nutrition_revision(pool, created["product_id"], _basis("80"), source="manual",
                                         owner_user_id=uid, created_by_user_id=uid)
    assert await pool.fetchval("SELECT calories FROM food_entries") == Decimal("90.0")


# ---------------------------------------------------------------------------
# AC-01: learned choice reused without a new global search
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_confirmed_choice_is_reused_and_isolated_per_user(pool):
    from app.services import food_bot

    uid = await _user(pool, 1002)
    other = await _user(pool, 1003)
    product_id = await _fatsecret_product(pool)
    ctx = await _ctx(pool, uid)

    candidate = {"tier": 5, "product_id": None, "provider": "fatsecret", "external_id": "33691",
                 "label": "Buckwheat, cooked", "source": "search"}
    from app.services.food_resolver import Candidate

    search = AsyncMock(return_value=[Candidate(tier=5, product_id=None, provider="fatsecret",
                                               external_id="33691", label="Buckwheat, cooked", match=1.0)])
    with patch("app.services.food_resolver.search_candidates", search):
        reply = await food_bot.handle_food_items(
            pool, ctx, [{"name_original": "гречка варена", "name_en": "buckwheat", "quantity_g": 180,
                         "quantity_explicit": True}], chat_id=1002, message_id=10,
        )
    assert reply.buttons  # new food → explicit selection
    draft_id = reply.draft_id
    draft = await pool.fetchrow("SELECT version FROM food_log_drafts WHERE id = $1", draft_id)
    reply = await food_bot.handle_callback(pool, ctx, f"fd:p:{draft_id}:{draft['version']}:0:0")
    assert await pool.fetchval("SELECT count(*) FROM food_entries WHERE user_id = $1", uid) == 1
    assert await pool.fetchval("SELECT calories FROM food_entries") == Decimal("198.0")

    with _no_search():
        await food_bot.handle_food_items(
            pool, ctx, [{"name_original": "гречка варена", "name_en": "buckwheat", "quantity_g": 200,
                         "quantity_explicit": True}], chat_id=1002, message_id=11,
        )
    assert await pool.fetchval("SELECT count(*) FROM food_entries WHERE user_id = $1", uid) == 2

    # Another user's learned alias does not leak.
    other_ctx = await _ctx(pool, other)
    with patch("app.services.food_resolver.search_candidates", AsyncMock(return_value=[])):
        reply = await food_bot.handle_food_items(
            pool, other_ctx, [{"name_original": "гречка варена", "quantity_g": 100, "quantity_explicit": True}],
            chat_id=1003, message_id=1,
        )
    assert await pool.fetchval("SELECT count(*) FROM food_entries WHERE user_id = $1", other) == 0

    # Raw buckwheat request does not pick the cooked choice.
    with patch("app.services.food_resolver.search_candidates", AsyncMock(return_value=[])):
        await food_bot.handle_food_items(
            pool, ctx, [{"name_original": "гречка суха", "preparation": "raw", "quantity_g": 50,
                         "quantity_explicit": True}], chat_id=1002, message_id=12,
        )
    assert await pool.fetchval("SELECT count(*) FROM food_entries WHERE user_id = $1", uid) == 2


# ---------------------------------------------------------------------------
# AC-04 / AC-08: grams reply, replays, double commit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_grams_reply_and_replay_safety(pool):
    from app.services import food_bot
    from app.services import food_catalog as catalog
    from app.services import food_logging as ledger

    uid = await _user(pool, 1004)
    created = await catalog.create_personal_product(pool, uid, name="Сирок", basis=_basis("246"))
    await catalog.set_default_rule(pool, uid, "сирок", created["product_id"])
    ctx = await _ctx(pool, uid)
    items = [{"name_original": "сирок", "quantity_g": None, "quantity_explicit": False}]
    with _no_search():
        reply = await food_bot.handle_food_items(pool, ctx, items, chat_id=1004, message_id=20)
    assert await pool.fetchval("SELECT count(*) FROM food_entries") == 0
    draft = await ledger.get_draft(pool, reply.draft_id, uid)
    assert draft["state"] == "needs_weight"

    # "135" alone is not a food entry unless it replies to the draft.
    assert await food_bot.apply_reply_text(pool, ctx, draft, "сьогодні чудово") is None
    reply = await food_bot.apply_reply_text(pool, ctx, draft, "135")
    assert await pool.fetchval("SELECT calories FROM food_entries") == Decimal("332.1")

    # Replayed Telegram update → no second meal.
    with _no_search():
        await food_bot.handle_food_items(pool, ctx, items, chat_id=1004, message_id=20)
    assert await pool.fetchval("SELECT count(*) FROM food_entries") == 1

    # Two concurrent commits of one ready draft → one event.
    draft2, _ = await ledger.create_draft(
        pool, uid, origin="web_manual", items=[{
            "index": 0, "text": "", "grams": "100", "quantity_source": "explicit",
            "selected": {"product_id": created["product_id"], "label": "Сирок"}, "status": "ready",
        }], meal_type="snack", local_date=ctx.today(), commit_key="web:test:double",
    )
    results = await asyncio.gather(
        ledger.commit_draft(pool, ctx, draft2["id"]), ledger.commit_draft(pool, ctx, draft2["id"]),
        return_exceptions=True,
    )
    assert await pool.fetchval("SELECT count(*) FROM food_entries WHERE draft_id = $1", draft2["id"]) == 1
    assert all(not isinstance(r, Exception) or type(r).__name__ == "VersionConflict" for r in results)

    # Deliberate later repeat meal is allowed (new message).
    with _no_search():
        await food_bot.handle_food_items(
            pool, ctx, [{"name_original": "сирок", "quantity_g": 50, "quantity_explicit": True}],
            chat_id=1004, message_id=21,
        )
    assert await pool.fetchval("SELECT count(*) FROM food_entries") == 3


@pytest.mark.asyncio
async def test_draft_version_conflict_and_stale_callback(pool):
    from app.services import food_bot
    from app.services import food_logging as ledger
    from app.services.food_catalog import VersionConflict

    uid = await _user(pool, 1005)
    ctx = await _ctx(pool, uid)
    draft, _ = await ledger.create_draft(pool, uid, origin="bot_text", items=[{"index": 0, "text": "x"}],
                                         meal_type="lunch", local_date=ctx.today(), chat_id=1, message_id=1)
    await ledger.save_draft(pool, draft, items=[{"index": 0, "text": "y"}])
    with pytest.raises(VersionConflict):
        await ledger.save_draft(pool, draft, items=[{"index": 0, "text": "z"}])
    reply = await food_bot.handle_callback(pool, ctx, f"fd:g:{draft['id']}:{draft['version']}:0:100")
    assert reply.text.startswith("Це повідомлення застаріло")
    # Other users cannot touch the draft.
    intruder = await _user(pool, 1006)
    reply = await food_bot.handle_callback(pool, await _ctx(pool, intruder), f"fd:x:{draft['id']}:2:0:0")
    assert "закрито" in reply.text
    assert (await ledger.get_draft(pool, draft["id"], uid))["state"] != "cancelled"


# ---------------------------------------------------------------------------
# AC-09 / AC-10: outbox, reconciliation, void, daily union
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_outbox_unknown_is_reconciled_not_resent(pool):
    from app.services import food_logging as ledger
    from app.services import food_sync
    from app.services.fatsecret_api import FoodEntryWriteResult

    uid = await _user(pool, 1007, fatsecret=True)
    product_id = await _fatsecret_product(pool, user_id=uid)
    ctx = await _ctx(pool, uid)
    item = await ledger.prepare_item(pool, ctx, product_id=product_id, grams=180)
    assert item.remote == {"food_id": "33691", "serving_id": "501", "units": Decimal("180.00")}
    [entry] = await ledger.commit_items(pool, ctx, [item], meal_type="lunch", local_date=ctx.today(),
                                        origin="bot_text", idempotency_prefix="t:1")
    assert entry["sync_status"] == "pending"

    create = AsyncMock(return_value=FoodEntryWriteResult("unknown", error="transport:ReadTimeout"))
    with patch.object(food_sync, "create_food_entry", create):
        await food_sync.process_outbox(pool)
        await food_sync.process_outbox(pool)  # must not re-send
    assert create.await_count == 1
    assert await pool.fetchval("SELECT sync_status FROM food_entries") == "unknown"

    # Daily view while ambiguous: counted once, partial.
    remote_entry = {"food_entry_id": "9001", "food_id": "33691", "serving_id": "501",
                    "number_of_units": "180.000", "calories": "198", "protein": "7", "fat": "2",
                    "carbohydrate": "38", "name": "Гречка", "meal": "Lunch"}
    with patch("app.services.fatsecret_api.fetch_food_entries", AsyncMock(return_value=[remote_entry])):
        view = await ledger.daily_view(pool, ctx)
    assert view.total_kcal == Decimal("198.0") and view.partial

    await pool.execute("UPDATE food_sync_outbox SET dispatched_at = NOW() - INTERVAL '5 minutes'")
    with patch.object(food_sync, "fetch_food_entries", AsyncMock(return_value=[remote_entry])):
        stats = await food_sync.reconcile_unknown(pool)
    assert stats["linked"] == 1
    row = await pool.fetchrow("SELECT sync_status, remote_entry_id FROM food_entries")
    assert (row["sync_status"], row["remote_entry_id"]) == ("synced", "9001")
    with patch("app.services.fatsecret_api.fetch_food_entries", AsyncMock(return_value=[remote_entry])):
        view = await ledger.daily_view(pool, ctx)
    assert view.total_kcal == Decimal(198) and not view.partial


@pytest.mark.asyncio
async def test_sync_success_then_void_deletes_remote(pool):
    from app.services import food_logging as ledger
    from app.services import food_sync
    from app.services.fatsecret_api import FoodEntryWriteResult

    uid = await _user(pool, 1008, fatsecret=True)
    product_id = await _fatsecret_product(pool, user_id=uid)
    ctx = await _ctx(pool, uid)
    item = await ledger.prepare_item(pool, ctx, product_id=product_id, grams=100)
    [entry] = await ledger.commit_items(pool, ctx, [item], meal_type="lunch", local_date=ctx.today(),
                                        origin="bot_text", idempotency_prefix="t:2")
    with patch.object(food_sync, "create_food_entry", AsyncMock(return_value=FoodEntryWriteResult("succeeded", "777"))):
        await food_sync.process_outbox(pool)
    entry = await ledger.get_entry(pool, uid, entry["id"])
    assert entry["sync_status"] == "synced" and entry["remote_entry_id"] == "777"

    voided = await ledger.void_entry(pool, ctx, entry["id"], expected_version=entry["version"])
    assert voided["sync_status"] == "delete_pending"
    delete = AsyncMock(return_value=FoodEntryWriteResult("failed", error="api_1"))
    with patch.object(food_sync, "delete_food_entry", delete):
        await food_sync.process_outbox(pool)
    # Failed delete: the voided entry must not come back as intake.
    remote = [{"food_entry_id": "777", "food_id": "33691", "serving_id": "501", "calories": "110",
               "number_of_units": "100"}]
    with patch("app.services.fatsecret_api.fetch_food_entries", AsyncMock(return_value=remote)):
        view = await ledger.daily_view(pool, ctx)
    assert view.total_kcal == 0

    await pool.execute("UPDATE food_sync_outbox SET next_attempt_at = NOW() WHERE operation = 'delete'")
    with patch.object(food_sync, "delete_food_entry", AsyncMock(return_value=FoodEntryWriteResult("succeeded", "777"))):
        await food_sync.process_outbox(pool)
    assert await pool.fetchval("SELECT sync_status FROM food_entries") == "deleted"


@pytest.mark.asyncio
async def test_void_before_dispatch_cancels_create_and_edit_revisions(pool):
    from app.services import food_logging as ledger
    from app.services.food_catalog import VersionConflict

    uid = await _user(pool, 1009, fatsecret=True)
    product_id = await _fatsecret_product(pool, user_id=uid)
    ctx = await _ctx(pool, uid)
    item = await ledger.prepare_item(pool, ctx, product_id=product_id, grams=100)
    [entry] = await ledger.commit_items(pool, ctx, [item], meal_type="lunch", local_date=ctx.today(),
                                        origin="web_manual", idempotency_prefix="t:3")
    edited = await ledger.edit_entry(pool, ctx, entry["id"], expected_version=entry["version"], grams=150)
    assert edited["calories"] == Decimal("165.0") and edited["revision"] == 2
    with pytest.raises(VersionConflict):
        await ledger.edit_entry(pool, ctx, entry["id"], expected_version=entry["version"], grams=120)
    voided = await ledger.void_entry(pool, ctx, entry["id"])
    assert voided["entry_status"] == "voided"
    statuses = [r["status"] for r in await pool.fetch("SELECT status FROM food_sync_outbox ORDER BY id")]
    assert "pending" not in statuses


# ---------------------------------------------------------------------------
# AC-14 / AC-21 / AC-22 / AC-23: FatSecret history → My Products
# ---------------------------------------------------------------------------

def _history(today: date):
    return {
        today: [
            {"food_entry_id": "1", "food_id": "111", "serving_id": "5", "name": "Гречка варена", "calories": "200"},
            {"food_entry_id": "2", "food_id": "111", "serving_id": "5", "name": "Гречка варена", "calories": "150"},
            {"food_entry_id": "3", "food_id": "222", "serving_id": "7", "name": "Сир 5%", "calories": "120"},
        ],
        today - timedelta(days=1): [
            {"food_entry_id": "4", "food_id": "111", "serving_id": "6", "name": "гречка", "calories": "100"},
        ],
    }


def _fetch_for(history, fail_on=None):
    from app.services.fatsecret_api import FatSecretAPIError, date_from_fatsecret

    async def fetch(token, secret, date_int):
        day = date_from_fatsecret(date_int)
        if fail_on is not None and day == fail_on:
            raise FatSecretAPIError(12, "rate limited")
        return history.get(day, [])

    return fetch


def _no_enrichment():
    return patch.multiple(
        "app.services.catalog_import",
        get_favorite_foods=AsyncMock(return_value=[]),
        get_most_eaten=AsyncMock(return_value=[]),
        get_recently_eaten=AsyncMock(return_value=[]),
    )


@pytest.mark.asyncio
async def test_history_import_populates_catalog_resumably(pool):
    from app.services import catalog_import
    from app.services import food_catalog as catalog

    uid = await _user(pool, 1010, fatsecret=True)
    ctx = await _ctx(pool, uid)
    today = ctx.today()
    history = _history(today)

    job = await catalog_import.start_import(pool, uid, days=3)
    again = await catalog_import.start_import(pool, uid, days=3)
    assert again["already_active"] and again["id"] == job["id"]

    with _no_enrichment(), patch.object(catalog_import, "fetch_food_entries",
                                        _fetch_for(history, fail_on=today - timedelta(days=1))):
        result = await catalog_import.run_job(pool, job["id"])
    assert result["status"] == "partial"
    job_row = await catalog_import.get_job(pool, uid, job["id"])
    assert job_row["checkpoint_date"] == today and job_row["last_error"] == "api_12"
    assert len(await catalog.list_products(pool, uid)) == 2  # partial result kept

    await pool.execute("UPDATE catalog_import_jobs SET next_attempt_at = NOW()")
    with _no_enrichment(), patch.object(catalog_import, "fetch_food_entries", _fetch_for(history)):
        result = await catalog_import.run_job(pool, job["id"])
    assert result["status"] == "completed"
    products = {p["external_id"]: p for p in await catalog.list_products(pool, uid)}
    assert set(products) == {"111", "222"}
    assert sorted(products["111"]["known_serving_ids"]) == ["5", "6"]
    assert products["111"]["label"] == "Гречка варена"  # user's own diary label
    assert await pool.fetchval("SELECT count(*) FROM food_entries") == 0  # no meals created

    # Remove 222 → it stays excluded across a new refresh import; default rules disabled.
    pid_222 = products["222"]["product_id"]
    await catalog.set_default_rule(pool, uid, "сир", pid_222)
    await catalog.exclude_product(pool, uid, pid_222)
    assert await pool.fetchval("SELECT count(*) FROM food_default_rules WHERE enabled") == 0
    job2 = await catalog_import.start_import(pool, uid, days=1, kind="refresh")
    with _no_enrichment(), patch.object(catalog_import, "fetch_food_entries", _fetch_for(history)):
        await catalog_import.run_job(pool, job2["id"])
    assert {p["external_id"] for p in await catalog.list_products(pool, uid)} == {"111"}
    assert [p["external_id"] for p in await catalog.list_products(pool, uid, state="excluded")] == ["222"]
    await catalog.restore_product(pool, uid, pid_222)
    assert len(await catalog.list_products(pool, uid)) == 2


@pytest.mark.asyncio
async def test_selective_import_records_skips_as_exclusions(pool):
    from app.services import catalog_import
    from app.services import food_catalog as catalog

    uid = await _user(pool, 1011, fatsecret=True)
    ctx = await _ctx(pool, uid)
    history = _history(ctx.today())
    job = await catalog_import.start_import(pool, uid, days=2, mode="selective")
    with _no_enrichment(), patch.object(catalog_import, "fetch_food_entries", _fetch_for(history)):
        await catalog_import.run_job(pool, job["id"])
    assert await catalog.list_products(pool, uid) == []
    candidates = await catalog_import.list_candidates(pool, uid, job["id"])
    by_ext = {c["external_id"]: c for c in candidates}
    assert by_ext["111"]["occurrences"] == 3 and by_ext["111"]["label"] == "Гречка варена"
    await catalog_import.apply_selection(pool, uid, job["id"], selected=[by_ext["111"]["product_id"]],
                                         skipped=[by_ext["222"]["product_id"]])
    assert [p["external_id"] for p in await catalog.list_products(pool, uid)] == ["111"]
    assert [p["external_id"] for p in await catalog.list_products(pool, uid, state="excluded")] == ["222"]


@pytest.mark.asyncio
async def test_personal_products_are_private(pool):
    from app.services import food_catalog as catalog
    from app.services.food_catalog import CatalogError

    a = await _user(pool, 1012)
    b = await _user(pool, 1013)
    created = await catalog.create_personal_product(pool, a, name="Секретний соус", basis=_basis("300"))
    assert await catalog.get_product(pool, created["product_id"], b) is None
    with pytest.raises(CatalogError, match="not_found"):
        await catalog.set_default_rule(pool, b, "соус", created["product_id"])
    with pytest.raises(CatalogError, match="not_found"):
        await catalog.exclude_product(pool, b, created["product_id"])


@pytest.mark.asyncio
async def test_provider_cache_purge(pool):
    from app.services import food_catalog as catalog

    uid = await _user(pool, 1014)
    product_id = await _fatsecret_product(pool, user_id=uid, display="гречка")
    await pool.execute("UPDATE food_nutrition_versions SET expires_at = NOW() - INTERVAL '1 minute'")
    await pool.execute("UPDATE food_products SET provider_cached_until = NOW() - INTERVAL '1 minute'")
    await catalog.purge_expired_provider_data(pool)
    assert await pool.fetchval("SELECT count(*) FROM food_nutrition_versions") == 0
    row = await pool.fetchrow("SELECT provider_name, external_id FROM food_products WHERE id = $1", product_id)
    assert row["provider_name"] is None and row["external_id"] == "33691"  # IDs are kept
    assert (await catalog.list_products(pool, uid))[0]["label"] == "гречка"


# ---------------------------------------------------------------------------
# Web App API (AC-16 / AC-17 / AC-19)
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def api(pool):
    import httpx

    from app.config import settings

    with (
        patch("app.routers.webapp.get_pool", AsyncMock(return_value=pool)),
        patch("app.routers.admin.get_pool", AsyncMock(return_value=pool)),
        patch.object(settings, "telegram_bot_token", "123:TEST"),
        patch.object(settings, "webapp_admin_telegram_ids", "2002"),
        patch.object(settings, "webapp_url", "https://example.test/app/"),
    ):
        from app.main import app

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://example.test") as client:
            yield client


async def _login(api, tg_id):
    from app.services.webapp_auth import sign_init_data

    init = sign_init_data({"auth_date": str(int(time.time())),
                           "user": json.dumps({"id": tg_id, "language_code": "en"})}, "123:TEST")
    resp = await api.post("/api/v1/webapp/auth/telegram", json={"init_data": init})
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_webapp_manual_product_default_entry_and_isolation(api, pool):
    login = await _login(api, 2001)
    assert login["user"]["is_admin"] is False
    auth = {"Authorization": f"Bearer {login['session_token']}"}

    resp = await api.post("/api/v1/webapp/products", headers=auth, json={
        "name": "Yogurt A 2%", "nutrition": {"energy_kcal": "60", "protein_g": "4"},
        "default_alias": "my yogurt", "usual_portion_g": "150",
    })
    assert resp.status_code == 201, resp.text
    product_id = resp.json()["product_id"]

    preview = await api.post("/api/v1/webapp/default-rules/preview", headers=auth, json={"text": "my yogurt"})
    assert preview.json()["decision"] == "auto"
    assert preview.json()["candidates"][0]["source"] == "default_rule"
    assert await pool.fetchval("SELECT count(*) FROM food_entries") == 0

    body = {"product_id": product_id, "grams": "150", "meal_type": "breakfast", "idempotency_key": "abcdefgh1"}
    first = await api.post("/api/v1/webapp/food-entries", headers=auth, json=body)
    second = await api.post("/api/v1/webapp/food-entries", headers=auth, json=body)
    assert first.status_code == second.status_code == 201
    assert first.json()["entries"][0]["id"] == second.json()["entries"][0]["id"]
    entry = first.json()["entries"][0]
    assert Decimal(entry["calories"]) == Decimal("90")

    day = await api.get("/api/v1/webapp/food-entries", headers=auth)
    assert Decimal(day.json()["total_kcal"]) == Decimal("90")

    stale = await api.patch(f"/api/v1/webapp/food-entries/{entry['id']}", headers=auth,
                            json={"version": entry["version"] + 5, "grams": "200"})
    assert stale.status_code == 409
    ok = await api.patch(f"/api/v1/webapp/food-entries/{entry['id']}", headers=auth,
                         json={"version": entry["version"], "grams": "200"})
    assert ok.status_code == 200 and Decimal(ok.json()["calories"]) == Decimal("120")

    # Another user cannot see or use the product; non-admins get 403 on admin APIs.
    other = await _login(api, 2003)
    other_auth = {"Authorization": f"Bearer {other['session_token']}"}
    assert (await api.get(f"/api/v1/webapp/products/{product_id}", headers=other_auth)).status_code == 404
    denied = await api.post("/api/v1/webapp/food-entries", headers=other_auth, json={**body, "idempotency_key": "zzzzzzzz"})
    assert denied.status_code == 404
    assert (await api.get("/api/v1/admin/features", headers=auth)).status_code == 403


@pytest.mark.asyncio
async def test_webapp_auth_rejections_and_csrf(api, pool):
    from app.services.webapp_auth import sign_init_data

    forged = sign_init_data({"auth_date": str(int(time.time())), "user": json.dumps({"id": 5})}, "999:OTHER")
    assert (await api.post("/api/v1/webapp/auth/telegram", json={"init_data": forged})).status_code == 401
    assert (await api.get("/api/v1/webapp/me")).status_code == 401
    assert (await api.get("/api/v1/webapp/me", headers={"Authorization": "Bearer nope"})).status_code == 401

    login = await _login(api, 2004)
    cookies = {"ht_session": login["session_token"]}
    api.cookies.set("ht_session", login["session_token"])
    no_csrf = await api.put("/api/v1/webapp/preferences", json={"version": 0, "changes": {}})
    assert no_csrf.status_code == 403
    with_csrf = await api.put("/api/v1/webapp/preferences", json={"version": 0, "changes": {"catalog_auto_add": False}},
                              headers={"X-CSRF-Token": login["csrf_token"], "Origin": "https://example.test"})
    assert with_csrf.status_code == 200 and with_csrf.json()["version"] == 1
    evil = await api.put("/api/v1/webapp/preferences", json={"version": 1, "changes": {}},
                         headers={"X-CSRF-Token": login["csrf_token"], "Origin": "https://evil.test"})
    assert evil.status_code == 403
    api.cookies.clear()
    body = (await api.get("/api/v1/webapp/me", headers={"Authorization": f"Bearer {login['session_token']}"})).text
    assert "tok" not in body and "123:TEST" not in body and cookies  # no secrets in responses


@pytest.mark.asyncio
async def test_admin_starter_catalog_features_and_audit(api, pool):
    login = await _login(api, 2002)
    assert login["user"]["is_admin"] is True
    auth = {"Authorization": f"Bearer {login['session_token']}"}
    created = await api.post("/api/v1/admin/catalog", headers=auth, json={
        "name": "Oatmeal (dry)", "preparation": "raw", "nutrition": {"energy_kcal": "370"},
    })
    assert created.status_code == 201
    flags = (await api.get("/api/v1/admin/features", headers=auth)).json()["items"]
    history = next(f for f in flags if f["key"] == "food_history")
    resp = await api.put("/api/v1/admin/features/food_history", headers=auth,
                         json={"enabled": False, "version": history["version"]})
    assert resp.status_code == 200
    fs_barcode = await api.put("/api/v1/admin/features/fatsecret_barcode", headers=auth,
                               json={"enabled": True, "version": 0})
    assert fs_barcode.status_code == 409  # capability not available → cannot be enabled by a toggle
    audit = (await api.get("/api/v1/admin/audit", headers=auth)).json()["items"]
    assert {a["action"] for a in audit} >= {"starter.create", "feature.set"}
    jobs = (await api.get("/api/v1/admin/jobs", headers=auth)).text
    assert "food_name" not in jobs
    # Revoked admin loses access immediately.
    await pool.execute("UPDATE user_roles SET revoked_at = NOW()")
    assert (await api.get("/api/v1/admin/features", headers=auth)).status_code == 403


# ---------------------------------------------------------------------------
# US-2 / US-3: barcode photo + grams, unknown barcode completed from a label
# ---------------------------------------------------------------------------

def _png_barcode(code: str) -> bytes:
    from tests.test_barcode_reader import _barcode_png

    return _barcode_png([(code, "EAN13")])


@pytest.mark.asyncio
async def test_barcode_photo_with_caption_grams_logs_exact_product(pool):
    pytest.importorskip("zxingcpp")
    from app.services import food_bot
    from app.services.open_food_facts import parse_product

    uid = await _user(pool, 1020)
    ctx = await _ctx(pool, uid)
    product = parse_product({"code": "4006381333931", "product_name": "Test bar", "brands": "B",
                             "nutriments": {"energy-kcal_100g": 246, "proteins_100g": 8}, "rev": 3})
    with patch("app.services.open_food_facts.lookup_barcode", AsyncMock(return_value=product)):
        reply = await food_bot.handle_photo(pool, ctx, _png_barcode("4006381333931"), caption="135 г",
                                            chat_id=1020, message_id=5, file_unique_id="f1")
    row = await pool.fetchrow("SELECT calories, grams, nutrition_source FROM food_entries")
    assert row["calories"] == Decimal("332.1") and row["nutrition_source"] == "off"
    assert "Open Food Facts" in reply.text
    # The same photo delivered again does nothing.
    with patch("app.services.open_food_facts.lookup_barcode", AsyncMock(return_value=product)):
        again = await food_bot.handle_photo(pool, ctx, _png_barcode("4006381333931"), caption="135 г",
                                            chat_id=1020, message_id=5, file_unique_id="f1")
    assert again is None
    assert await pool.fetchval("SELECT count(*) FROM food_entries") == 1


@pytest.mark.asyncio
async def test_unknown_barcode_then_label_reply_creates_reusable_product(pool):
    pytest.importorskip("zxingcpp")
    from app.services import food_bot
    from app.services import food_logging as ledger
    from app.services.food_vision import validate_vision_payload

    uid = await _user(pool, 1021)
    ctx = await _ctx(pool, uid)
    with patch("app.services.open_food_facts.lookup_barcode", AsyncMock(return_value=None)):
        reply = await food_bot.handle_photo(pool, ctx, _png_barcode("4006381333931"), caption="",
                                            chat_id=1021, message_id=7, file_unique_id="b1")
    assert "4006381333931" in reply.text
    draft = await ledger.get_draft(pool, reply.draft_id, uid)
    assert draft["state"] == "needs_label"
    await ledger.set_reply_message(pool, draft["id"], uid, 8)

    label = validate_vision_payload({"kind": "label", "product_name": "Сирок X", "label": {
        "basis": "100g", "energy_kcal": 246, "protein_g": 8, "package_net_weight_g": 400}})
    with (
        patch("app.services.barcode_reader.decode_barcodes", AsyncMock(return_value=[])),
        patch("app.services.food_vision.analyze_food_image", AsyncMock(return_value=label)),
        patch("app.config.settings.openai_api_key", "sk-test"),
    ):
        reply = await food_bot.handle_photo(pool, ctx, b"\x89PNG-label", caption="135 г", chat_id=1021,
                                            message_id=9, reply_to=8, file_unique_id="l1")
    assert "246" in reply.text and reply.buttons
    draft = await ledger.get_draft(pool, draft["id"], uid)
    save = next(data for row in reply.buttons for _, data in row if data.startswith("fd:l:"))
    reply = await food_bot.handle_callback(pool, ctx, save)
    row = await pool.fetchrow(
        """SELECT fe.calories, p.barcode, p.provider, p.owner_user_id FROM food_entries fe
           JOIN food_products p ON p.id = fe.product_id"""
    )
    assert row["calories"] == Decimal("332.1")  # 135 g from the caption, not the 400 g package
    assert (row["barcode"], row["provider"], row["owner_user_id"]) == ("4006381333931", "label", uid)

    # Next scan of the same barcode resolves to the saved personal product.
    with patch("app.services.open_food_facts.lookup_barcode", AsyncMock(side_effect=AssertionError)):
        await food_bot.handle_photo(pool, ctx, _png_barcode("4006381333931"), caption="50 г",
                                    chat_id=1021, message_id=12, file_unique_id="b2")
    assert await pool.fetchval("SELECT count(*) FROM food_entries") == 2


@pytest.mark.asyncio
async def test_album_barcode_and_label_merge_into_one_draft(pool):
    pytest.importorskip("zxingcpp")
    from app.services import food_bot
    from app.services import food_logging as ledger
    from app.services.food_vision import validate_vision_payload

    uid = await _user(pool, 1022)
    ctx = await _ctx(pool, uid)
    label = validate_vision_payload({"kind": "label", "product_name": "Сирок X", "label": {
        "basis": "100g", "energy_kcal": 246}})
    barcode_png = _png_barcode("4006381333931")

    async def decode(data, **kw):
        from app.services.barcode_reader import _decode_sync

        return _decode_sync(data, 40_000_000) if data == barcode_png else []

    with (
        patch("app.services.open_food_facts.lookup_barcode", AsyncMock(return_value=None)),
        patch("app.services.barcode_reader.decode_barcodes", decode),
        patch("app.services.food_vision.analyze_food_image", AsyncMock(return_value=label)),
        patch("app.config.settings.openai_api_key", "sk-test"),
    ):
        await asyncio.gather(
            food_bot.handle_photo(pool, ctx, b"label-bytes", caption="", chat_id=1022, message_id=30,
                                  media_group_id="alb1", file_unique_id="a"),
            food_bot.handle_photo(pool, ctx, barcode_png, caption="135 г", chat_id=1022, message_id=31,
                                  media_group_id="alb1", file_unique_id="b"),
        )
    drafts = await pool.fetch("SELECT id FROM food_log_drafts WHERE user_id = $1", uid)
    assert len(drafts) == 1
    draft = await ledger.get_draft(pool, drafts[0]["id"], uid)
    assert len(draft["items"]) == 1
    item = draft["items"][0]
    assert item["barcode"] == "4006381333931" and item["label"]["usable"]
    assert len(draft["media"]) == 2
