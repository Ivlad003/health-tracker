"""Deterministic nutrition arithmetic (plan FR-08/FR-09).

Rules enforced here, never in a prompt:
- ``Decimal`` everywhere; ``portion = per_100g × grams / 100``.
- Unknown nutrients stay ``None`` (never 0). Missing energy means the portion
  cannot be calculated and the caller must keep a draft.
- kJ → kcal uses 4.184; mass ounces/pounds convert explicitly; volume (ml)
  never converts to mass without a product-specific density.
- Weights must be finite, positive and bounded.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Iterable, Optional

KJ_PER_KCAL = Decimal("4.184")
GRAMS_PER_OZ = Decimal("28.349523125")
GRAMS_PER_LB = Decimal("453.59237")
MAX_PORTION_GRAMS = Decimal("5000")
MIN_PORTION_GRAMS = Decimal("0.1")
# Energy density sanity bound: pure fat is ~900 kcal/100 g.
MAX_KCAL_PER_100G = Decimal("950")
# Diary lines cache calories under this id. It is not a FatSecret serving id,
# so it must never be sent to food_entry.create.
DIARY_BLURB_SERVING_ID = "per100g"

NUTRIENT_FIELDS = ("energy_kcal", "protein_g", "fat_g", "carbs_g", "fiber_g", "sugar_g", "salt_g")
MASS_UNITS = {"g": Decimal(1), "kg": Decimal(1000), "mg": Decimal("0.001"),
              "oz": GRAMS_PER_OZ, "lb": GRAMS_PER_LB}
VOLUME_UNITS = {"ml": Decimal(1), "l": Decimal(1000), "cl": Decimal(10), "dl": Decimal(100)}

_UNIT_ALIASES = {
    "g": "g", "gr": "g", "gram": "g", "grams": "g", "gramm": "g", "г": "g", "гр": "g",
    "грам": "g", "грами": "g", "грамів": "g", "грама": "g",
    "kg": "kg", "кг": "kg", "kilogram": "kg", "кілограм": "kg",
    "mg": "mg", "мг": "mg",
    "oz": "oz", "ounce": "oz", "ounces": "oz", "унц": "oz",
    "lb": "lb", "lbs": "lb", "pound": "lb",
    "ml": "ml", "мл": "ml", "l": "l", "л": "l", "cl": "cl", "dl": "dl",
}


class NutritionError(ValueError):
    """Invalid quantity/unit/nutrition input (reported to the user, never 0 kcal)."""


def to_decimal(value: Any) -> Optional[Decimal]:
    """Parse numbers incl. decimal commas; ``None``/blank/NaN/inf → ``None``."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        result = value
    else:
        text = str(value).strip().replace("\u00a0", "").replace(" ", "")
        if not text:
            return None
        if "," in text and "." not in text:
            text = text.replace(",", ".")
        elif "," in text and "." in text:
            # "1.234,5" or "1,234.5": the last separator is the decimal one.
            if text.rfind(",") > text.rfind("."):
                text = text.replace(".", "").replace(",", ".")
            else:
                text = text.replace(",", "")
        try:
            result = Decimal(text)
        except (InvalidOperation, ValueError):
            return None
    if not result.is_finite():
        return None
    return result


def normalize_unit(unit: Optional[str]) -> Optional[str]:
    if unit is None:
        return None
    key = str(unit).strip().lower().rstrip(".")
    return _UNIT_ALIASES.get(key, key if key in MASS_UNITS or key in VOLUME_UNITS else None)


def to_grams(amount: Any, unit: Optional[str]) -> Decimal:
    """Convert a mass quantity to grams. Volume/unknown units raise."""
    value = to_decimal(amount)
    if value is None:
        raise NutritionError("quantity_invalid")
    norm = normalize_unit(unit or "g")
    if norm in VOLUME_UNITS:
        raise NutritionError("volume_needs_density")
    if norm not in MASS_UNITS:
        raise NutritionError("unit_unknown")
    return value * MASS_UNITS[norm]


