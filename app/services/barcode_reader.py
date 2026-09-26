"""Retail barcode decoding and GTIN normalization (plan §5.B, AC-05).

Barcodes are strings: leading zeros and the original symbology are kept.
Only EAN-13, EAN-8, UPC-A and UPC-E (expanded correctly) are supported.
Restricted-circulation codes (in-store / variable weight / coupons) are
reported as unsupported instead of guessing a product or a weight.
Provider-specific forms (FatSecret GTIN-13, Open Food Facts normalization)
are derived explicitly.
"""
from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

SUPPORTED_SYMBOLOGIES = ("EAN13", "EAN8", "UPCA", "UPCE")
_RESTRICTED_EAN13_PREFIXES = (
    tuple(f"0{d}" for d in "2 4 5".split())  # UPC-A 2/4/5 → in-store, coupons
    + tuple(str(p) for p in range(20, 30))    # 200–299 restricted circulation
    + ("98", "99")                            # coupons
)
_RESTRICTED_EAN8_PREFIXES = ("0", "2")        # RCN-8


class BarcodeError(ValueError):
    """Invalid/unsupported barcode (message is an i18n-friendly code)."""


def gtin_check_digit(body: str) -> str:
    """GS1 mod-10 check digit for the digits *before* the check digit."""
    if not body.isdigit():
        raise BarcodeError("barcode_not_digits")
    total = 0
    for index, char in enumerate(reversed(body)):
        total += int(char) * (3 if index % 2 == 0 else 1)
    return str((10 - total % 10) % 10)


def has_valid_check_digit(code: str) -> bool:
    return len(code) >= 2 and code.isdigit() and gtin_check_digit(code[:-1]) == code[-1]


def expand_upce(code: str) -> str:
    """UPC-E (8 digits: number system + 6 + check) → UPC-A (12 digits)."""
    if len(code) == 6:
        raise BarcodeError("upce_needs_number_system")
    if len(code) == 7:
        code = code + "?"
    if len(code) != 8 or not code[:7].isdigit():
        raise BarcodeError("upce_length")
    ns, d, check = code[0], code[1:7], code[7]
    if ns not in "01":
        raise BarcodeError("upce_number_system")
    last = d[5]
    if last in "012":
        body = f"{ns}{d[0]}{d[1]}{last}0000{d[2]}{d[3]}{d[4]}"
    elif last == "3":
        body = f"{ns}{d[0]}{d[1]}{d[2]}00000{d[3]}{d[4]}"
    elif last == "4":
        body = f"{ns}{d[0]}{d[1]}{d[2]}{d[3]}00000{d[4]}"
    else:
        body = f"{ns}{d[0]}{d[1]}{d[2]}{d[3]}{d[4]}0000{last}"
    computed = gtin_check_digit(body)
    if check != "?" and check != computed:
        raise BarcodeError("barcode_checksum")
    return body + computed


@dataclass(frozen=True)
class Gtin:
    """A validated retail code. ``code`` is the printed form, ``symbology``
    the original symbology; ``gtin13`` is derived (FatSecret needs it)."""

    code: str
    symbology: str
    gtin13: str

    @property
    def off_code(self) -> str:
        return off_normalize(self.code if self.symbology != "UPCE" else self.gtin13)


def normalize_barcode(raw: str, symbology: Optional[str] = None) -> Gtin:
    """Validate a code and derive GTIN-13. Raises ``BarcodeError``."""
    code = "".join(ch for ch in str(raw or "") if not ch.isspace() and ch != "-")
    if not code.isdigit():
        raise BarcodeError("barcode_not_digits")
    sym = (symbology or "").upper().replace("-", "").replace("_", "")
    if sym and sym not in SUPPORTED_SYMBOLOGIES:
        raise BarcodeError("barcode_symbology_unsupported")

    if sym == "UPCE" or (not sym and len(code) == 8 and code[0] in "01" and _looks_upce(code)):
        if len(code) in (7, 8):
            upca = expand_upce(code)
            gtin13 = "0" + upca
            _reject_restricted(gtin13)
            return Gtin(code=code, symbology="UPCE", gtin13=gtin13)
        # Some decoders emit the expanded form for UPC-E symbols.
        if len(code) == 12:
            return _finalize(code, "UPCE", "0" + code)
        if len(code) == 13 and code.startswith("0"):
            return _finalize(code, "UPCE", code)
        raise BarcodeError("barcode_length")

    if len(code) == 8:
        if not has_valid_check_digit(code):
            raise BarcodeError("barcode_checksum")
        if code[0] in _RESTRICTED_EAN8_PREFIXES:
            raise BarcodeError("barcode_restricted")
        return Gtin(code=code, symbology="EAN8", gtin13=code.zfill(13))
    if len(code) == 12:
        return _finalize(code, "UPCA", "0" + code)
    if len(code) == 13:
        return _finalize(code, "UPCA" if sym == "UPCA" else "EAN13", code)
    if len(code) == 14 and code.startswith("0"):
        return _finalize(code[1:], "EAN13", code[1:])
    raise BarcodeError("barcode_length")


