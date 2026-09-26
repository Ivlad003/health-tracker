"""Image intake + OpenAI vision extraction for food photos (plan §5.C/D, §8).

- Images are downloaded by the backend and sent as a data URL — never a
  Telegram file URL (it contains the bot token).
- Processing copies are orientation-normalized, re-encoded (metadata
  stripped) and bounded in bytes and decoded pixels; nothing is stored.
- The model only *extracts* visible facts into a strict JSON shape; the
  backend validates every number and does all arithmetic. Missing fields
  stay ``None``; model "confidence" is never used to auto-commit.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

from app.config import settings
from app.services.food_nutrition import NutritionBasis, NutritionError, make_basis, to_decimal

logger = logging.getLogger(__name__)

VISION_MAX_SIDE = 1600
ALLOWED_MIME = ("image/jpeg", "image/png", "image/webp")
_semaphore: Optional[asyncio.Semaphore] = None


class MediaError(ValueError):
    """Rejected image (code in args[0])."""


def _sem() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(max(1, settings.vision_max_concurrency))
    return _semaphore


def check_upload(data: bytes, mime: Optional[str]) -> None:
    if not data:
        raise MediaError("image_empty")
    if len(data) > settings.media_max_bytes:
        raise MediaError("image_too_large")
    if mime and mime.split(";")[0].strip().lower() not in ALLOWED_MIME:
        raise MediaError("image_type_unsupported")


def _prepare_sync(data: bytes, max_side: int) -> bytes:
    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = settings.media_max_pixels
    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.format not in ("JPEG", "PNG", "WEBP", "MPO"):
                raise MediaError("image_type_unsupported")
            if img.width * img.height > settings.media_max_pixels:
                raise MediaError("image_too_large")
            img.load()
            img = ImageOps.exif_transpose(img).convert("RGB")
            img.thumbnail((max_side, max_side))
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=85)  # no EXIF/metadata copied
            return out.getvalue()
    except MediaError:
        raise
    except Image.DecompressionBombError as exc:
        raise MediaError("image_too_large") from exc
    except Exception as exc:
        raise MediaError("image_unreadable") from exc


async def prepare_for_vision(data: bytes) -> bytes:
    return await asyncio.to_thread(_prepare_sync, data, VISION_MAX_SIDE)


# ---------------------------------------------------------------------------
# Typed extraction results
# ---------------------------------------------------------------------------

@dataclass
class LabelExtraction:
    name: Optional[str]
    brand: Optional[str]
    basis: Optional[NutritionBasis]
    basis_label: Optional[str]  # "100g" | "100ml" | "serving"
    serving_grams: Optional[Decimal]
    package_net_grams: Optional[Decimal]
    missing: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        """Mass-based with energy → can become a reusable personal product."""
        return self.basis is not None and self.basis.has_energy and self.basis.mass_based

    def to_json(self) -> dict:
        b = self.basis
        return {
            "name": self.name, "brand": self.brand, "basis_label": self.basis_label,
            "serving_grams": str(self.serving_grams) if self.serving_grams is not None else None,
            "package_net_grams": str(self.package_net_grams) if self.package_net_grams is not None else None,
            "missing": self.missing, "problems": self.problems,
            "nutrients": None if b is None else {k: (str(v) if v is not None else None)
                                                 for k, v in b.nutrients().items()},
            "basis": None if b is None else {
                "basis_quantity": str(b.basis_quantity), "basis_unit": b.basis_unit,
                "grams_per_basis": str(b.grams_per_basis) if b.grams_per_basis is not None else None,
            },
        }


@dataclass
class PlateItem:
    name_en: str
    name_original: str
    preparation: Optional[str]


@dataclass
class VisionResult:
    kind: str  # "label" | "package" | "plate" | "other"
    product_name: Optional[str] = None
    brand: Optional[str] = None
    barcode_digits: Optional[str] = None  # tentative until validated
    label: Optional[LabelExtraction] = None
    plate_items: list[PlateItem] = field(default_factory=list)


VISION_PROMPT = """You extract facts from ONE food-related photo. Never guess values you cannot read.
Classify the photo:
- "label": a nutrition facts panel is readable
- "package": product packaging/front without a readable nutrition panel
- "plate": prepared food / a dish / a plate
- "other": anything else
Return ONLY JSON:
{
 "kind": "label|package|plate|other",
 "product_name": string|null, "brand": string|null,
 "barcode_digits": string|null,
 "label": null | {
   "basis": "100g"|"100ml"|"serving"|null,
   "serving_size_g": number|null,
   "energy_kcal": number|null, "energy_kj": number|null,
   "protein_g": number|null, "fat_g": number|null, "carbs_g": number|null,
   "fiber_g": number|null, "sugar_g": number|null, "salt_g": number|null,
   "package_net_weight_g": number|null
 },
 "plate_items": [{"name_en": string, "name_original": string, "preparation": "raw|cooked|fried|baked|boiled|unknown"}]
}
Rules: copy numbers exactly as printed for the stated basis; use null for anything not visible;
"package_net_weight_g" is the package weight, NOT an eaten amount; do not estimate grams of a dish;
plate_items lists only clearly visible distinct foods (max 6); use the user's language for name_original."""


