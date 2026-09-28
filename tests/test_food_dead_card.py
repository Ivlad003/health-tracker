"""A saved card with no calories per gram is not offered, and a tap falls back to search."""
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.services.food_resolver import TIER_HISTORY, TIER_SEARCH, Candidate


class _Conn:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *args):
        return False


class _Pool:
    def acquire(self):
        return _Conn()


def _hit(food_id: str, label: str, kcal: str | None = None) -> Candidate:
    return Candidate(
        tier=TIER_SEARCH, product_id=None, provider="fatsecret", external_id=food_id,
        label=label, kcal_per_100g=Decimal(kcal) if kcal else None,
    )


@pytest.mark.asyncio
async def test_history_card_without_calories_is_dropped(monkeypatch):
    from app.services import food_resolver as resolver

    async def no_nutrition(conn, product_id, user_id, serving_id=None):
        return None

    monkeypatch.setattr("app.services.food_catalog.current_nutrition", no_nutrition)
    history = Candidate(
        tier=TIER_HISTORY, product_id=3, provider="fatsecret", external_id="5382091",
        label="Макароны Отварные",
    )
    kept = await resolver._drop_history_without_nutrition(None, 1, [history, _hit("34499", "Макарони", "158")])
    assert [c.external_id for c in kept] == ["34499"]


@pytest.mark.asyncio
async def test_history_card_with_calories_stays_and_shows_kcal(monkeypatch):
    from app.services import food_resolver as resolver

    async def nutrition(conn, product_id, user_id, serving_id=None):
        return {"energy_kcal": Decimal("158"), "grams_per_basis": Decimal("100")}

    monkeypatch.setattr("app.services.food_catalog.current_nutrition", nutrition)
    history = Candidate(
        tier=TIER_HISTORY, product_id=3, provider="fatsecret", external_id="5382091",
        label="Макароны Отварные",
    )
    kept = await resolver._drop_history_without_nutrition(None, 1, [history])
    assert kept[0].kcal_per_100g == Decimal("158")


@pytest.mark.asyncio
async def test_unusable_card_reprompts_that_item_only(monkeypatch):
    from app.services import food_bot
    from app.services.food_logging import UserContext

    saved = {}

    async def save_draft(conn, draft, items=None):
        draft = dict(draft)
        draft["items"] = items
        draft["version"] = draft["version"] + 1
        saved["draft"] = draft
        return draft

    async def search(query, language=None, max_results=8):
        assert query.text == "Макарони"
        return [_hit("34499", "Макарони", "158"), _hit("5382091", "Макароны Отварные")]

    monkeypatch.setattr(food_bot.ledger, "save_draft", save_draft)
    monkeypatch.setattr("app.services.food_resolver.search_candidates", search)
    draft = {
        "id": 7,
        "version": 2,
        "items": [
            {
                "index": 0, "text": "Макарони", "grams": "155", "status": "ready", "candidates": [],
                "selected": {"product_id": 3, "external_id": "5382091", "label": "Макароны Отварные"},
            },
            {
                "index": 1, "text": "хліб", "grams": "80", "status": "ready", "candidates": [],
                "selected": {"product_id": 4, "external_id": "6234", "label": "Хліб"},
            },
        ],
    }
    ctx = UserContext(user_id=1, tz=ZoneInfo("Europe/Kyiv"), language="uk")
    reply = await food_bot._offer_search_instead(_Pool(), ctx, draft, item_index=0)
    assert "Макароны Отварные" in reply.text
    assert "пошуку" in reply.text
    pasta = saved["draft"]["items"][0]
    bread = saved["draft"]["items"][1]
    assert pasta["selected"] is None
    assert [c["external_id"] for c in pasta["candidates"]] == ["34499"]
    assert bread["selected"]["product_id"] == 4
    assert reply.buttons[0][0][1].startswith("fd:p:7:")


@pytest.mark.asyncio
async def test_commit_failure_names_the_item_without_calories(monkeypatch):
    from app.services import food_bot
    from app.services.food_logging import LedgerError, UserContext

    seen = {}

    async def boom(*args, **kwargs):
        exc = LedgerError("nutrition_missing")
        exc.item_index = 1
        raise exc

    async def offer(pool, ctx, draft, item_index=None):
        seen["index"] = item_index
        return food_bot.BotReply("ok")

    monkeypatch.setattr(food_bot.ledger, "commit_draft", boom)
    monkeypatch.setattr(food_bot, "_offer_search_instead", offer)
    ctx = UserContext(user_id=1, tz=ZoneInfo("Europe/Kyiv"), language="uk")
    reply = await food_bot._commit_and_render(object(), ctx, {"id": 1, "version": 1, "items": []})
    assert reply.text == "ok"
    assert seen["index"] == 1


def test_diary_import_prefers_a_per_100g_line():
    from app.services.catalog_import import _remember_description

    agg = {"description": None}
    _remember_description(agg, "Per 1 serving - Calories: 400kcal")
    _remember_description(agg, "Per 100g - Calories: 158kcal | Fat: 1g | Carbs: 20g | Protein: 4g")
    _remember_description(agg, "Per 1 cup - Calories: 300kcal")
    assert "158kcal" in agg["description"]


@pytest.mark.asyncio
async def test_diary_blurb_is_cached_and_a_cup_line_is_not(monkeypatch):
    from app.services import catalog_import

    stored = []

    async def store(conn, product_id, servings):
        stored.append((product_id, list(servings)))
        return len(servings)

    monkeypatch.setattr(catalog_import.catalog, "store_fatsecret_servings", store)
    await catalog_import._cache_diary_blurb(None, 9, "Per 1 serving - Calories: 200kcal")
    await catalog_import._cache_diary_blurb(
        None, 9, "Per 100g - Calories: 158kcal | Fat: 1.00g | Carbs: 20g | Protein: 4g",
    )
    assert stored[0][0] == 9
    assert stored[0][1][0]["serving_id"] == "per100g"
    assert stored[0][1][0]["calories"] == "158"
    assert len(stored) == 1
