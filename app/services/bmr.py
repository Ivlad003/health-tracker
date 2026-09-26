"""Basal metabolic rate (Mifflin-St Jeor) from the user profile + latest weight."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional


def mifflin_st_jeor(*, weight_kg: float, height_cm: float, age: int, sex: str) -> int:
    """BMR in kcal/day. sex: 'male' | 'female'."""
    base = 10 * weight_kg + 6.25 * height_cm - 5 * age
    return round(base + (5 if sex == "male" else -161))


def parse_profile_args(text: str) -> Optional[dict[str, Any]]:
    """Parse ``"1990 m 180"`` (any order of sex token) into profile fields."""
    tokens = text.replace(",", " ").split()
    if len(tokens) != 3:
        return None
    birth_year = height = sex = None
    for token in tokens:
        low = token.lower()
        if low in {"m", "male", "ч", "чол", "чоловік", "man"}:
            sex = "male"
        elif low in {"f", "female", "ж", "жін", "жінка", "woman"}:
            sex = "female"
        elif low.isdigit() and len(low) == 4:
            birth_year = int(low)
        else:
            try:
                height = float(low.replace("см", "").replace("cm", ""))
            except ValueError:
                return None
    current_year = date.today().year
    if (
        birth_year is None or sex is None or height is None
        or not current_year - 110 <= birth_year <= current_year - 10
        or not 100 <= height <= 250
    ):
        return None
    return {"birth_year": birth_year, "sex": sex, "height_cm": round(height, 1)}


def compute_bmr(
    *,
    birth_year: Optional[int],
    sex: Optional[str],
    height_cm: Optional[float],
    weight_kg: Optional[float],
    today: Optional[date] = None,
) -> Optional[int]:
    """BMR when every input is known, else None."""
    if not (birth_year and sex in {"male", "female"} and height_cm and weight_kg):
        return None
    age = (today or date.today()).year - int(birth_year)
    return mifflin_st_jeor(
        weight_kg=float(weight_kg), height_cm=float(height_cm), age=age, sex=str(sex),
    )


def prorated_bmr(bmr: int, local_now: datetime) -> int:
    """Share of today's BMR already burned at ``local_now`` (local wall time)."""
    seconds = local_now.hour * 3600 + local_now.minute * 60 + local_now.second
    return round(bmr * seconds / 86400)
