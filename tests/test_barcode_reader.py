"""Barcode normalization + decoding (plan §5.B, AC-05)."""
import io

import pytest

from app.services.barcode_reader import (
    BarcodeError,
    decode_barcodes,
    expand_upce,
    gtin_check_digit,
    normalize_barcode,
    off_normalize,
)


def _with_check(body: str) -> str:
    return body + gtin_check_digit(body)


def test_ean13_valid_and_checksum_failure():
    gtin = normalize_barcode("4006381333931")
    assert (gtin.symbology, gtin.gtin13) == ("EAN13", "4006381333931")
    with pytest.raises(BarcodeError, match="barcode_checksum"):
        normalize_barcode("4006381333932")


def test_leading_zeros_are_preserved():
    gtin = normalize_barcode("036000291452")  # UPC-A
    assert gtin.code == "036000291452"
    assert gtin.symbology == "UPCA"
    assert gtin.gtin13 == "0036000291452"
    assert gtin.off_code == "0036000291452"


def test_ean8():
    gtin = normalize_barcode("96385074")
    assert (gtin.symbology, gtin.gtin13) == ("EAN8", "0000096385074")
    assert gtin.off_code == "96385074"


def test_upce_expansion():
    assert expand_upce("01234565") == "012345000065"
    gtin = normalize_barcode("01234565", "UPC-E")
    assert gtin.symbology == "UPCE"
    assert gtin.gtin13 == "0012345000065"
    with pytest.raises(BarcodeError, match="barcode_checksum"):
        expand_upce("01234566")


def test_decoder_expanded_upce_form_is_accepted():
    gtin = normalize_barcode("0012345000065", "UPCE")
    assert gtin.symbology == "UPCE" and gtin.gtin13 == "0012345000065"


@pytest.mark.parametrize("body", ["200123456789", "240000000001", "020000000001", "990000000001"])
def test_restricted_store_codes_rejected(body):
    with pytest.raises(BarcodeError, match="barcode_restricted"):
        normalize_barcode(_with_check(body))


def test_restricted_ean8_rejected():
    with pytest.raises(BarcodeError, match="barcode_restricted"):
        normalize_barcode(_with_check("2123456"))


@pytest.mark.parametrize("raw", ["", "abc", "12345", "123456789012345"])
def test_invalid_input(raw):
    with pytest.raises(BarcodeError):
        normalize_barcode(raw)


def test_off_normalization_rules():
    assert off_normalize("0000096385074") == "96385074"
    assert off_normalize("034000470693") == "0034000470693"
    assert off_normalize("4006381333931") == "4006381333931"


def _barcode_png(codes: list[tuple[str, str]], blur: float = 0) -> bytes:
    zxingcpp = pytest.importorskip("zxingcpp")
    from PIL import Image, ImageFilter

    images = []
    for text, fmt in codes:
        barcode = zxingcpp.create_barcode(text, getattr(zxingcpp.BarcodeFormat, fmt))
        view = memoryview(barcode.to_image(scale=3))
        images.append(Image.frombytes("L", (view.shape[1], view.shape[0]), bytes(view)))
    width = sum(i.width for i in images) + 80 * (len(images) + 1)
    height = max(i.height for i in images) + 160
    canvas = Image.new("L", (width, height), 255)
    x = 80
    for image in images:
        canvas.paste(image, (x, 80))
        x += image.width + 80
    if blur:
        canvas = canvas.filter(ImageFilter.GaussianBlur(blur))
    out = io.BytesIO()
    canvas.save(out, format="PNG")
    return out.getvalue()


@pytest.mark.asyncio
async def test_decode_single_and_multiple_barcodes():
    single = await decode_barcodes(_barcode_png([("4006381333931", "EAN13")]))
    assert [d.gtin.gtin13 for d in single] == ["4006381333931"]

    multi = await decode_barcodes(_barcode_png([("4006381333931", "EAN13"), ("96385074", "EAN8")]))
    assert sorted(d.gtin.code for d in multi) == ["4006381333931", "96385074"]


@pytest.mark.asyncio
async def test_decode_restricted_code_is_reported_not_resolved():
    code = _with_check("200123456789")
    decoded = await decode_barcodes(_barcode_png([(code, "EAN13")]))
    assert decoded and decoded[0].gtin is None and decoded[0].error == "barcode_restricted"


@pytest.mark.asyncio
async def test_blurred_image_yields_nothing_and_garbage_raises():
    assert await decode_barcodes(_barcode_png([("4006381333931", "EAN13")], blur=12)) == []
    pytest.importorskip("zxingcpp")
    with pytest.raises(BarcodeError, match="image_unreadable"):
        await decode_barcodes(b"not an image")