def validate_grams(value: Any) -> Decimal:
    """Portion weight in grams: finite, > 0, ≤ 5 kg."""
    grams = to_decimal(value)
    if grams is None:
        raise NutritionError("grams_invalid")
    if grams < MIN_PORTION_GRAMS:
        raise NutritionError("grams_not_positive")
    if grams > MAX_PORTION_GRAMS:
        raise NutritionError("grams_too_large")
    return grams


_GRAMS_RE = re.compile(
    r"(?<![\w.,])(\d{1,5}(?:[.,]\d{1,2})?)\s*(кг|kg|г|гр|грам\w*|g|gr|grams?|oz|lbs?|мл|ml|л|l)(?![a-zа-яіїєґ])",
    re.IGNORECASE,
)
_BARE_NUMBER_RE = re.compile(r"^\s*(\d{1,5}(?:[.,]\d{1,2})?)\s*$")


def parse_quantity_text(text: str, *, allow_bare_number: bool = False) -> Optional[Decimal]:
    """Extract an explicit mass in grams from free text ("135 г", "0,2 кг").

    A bare number ("135") is accepted only when the caller is replying to a
    specific draft (AC-04); otherwise it is ambiguous and returns ``None``.
    Volumes raise ``NutritionError("volume_needs_density")``.
    """
    if not text:
        return None
    matches = list(_GRAMS_RE.finditer(text))
    if len(matches) > 1:
        return None
    if matches:
        amount, unit = matches[0].group(1), matches[0].group(2)
        return validate_grams(to_grams(amount, unit))
    if allow_bare_number:
        bare = _BARE_NUMBER_RE.match(text)
        if bare:
            return validate_grams(bare.group(1))
    return None


def energy_kcal_from(kcal: Any = None, kj: Any = None) -> Optional[Decimal]:
    """Prefer explicit kcal; otherwise derive from kJ ÷ 4.184."""
    kcal_d = to_decimal(kcal)
    if kcal_d is not None:
        return kcal_d
    kj_d = to_decimal(kj)
    if kj_d is not None:
        return kj_d / KJ_PER_KCAL
    return None


@dataclass(frozen=True)
class NutritionBasis:
    """Nutrients for ``basis_quantity`` of ``basis_unit``.

    ``grams_per_basis`` is the mass of one basis (100 for "per 100 g", the
    serving mass for per-serving data). It is ``None`` for per-100-ml data
    and servings with unknown mass: such a basis cannot calculate a gram
    portion.
    """

    basis_quantity: Decimal
    basis_unit: str  # "g" | "ml" | "serving"
    grams_per_basis: Optional[Decimal]
    energy_kcal: Optional[Decimal] = None
    protein_g: Optional[Decimal] = None
    fat_g: Optional[Decimal] = None
    carbs_g: Optional[Decimal] = None
    fiber_g: Optional[Decimal] = None
    sugar_g: Optional[Decimal] = None
    salt_g: Optional[Decimal] = None
    extra: dict = field(default_factory=dict)

    def nutrients(self) -> dict[str, Optional[Decimal]]:
        return {name: getattr(self, name) for name in NUTRIENT_FIELDS}

    @property
    def has_energy(self) -> bool:
        return self.energy_kcal is not None

    @property
    def mass_based(self) -> bool:
        return self.grams_per_basis is not None and self.grams_per_basis > 0


def make_basis(
    *,
    basis_quantity: Any,
    basis_unit: str,
    grams_per_basis: Any = None,
    energy_kcal: Any = None,
    energy_kj: Any = None,
    protein_g: Any = None,
    fat_g: Any = None,
    carbs_g: Any = None,
    fiber_g: Any = None,
    sugar_g: Any = None,
    salt_g: Any = None,
) -> NutritionBasis:
    """Validate and build a basis. Negative nutrients are rejected."""
    qty = to_decimal(basis_quantity)
    if qty is None or qty <= 0:
        raise NutritionError("basis_quantity_invalid")
    unit = normalize_unit(basis_unit) if basis_unit != "serving" else "serving"
    grams: Optional[Decimal]
    if unit in MASS_UNITS:
        grams = to_grams(qty, unit)
        qty, unit = grams, "g"
    elif unit in VOLUME_UNITS:
        qty = qty * VOLUME_UNITS[unit]
        unit = "ml"
        grams = None  # needs density; never assume 1 g/ml
    elif unit == "serving":
        grams = to_decimal(grams_per_basis)
        if grams is not None and grams <= 0:
            grams = None
    else:
        raise NutritionError("unit_unknown")
    values = {
        "energy_kcal": energy_kcal_from(energy_kcal, energy_kj),
        "protein_g": to_decimal(protein_g),
        "fat_g": to_decimal(fat_g),
        "carbs_g": to_decimal(carbs_g),
        "fiber_g": to_decimal(fiber_g),
        "sugar_g": to_decimal(sugar_g),
        "salt_g": to_decimal(salt_g),
    }
    for name, value in values.items():
        if value is not None and value < 0:
            raise NutritionError(f"{name}_negative")
    basis = NutritionBasis(basis_quantity=qty, basis_unit=unit, grams_per_basis=grams, **values)
    per100 = per_100g(basis)
    if per100 is not None and per100.energy_kcal is not None and per100.energy_kcal > MAX_KCAL_PER_100G:
        raise NutritionError("energy_implausible")
    return basis


