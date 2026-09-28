"""Resolver compatibility / ranking / decision (plan FR-03..FR-05, FR-15; AC-01, AC-02, AC-15)."""
from datetime import datetime, timezone
from decimal import Decimal

from app.services.food_resolver import (
    TIER_CONFIRMED,
    TIER_DEFAULT,
    TIER_EXPLICIT,
    TIER_HISTORY,
    TIER_LEARNED,
    Candidate,
    FoodQuery,
    compatibility,
    decide,
    infer_preparation,
    match_score,
    rank,
)


def cand(tier, pid, label, **kw):
    return Candidate(tier=tier, product_id=pid, provider="fatsecret", external_id=str(pid), label=label, **kw)


def test_infer_preparation():
    assert infer_preparation("гречка варена") == "cooked"
    assert infer_preparation("cooked buckwheat") == "cooked"
    assert infer_preparation("гречка суха") == "raw"
    assert infer_preparation("сир кисломолочний") is None  # cheese is not "raw"
    assert infer_preparation("сирники") is None


def test_raw_history_cannot_override_cooked_request():
    query = FoodQuery.from_item({"name_original": "гречка варена 180 г", "name_en": "cooked buckwheat"})
    raw = cand(TIER_CONFIRMED, 1, "Гречка суха", preparation="raw", confirmed_count=9, match=1.0, exact=False)
    cooked = cand(TIER_LEARNED, 2, "гречка варена", preparation="cooked", confirmed_count=2, match=1.0, exact=True)
    ranked = rank(query, [raw, cooked])
    assert [c.product_id for c in ranked] == [2]
    resolution = decide(query, ranked)
    assert resolution.decision == "auto" and resolution.selected.product_id == 2


def test_explicit_brand_and_fat_defeat_favourite():
    query = FoodQuery.from_item({"name_original": "молоко Галичина 2.5%", "brand": "Галичина"})
    fav = cand(TIER_CONFIRMED, 1, "Молоко 3.2%", brand="Яготинське", confirmed_count=50, exact=True, match=1.0)
    ok_fat_no_brand = cand(TIER_HISTORY, 2, "Молоко 2.5%", match=1.0)
    ranked = rank(query, [fav, ok_fat_no_brand])
    assert [c.product_id for c in ranked] == [2]
    assert "brand" in ranked[0].uncertain
    assert decide(query, ranked).decision == "choose"


def test_ambiguous_generic_shows_candidates():
    query = FoodQuery.from_item({"name_original": "сир"})
    ranked = rank(query, [
        cand(TIER_HISTORY, 1, "Сир твердий", match=1.0),
        cand(TIER_HISTORY, 2, "Сир кисломолочний 5%", match=1.0),
    ])
    resolution = decide(query, ranked)
    assert resolution.decision == "choose" and len(resolution.candidates) == 2


def test_pinned_default_auto_but_not_against_brand_or_barcode():
    rule = cand(TIER_DEFAULT, 5, "Йогурт натуральний 2%", brand="Brand A", match=1.0, exact=True)
    generic = FoodQuery.from_item({"name_original": "мій йогурт"})
    assert decide(generic, rank(generic, [rule])).decision == "auto"

    other_brand = FoodQuery.from_item({"name_original": "йогурт", "brand": "Brand B"})
    assert rank(other_brand, [rule]) == []

    other_fat = FoodQuery.from_item({"name_original": "йогурт 5%"})
    assert rank(other_fat, [rule]) == []

    scanned = FoodQuery(text="йогурт", barcode="4006381333931")
    barcode_product = cand(TIER_EXPLICIT, 9, "Інший йогурт", barcode="4006381333931", match=1.0, exact=True)
    rule_with_barcode = cand(TIER_DEFAULT, 5, "Йогурт натуральний 2%", barcode="4820000000000", match=1.0)
    ranked = rank(scanned, [rule_with_barcode, barcode_product])
    assert [c.product_id for c in ranked] == [9]


def test_review_all_never_auto_selects_history():
    query = FoodQuery.from_item({"name_original": "гречка варена"})
    top = cand(TIER_LEARNED, 1, "гречка варена", preparation="cooked", match=1.0, exact=True)
    assert decide(query, rank(query, [top]), review_all=True).decision == "choose"


def test_two_confirmed_exact_matches_are_ambiguous():
    query = FoodQuery.from_item({"name_original": "вівсянка"})
    a = cand(TIER_LEARNED, 1, "вівсянка", match=1.0, exact=True, confirmed_count=3,
             last_used_at=datetime(2026, 9, 1, tzinfo=timezone.utc))
    b = cand(TIER_LEARNED, 2, "вівсянка", match=1.0, exact=True, confirmed_count=1)
    assert decide(query, rank(query, [a, b])).decision == "choose"


def test_recency_only_breaks_ties():
    query = FoodQuery.from_item({"name_original": "рис"})
    old_exact = cand(TIER_HISTORY, 1, "рис", match=1.0, exact=True, history_count=1)
    recent_partial = cand(TIER_HISTORY, 2, "рис басматі", match=1.0, exact=False, history_count=40,
                          last_used_at=datetime(2026, 9, 25, tzinfo=timezone.utc))
    assert [c.product_id for c in rank(query, [recent_partial, old_exact])] == [1, 2]


def test_match_score_uses_stems():
    score, exact = match_score("гречки вареної", "Гречка варена")
    assert score == 1.0 and not exact
    assert match_score("гречка варена", "гречка варена") == (1.0, True)


def test_query_parses_fat_from_text():
    assert FoodQuery.from_item({"name_original": "кефір 1,5%"}).fat_pct == Decimal("1.5")


def test_merge_choices_keeps_history_and_other_search_hits():
    from app.services.food_resolver import TIER_HISTORY, TIER_SEARCH, merge_choices

    history = cand(TIER_HISTORY, 3, "Зеленый Борщ")
    history.external_id = "7492318"
    same = cand(TIER_SEARCH, None, "Зелений борщ")
    same.external_id = "7492318"
    other = cand(TIER_SEARCH, None, "Борщ зелений з кропивою")
    other.external_id = "34499"
    third = cand(TIER_SEARCH, None, "Зелений борщ з яйцем")
    third.external_id = "35500"
    merged = merge_choices([history], [same, other, third])
    assert [c.external_id for c in merged] == ["7492318", "34499", "35500"]


def test_ukrainian_search_order_tries_ukraine_before_us():
    from app.services.fatsecret_api import food_locales

    assert food_locales("uk") == [("UA", "uk"), ("UA", "ru"), (None, None)]
    assert food_locales("en") == [(None, None)]


def test_compatibility_uncertain_preparation():
    query = FoodQuery.from_item({"name_original": "рис варений"})
    ok, uncertain = compatibility(query, cand(TIER_HISTORY, 1, "Рис"))
    assert ok and uncertain == ["preparation"]