def _looks_upce(code: str) -> bool:
    try:
        expand_upce(code)
        return not has_valid_check_digit(code)  # valid EAN-8 wins when ambiguous
    except BarcodeError:
        return False


def _finalize(code: str, symbology: str, gtin13: str) -> Gtin:
    if not has_valid_check_digit(gtin13):
        raise BarcodeError("barcode_checksum")
    _reject_restricted(gtin13)
    return Gtin(code=code, symbology=symbology, gtin13=gtin13)


def _reject_restricted(gtin13: str) -> None:
    if gtin13.startswith(_RESTRICTED_EAN13_PREFIXES):
        raise BarcodeError("barcode_restricted")


def off_normalize(code: str) -> str:
    """Open Food Facts normalization: strip leading zeros, then pad ≤7 digits
    to 8 and 9–12 digits to 13."""
    stripped = code.lstrip("0") or "0"
    if len(stripped) <= 7:
        return stripped.zfill(8)
    if 9 <= len(stripped) <= 12:
        return stripped.zfill(13)
    return stripped


# --------------------------------------------------------------------------
# Image decoding (zxing-cpp + Pillow), offloaded from the event loop
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class DecodedBarcode:
    text: str
    symbology: str
    gtin: Optional[Gtin]
    error: Optional[str]


def _decode_sync(image_bytes: bytes, max_pixels: int) -> list[DecodedBarcode]:
    import zxingcpp  # imported lazily: optional native dependency
    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = max_pixels
    with Image.open(io.BytesIO(image_bytes)) as img:
        img.load()
        if img.width * img.height > max_pixels:
            raise BarcodeError("image_too_large")
        # Keep full resolution for barcodes; only fix orientation.
        gray = ImageOps.exif_transpose(img).convert("L")
    formats = (
        zxingcpp.BarcodeFormat.EAN13,
        zxingcpp.BarcodeFormat.EAN8,
        zxingcpp.BarcodeFormat.UPCA,
        zxingcpp.BarcodeFormat.UPCE,
    )
    results = zxingcpp.read_barcodes(gray, formats=formats)
    decoded: dict[str, DecodedBarcode] = {}
    for item in results:
        if hasattr(item, "valid") and not item.valid:
            continue
        sym = str(getattr(item.format, "name", item.format)).upper().replace("-", "")
        text = str(item.text or "")
        try:
            gtin = normalize_barcode(text, sym)
            decoded.setdefault(gtin.gtin13, DecodedBarcode(text, sym, gtin, None))
        except BarcodeError as exc:
            decoded.setdefault(f"err:{text}", DecodedBarcode(text, sym, None, str(exc)))
    return list(decoded.values())


async def decode_barcodes(image_bytes: bytes, *, max_pixels: int = 40_000_000) -> list[DecodedBarcode]:
    """Decode retail barcodes from an image in a worker thread.

    Returns one entry per distinct code; entries with ``error`` are codes that
    were read but are invalid/restricted (never turned into a product).
    """
    try:
        return await asyncio.to_thread(_decode_sync, image_bytes, max_pixels)
    except BarcodeError:
        raise
    except ImportError:
        logger.warning("zxing-cpp/Pillow not installed; barcode decoding disabled")
        return []
    except Exception as exc:  # corrupt image, unsupported format
        logger.info("Barcode decode failed: %s", exc.__class__.__name__)
        raise BarcodeError("image_unreadable") from exc