def per_100g(basis: NutritionBasis) -> Optional[NutritionBasis]:
    """Normalize a mass-based basis to per-100 g; ``None`` when impossible."""
    if not basis.mass_based:
        return None
    factor = Decimal(100) / basis.grams_per_basis  # type: ignore[operator]
    scaled = {
        name: (value * factor if value is not None else None)
        for name, value in basis.nutrients().items()
    }
    return NutritionBasis(
        basis_quantity=Decimal(100), basis_unit="g", grams_per_basis=Decimal(100), **scaled,
    )


@dataclass(frozen=True)
class Portion:
    grams: Decimal
    energy_kcal: Decimal
    protein_g: Optional[Decimal]
    fat_g: Optional[Decimal]
    carbs_g: Optional[Decimal]
    fiber_g: Optional[Decimal] = None
    sugar_g: Optional[Decimal] = None
    salt_g: Optional[Decimal] = None

    def rounded(self) -> "Portion":
        return replace(
            self,
            energy_kcal=q1(self.energy_kcal),
            protein_g=q1(self.protein_g),
            fat_g=q1(self.fat_g),
            carbs_g=q1(self.carbs_g),
            fiber_g=q1(self.fiber_g),
            sugar_g=q1(self.sugar_g),
            salt_g=q2(self.salt_g),
        )


def q1(value: Optional[Decimal]) -> Optional[Decimal]:
    return None if value is None else value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def q2(value: Optional[Decimal]) -> Optional[Decimal]:
    return None if value is None else value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_portion(basis: NutritionBasis, grams: Any) -> Portion:
    """Portion nutrition for ``grams`` of a mass-based basis (rounded to 0.1)."""
    grams_d = validate_grams(grams)
    per100 = per_100g(basis)
    if per100 is None:
        raise NutritionError("basis_not_mass_based")
    if per100.energy_kcal is None:
        raise NutritionError("energy_missing")
    factor = grams_d / Decimal(100)
    values = {
        name: (value * factor if value is not None else None)
        for name, value in per100.nutrients().items()
    }
    return Portion(grams=grams_d, **values).rounded()  # type: ignore[arg-type]


