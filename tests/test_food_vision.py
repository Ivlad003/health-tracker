"""Vision payload validation + image intake bounds (plan §5.C/D, AC-06, AC-07)."""
import io
from decimal import Decimal

import pytest

from app.services.food_vision import MediaError, check_upload, prepare_for_vision, validate_vision_payload


def test_label_per_100g_is_usable_and_package_weight_separate():
    result = validate_vision_payload({
        "kind": "label", "product_name": "Сирок", "brand": "X",
        "label": {"basis": "100g", "energy_kcal": "246", "protein_g": "8,5", "fat_g": None,
                  "carbs_g": 30, "package_net_weight_g": 400},
    })
    assert result.label.usable
    assert result.label.basis.energy_kcal == Decimal(246)
    assert result.label.basis.fat_g is None  # absent macro is not zero
    assert "fat_g" in result.label.missing
    assert result.label.package_net_grams == Decimal(400)  # never used as eaten weight


def test_label_without_energy_is_unusable():
    result = validate_vision_payload({"kind": "label", "label": {"basis": "100g", "protein_g": 10}})
    assert not result.label.usable
    assert "energy" in result.label.missing


def test_per_serving_without_grams_and_per_100ml_are_unusable():
    serving = validate_vision_payload({"kind": "label", "label": {"basis": "serving", "energy_kcal": 120}})
    assert not serving.label.usable and "serving_grams_missing" in serving.label.problems
    ml = validate_vision_payload({"kind": "label", "label": {"basis": "100ml", "energy_kcal": 42}})
    assert not ml.label.usable


def test_negative_values_dropped_and_kj_used():
    result = validate_vision_payload({
        "kind": "label", "label": {"basis": "100g", "energy_kj": 1000, "fat_g": -3},
    })
    assert result.label.basis.fat_g is None
    assert "fat_g_negative" in result.label.problems
    assert result.label.basis.energy_kcal.quantize(Decimal("0.1")) == Decimal("239.0")


def test_plate_items_and_tentative_barcode_digits():
    result = validate_vision_payload({
        "kind": "plate", "barcode_digits": "48 2000 1234567",
        "plate_items": [{"name_en": "rice", "name_original": "рис", "preparation": "boiled"},
                        {"name_en": "chicken", "preparation": "fried"}, "junk"],
    })
    assert [p.name_en for p in result.plate_items] == ["rice", "chicken"]
    assert result.plate_items[0].preparation == "cooked"
    assert result.barcode_digits == "4820001234567"


def test_garbage_payload_is_other():
    assert validate_vision_payload("nope").kind == "other"
    assert validate_vision_payload({"kind": "selfie"}).kind == "other"


def test_upload_bounds(mock_settings):
    with pytest.raises(MediaError, match="image_empty"):
        check_upload(b"", "image/jpeg")
    with pytest.raises(MediaError, match="image_type_unsupported"):
        check_upload(b"x", "application/pdf")
    with pytest.raises(MediaError, match="image_too_large"):
        check_upload(b"x" * (10 * 1024 * 1024 + 1), "image/png")


@pytest.mark.asyncio
async def test_prepare_strips_metadata_and_downscales(mock_settings):
    from PIL import Image

    img = Image.new("RGB", (4000, 3000), (200, 10, 10))
    buf = io.BytesIO()
    exif = Image.Exif()
    exif[0x010E] = "secret description"
    img.save(buf, format="JPEG", exif=exif)
    out = await prepare_for_vision(buf.getvalue())
    with Image.open(io.BytesIO(out)) as prepared:
        assert max(prepared.size) == 1600
        assert not prepared.getexif()
    with pytest.raises(MediaError):
        await prepare_for_vision(b"not an image")
