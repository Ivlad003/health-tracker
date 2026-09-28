"""Macro estimate parsing. The model must return three numbers; kcal is never taken from it."""
from decimal import Decimal

from app.services.custom_food import parse_macro_estimate


def test_parse_macro_estimate_accepts_three_numbers():
    parsed = parse_macro_estimate('{"protein_g": 1.2, "fat_g": 0, "carbs_g": 4.5}')
    assert parsed == {
        "protein_g": Decimal("1.2"),
        "fat_g": Decimal("0.0"),
        "carbs_g": Decimal("4.5"),
    }


def test_parse_macro_estimate_rejects_missing_or_wild_values():
    assert parse_macro_estimate('{"protein_g": 1}') is None
    assert parse_macro_estimate('{"protein_g": 1, "fat_g": -1, "carbs_g": 1}') is None
    assert parse_macro_estimate("not json") is None