def serving_from_per_100g_blurb(description: Optional[str]) -> Optional[dict]:
    """Diary line like ``Per 100g - Calories: 158kcal | Fat: 0.93g | ...``.

    Only the per-100 g clause is usable. Calories from another serving in the
    same string are ignored. The result is a synthetic gram serving that the
    24 h FatSecret cache can store; its id is not writable.
    """
    if not description:
        return None
    anchor = re.search(r"per\s*100\s*g", description, re.IGNORECASE)
    if anchor is None:
        return None
    window = description[anchor.end():]
    nxt = re.search(r"\bper\s*\d", window, re.IGNORECASE)
    if nxt is not None:
        window = window[: nxt.start()]
    calories = re.search(r"calories:\s*([0-9]+(?:\.[0-9]+)?)\s*kcal", window, re.IGNORECASE)
    if calories is None:
        return None

    def grab(*labels: str) -> Optional[str]:
        for label in labels:
            match = re.search(rf"\b{label}:\s*([0-9]+(?:\.[0-9]+)?)", window, re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    return {
        "serving_id": DIARY_BLURB_SERVING_ID,
        "description": "100 g",
        "metric_serving_amount": "100",
        "metric_serving_unit": "g",
        "number_of_units": "100",
        "calories": calories.group(1),
        "protein": grab("protein"),
        "fat": grab("fat"),
        "carbohydrate": grab("carbohydrate", "carbs"),
    }


def basis_from_fatsecret_serving(serving: dict) -> Optional[NutritionBasis]:
    """Structured FatSecret serving → basis. Only gram-measured servings.

    ``metric_serving_unit`` of ``ml``/``oz`` is not treated as grams; ``oz``
    (mass) is converted explicitly.
    """
    unit = normalize_unit(serving.get("metric_serving_unit") or "")
    amount = to_decimal(serving.get("metric_serving_amount"))
    if amount is None or amount <= 0 or unit not in MASS_UNITS:
        return None
    grams = to_grams(amount, unit)
    try:
        return make_basis(
            basis_quantity=1,
            basis_unit="serving",
            grams_per_basis=grams,
            energy_kcal=serving.get("calories"),
            protein_g=serving.get("protein"),
            fat_g=serving.get("fat"),
            carbs_g=serving.get("carbohydrate", serving.get("carbs")),
            fiber_g=serving.get("fiber"),
            sugar_g=serving.get("sugar"),
        )
    except NutritionError:
        return None


def fatsecret_units_for_grams(serving: dict, grams: Decimal) -> Decimal:
    """FatSecret ``number_of_units`` that represents ``grams`` of ``serving``.

    FatSecret counts base units: a "100 g" serving has number_of_units=100.
    """
    metric = to_decimal(serving.get("metric_serving_amount"))
    unit = normalize_unit(serving.get("metric_serving_unit") or "")
    if metric is None or metric <= 0 or unit not in MASS_UNITS:
        raise NutritionError("serving_not_mass_based")
    metric_g = to_grams(metric, unit)
    base_units = to_decimal(serving.get("number_of_units")) or Decimal(1)
    return q2(grams / metric_g * base_units)  # type: ignore[return-value]


def choose_gram_serving(servings: Iterable[dict]) -> Optional[dict]:
    """Pick a writable mass serving: 1 g → 100 g → smallest pure gram → any gram.

    Derived servings (``serving_id == "0"``) are never writable.
    """
    real = [s for s in servings if str(s.get("serving_id", "0")) not in ("", "0")]
    mass = [s for s in real if basis_from_fatsecret_serving(s) is not None]
    if not mass:
        return None

    def grams(s: dict) -> Decimal:
        return to_grams(s["metric_serving_amount"], s.get("metric_serving_unit"))

    def pure(s: dict) -> bool:
        desc = str(s.get("description") or s.get("serving_description") or "").strip().lower()
        return bool(re.fullmatch(r"\d+(?:[.,]\d+)?\s*g", desc))

    for target in (Decimal(1), Decimal(100)):
        for s in mass:
            if grams(s) == target and (target == 1 or pure(s)):
                return s
    pure_mass = [s for s in mass if pure(s)]
    if pure_mass:
        return min(pure_mass, key=grams)
    return mass[0]


def writable_gram_serving(servings: Iterable[dict], preferred_id: Optional[str] = None) -> Optional[dict]:
    """Real FatSecret serving for ``food_entry.create``.

    ``per100g`` is the diary-blurb cache. It can fill a local portion and
    must not be sent back as a serving id.
    """
    real = [s for s in servings if str(s.get("serving_id") or "") != DIARY_BLURB_SERVING_ID]
    if preferred_id:
        preferred = next((s for s in real if str(s.get("serving_id")) == str(preferred_id)), None)
        if preferred is not None and basis_from_fatsecret_serving(preferred) is not None:
            return preferred
    return choose_gram_serving(real)


def sum_nutrients(portions: Iterable[dict]) -> dict[str, Any]:
    """Sum entry dicts; any unknown energy marks the total partial."""
    total = Decimal(0)
    partial = False
    for item in portions:
        value = item.get("energy_kcal")
        if value is None:
            partial = True
            continue
        total += Decimal(value)
    return {"energy_kcal": q1(total), "partial": partial}
