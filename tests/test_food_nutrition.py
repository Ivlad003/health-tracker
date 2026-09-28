"""Deterministic nutrition arithmetic (plan FR-08/FR-09, AC-03, AC-04, AC-06)."""
from decimal import Decimal

import pytest

from app.services.food_nutrition import (
    DIARY_BLURB_SERVING_ID,
    NutritionError,
    basis_from_fatsecret_serving,
    calculate_portion,
    choose_gram_serving,
    fatsecret_units_for_grams,
    make_basis,
    parse_quantity_text,
    per_100g,
    serving_from_per_100g_blurb,
    sum_nutrients,
    to_decimal,
    validate_grams,
    writable_gram_serving,
)


def test_label_example_246_kcal_per_100g_times_135g():
    basis = make_basis(basis_quantity=100, basis_unit="g", energy_kcal=246, protein_g="10,5")
    portion = calculate_portion(basis, 135)
    assert portion.energy_kcal == Decimal("332.1")
    assert portion.protein_g == Decimal("14.2")  # 10.5 * 1.35 = 14.175 → 14.2
    assert portion.fat_g is None  # unknown stays unknown, not 0


def test_per_serving_label_with_serving_mass():
    basis = make_basis(basis_quantity=1, basis_unit="serving", grams_per_basis=30, energy_kcal=120)
    assert per_100g(basis).energy_kcal == Decimal(400)
    assert calculate_portion(basis, 45).energy_kcal == Decimal("180.0")


def test_per_serving_without_mass_cannot_calculate():
    basis = make_basis(basis_quantity=1, basis_unit="serving", energy_kcal=120)
    with pytest.raises(NutritionError, match="basis_not_mass_based"):
        calculate_portion(basis, 100)


def test_kj_converts_with_4_184():
    basis = make_basis(basis_quantity=100, basis_unit="g", energy_kj=1000)
    assert calculate_portion(basis, 100).energy_kcal == Decimal("239.0")


def test_ml_without_density_is_not_grams():
    basis = make_basis(basis_quantity=100, basis_unit="ml", energy_kcal=42)
    assert not basis.mass_based
    with pytest.raises(NutritionError):
        calculate_portion(basis, 250)
    with pytest.raises(NutritionError, match="volume_needs_density"):
        parse_quantity_text("250 мл")


def test_missing_energy_keeps_draft():
    basis = make_basis(basis_quantity=100, basis_unit="g", protein_g=10)
    with pytest.raises(NutritionError, match="energy_missing"):
        calculate_portion(basis, 100)


@pytest.mark.parametrize("value", ["abc", None, "NaN", "inf", -5, 0, "0", 6000, "1e9"])
def test_invalid_weights_rejected(value):
    with pytest.raises(NutritionError):
        validate_grams(value)


def test_negative_nutrients_rejected():
    with pytest.raises(NutritionError):
        make_basis(basis_quantity=100, basis_unit="g", energy_kcal=-1)


def test_implausible_energy_rejected():
    with pytest.raises(NutritionError, match="energy_implausible"):
        make_basis(basis_quantity=100, basis_unit="g", energy_kcal=2460)


@pytest.mark.parametrize(
    ("text", "grams"),
    [("135 г", "135"), ("135g", "135"), ("0,2 кг", "200.0"), ("гречка варена 180 грам", "180"),
     ("1.5 kg", "1500.0"), ("2 oz", "56.699046250")],
)
def test_parse_quantity_text(text, grams):
    assert parse_quantity_text(text) == Decimal(grams)


def test_bare_number_only_for_draft_replies():
    assert parse_quantity_text("135") is None
    assert parse_quantity_text("135", allow_bare_number=True) == Decimal(135)
    # two quantities are ambiguous
    assert parse_quantity_text("рис 200 г, курка 150 г") is None


def test_decimal_parsing_variants():
    assert to_decimal("1 234,5") == Decimal("1234.5")
    assert to_decimal("1,234.5") == Decimal("1234.5")
    assert to_decimal("NaN") is None
    assert to_decimal(True) is None


def test_fatsecret_serving_units_and_choice():
    servings = [
        {"serving_id": "0", "metric_serving_amount": "1", "metric_serving_unit": "g",
         "number_of_units": "1", "calories": "3.43", "description": "1 g"},
        {"serving_id": "10", "metric_serving_amount": "100", "metric_serving_unit": "g",
         "number_of_units": "100", "calories": "343", "description": "100 g"},
        {"serving_id": "11", "metric_serving_amount": "240", "metric_serving_unit": "ml",
         "number_of_units": "1", "calories": "100", "description": "1 cup"},
    ]
    chosen = choose_gram_serving(servings)
    assert chosen["serving_id"] == "10"  # derived id 0 is never writable
    assert fatsecret_units_for_grams(chosen, Decimal(180)) == Decimal("180.00")
    assert basis_from_fatsecret_serving(servings[2]) is None  # ml ≠ g
    basis = basis_from_fatsecret_serving(chosen)
    assert calculate_portion(basis, 180).energy_kcal == Decimal("617.4")


def test_diary_per_100g_blurb_is_a_local_gram_serving():
    blurb = "Per 100g - Calories: 158kcal | Fat: 0.93g | Carbs: 30.20g | Protein: 5.10g"
    serving = serving_from_per_100g_blurb(blurb)
    assert serving["serving_id"] == DIARY_BLURB_SERVING_ID
    assert serving["calories"] == "158"
    assert serving["carbohydrate"] == "30.20"
    assert serving["fat"] == "0.93"
    assert serving["protein"] == "5.10"
    basis = basis_from_fatsecret_serving(serving)
    assert calculate_portion(basis, 155).energy_kcal == Decimal("244.9")


def test_blurb_ignores_another_serving_and_missing_calories():
    mixed = "Per 1 cup - Calories: 300kcal | Per 100g - Calories: 158kcal | Fat: 1.00g | Carbs: 20g | Protein: 4g"
    assert serving_from_per_100g_blurb(mixed)["calories"] == "158"
    assert serving_from_per_100g_blurb("Per 1 serving - Calories: 200kcal | Fat: 1g") is None
    assert serving_from_per_100g_blurb("Per 100g - Fat: 1.00g | Carbs: 2g | Protein: 3g") is None
    assert serving_from_per_100g_blurb(None) is None


def test_diary_blurb_serving_is_not_sent_to_fatsecret():
    blurb = serving_from_per_100g_blurb("Per 100g - Calories: 110kcal | Fat: 1g | Carbs: 2g | Protein: 3g")
    real = {"serving_id": "10", "metric_serving_amount": "100", "metric_serving_unit": "g",
            "number_of_units": "100", "calories": "110", "description": "100 g"}
    assert writable_gram_serving([blurb]) is None
    assert writable_gram_serving([blurb, real], "10")["serving_id"] == "10"
    assert writable_gram_serving([blurb, real], DIARY_BLURB_SERVING_ID)["serving_id"] == "10"


def test_sum_nutrients_marks_partial():
    total = sum_nutrients([{"energy_kcal": Decimal("100")}, {"energy_kcal": None}])
    assert total == {"energy_kcal": Decimal("100.0"), "partial": True}
