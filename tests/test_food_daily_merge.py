"""Daily total = local ledger ∪ remote diary, linked entries once (plan §7.5, AC-10)."""
from datetime import date
from decimal import Decimal

from app.services.food_logging import merge_daily

DAY = date(2026, 9, 26)


def local(id_, kcal, *, status="synced", remote=None, entry_status="committed", food="1", serving="2", outbox=None):
    return {
        "id": id_, "food_name": f"food{id_}", "calories": Decimal(kcal) if kcal is not None else None,
        "protein": None, "fat": None, "carbs": None, "grams": Decimal(100), "meal_type": "lunch",
        "entry_status": entry_status, "sync_status": status, "remote_entry_id": remote,
        "remote_food_id": food, "remote_serving_id": serving, "version": 1, "origin": "bot_text",
        "outbox_status": outbox,
    }


def remote(rid, kcal, food="1", serving="2"):
    return {"food_entry_id": rid, "food_id": food, "serving_id": serving, "calories": str(kcal),
            "protein": "0", "fat": "0", "carbohydrate": "0", "name": f"r{rid}", "meal": "Lunch",
            "number_of_units": "100"}


def test_linked_entry_counts_once():
    view = merge_daily([local(1, 200, remote="77")], [remote("77", 210)], local_date=DAY, remote_connected=True)
    assert view.total_kcal == Decimal(210)  # remote value for a synced entry
    assert not view.partial


def test_remote_only_and_local_only_both_count():
    view = merge_daily(
        [local(1, 100, status="local_only", remote=None)],
        [remote("88", 300)], local_date=DAY, remote_connected=True,
    )
    assert view.total_kcal == Decimal(400)
    assert {e["source"] for e in view.entries} == {"local", "fatsecret_only"}


def test_delayed_remote_entry_does_not_disappear():
    # synced locally, FatSecret read does not show it yet → still counted once
    view = merge_daily([local(1, 150, remote="99")], [], local_date=DAY, remote_connected=True)
    assert view.total_kcal == Decimal(150)
    assert "remote_entry_missing" in view.reasons


def test_provider_unavailable_is_partial_not_zero():
    view = merge_daily([local(1, 150, remote="99")], None, local_date=DAY, remote_connected=True)
    assert view.total_kcal == Decimal(150)
    assert view.partial and "provider_unavailable" in view.reasons


def test_ambiguous_sync_suppresses_one_matching_remote():
    view = merge_daily(
        [local(1, 150, status="unknown", outbox="unknown")],
        [remote("55", 150)], local_date=DAY, remote_connected=True,
    )
    assert view.total_kcal == Decimal(150)
    assert view.partial and "sync_ambiguous" in view.reasons


def test_voided_entry_with_failed_remote_delete_is_not_counted():
    view = merge_daily(
        [local(1, 150, remote="66", entry_status="voided", status="delete_failed")],
        [remote("66", 150)], local_date=DAY, remote_connected=True,
    )
    assert view.total_kcal == Decimal(0)
    assert "remote_delete_failed" in view.reasons


def test_unknown_nutrition_marks_partial():
    view = merge_daily([local(1, None, status="local_only")], None, local_date=DAY, remote_connected=False)
    assert view.total_kcal == Decimal(0) and view.partial
    assert view.reasons == ["nutrition_unavailable"]


def test_two_equal_legitimate_meals_are_not_merged():
    view = merge_daily(
        [local(1, 100, remote="1"), local(2, 100, remote="2")],
        [remote("1", 100), remote("2", 100)], local_date=DAY, remote_connected=True,
    )
    assert view.total_kcal == Decimal(200)