def _clean_text(value: Any, limit: int = 255) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] or None


def validate_label(raw: Any) -> Optional[LabelExtraction]:
    if not isinstance(raw, dict):
        return None
    problems: list[str] = []
    basis_label = raw.get("basis") if raw.get("basis") in ("100g", "100ml", "serving") else None
    serving_g = to_decimal(raw.get("serving_size_g"))
    if serving_g is not None and serving_g <= 0:
        serving_g, problems = None, problems + ["serving_size_invalid"]
    package_g = to_decimal(raw.get("package_net_weight_g"))
    values = {k: to_decimal(raw.get(k)) for k in (
        "energy_kcal", "energy_kj", "protein_g", "fat_g", "carbs_g", "fiber_g", "sugar_g", "salt_g")}
    for key, value in list(values.items()):
        if value is not None and value < 0:
            values[key] = None
            problems.append(f"{key}_negative")
    missing = [k for k in ("energy", "protein_g", "fat_g", "carbs_g")
               if (values.get(k) if k != "energy" else (values["energy_kcal"] or values["energy_kj"])) is None]
    basis = None
    if basis_label is None:
        problems.append("basis_unknown")
    else:
        try:
            if basis_label == "100g":
                basis = make_basis(basis_quantity=100, basis_unit="g", **values)
            elif basis_label == "100ml":
                basis = make_basis(basis_quantity=100, basis_unit="ml", **values)
            else:
                basis = make_basis(basis_quantity=1, basis_unit="serving", grams_per_basis=serving_g, **values)
                if serving_g is None:
                    problems.append("serving_grams_missing")
        except NutritionError as exc:
            problems.append(str(exc))
            basis = None
    return LabelExtraction(
        name=None, brand=None, basis=basis, basis_label=basis_label, serving_grams=serving_g,
        package_net_grams=package_g if package_g and package_g > 0 else None,
        missing=missing, problems=problems,
    )


def validate_vision_payload(data: Any) -> VisionResult:
    if not isinstance(data, dict):
        return VisionResult(kind="other")
    kind = data.get("kind") if data.get("kind") in ("label", "package", "plate", "other") else "other"
    digits = data.get("barcode_digits")
    digits = "".join(ch for ch in str(digits) if ch.isdigit()) if digits else None
    result = VisionResult(
        kind=kind,
        product_name=_clean_text(data.get("product_name")),
        brand=_clean_text(data.get("brand")),
        barcode_digits=digits if digits and 8 <= len(digits) <= 14 else None,
    )
    if kind == "label":
        result.label = validate_label(data.get("label"))
        if result.label:
            result.label.name = result.product_name
            result.label.brand = result.brand
    if kind == "plate":
        items = data.get("plate_items") if isinstance(data.get("plate_items"), list) else []
        for item in items[:6]:
            if not isinstance(item, dict):
                continue
            name_en = _clean_text(item.get("name_en"), 100)
            if not name_en:
                continue
            prep = item.get("preparation")
            prep = "cooked" if prep in ("cooked", "fried", "baked", "boiled") else (
                "raw" if prep == "raw" else None)
            result.plate_items.append(PlateItem(
                name_en=name_en, name_original=_clean_text(item.get("name_original"), 100) or name_en,
                preparation=prep,
            ))
    return result


async def analyze_food_image(image_bytes: bytes, *, caption: str = "", language: str = "uk") -> VisionResult:
    """One bounded vision call. Raises MediaError for rejected images."""
    from app.services.ai_assistant import client

    prepared = await prepare_for_vision(image_bytes)
    data_url = "data:image/jpeg;base64," + base64.b64encode(prepared).decode()
    user_text = f"User language: {language}. Caption: {caption[:300]}" if caption else f"User language: {language}."
    async with _sem():
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=settings.openai_vision_model,
                messages=[
                    {"role": "system", "content": VISION_PROMPT},
                    {"role": "user", "content": [
                        {"type": "text", "text": user_text},
                        {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
                    ]},
                ],
                temperature=0,
                max_tokens=700,
                response_format={"type": "json_object"},
            ),
            timeout=max(30.0, settings.http_timeout_seconds * 3),
        )
    raw = response.choices[0].message.content or "{}"
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Vision returned invalid JSON")
        return VisionResult(kind="other")
    return validate_vision_payload(payload)
