"""Open Food Facts barcode adapter (plan §3, §5.B).

- Pinned API version (``OFF_API_VERSION``, default 3.4): API 3.5+ replaces
  the ``nutriments`` object with a new structure that upstream still marks
  as under development, so the adapter is bound to the documented legacy
  fields of 3.4 and to fixtures in ``tests/fixtures/off_*.json``.
- Shared process-wide limiter (≤ OFF_READS_PER_MINUTE), custom User-Agent,
  cache (hits 7 days, misses 1 hour) in ``external_lookup_cache``.
- Provenance: products keep ``provider='off'`` and the OFF revision; data is
  ODbL — the bot shows "Open Food Facts" as the source.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.config import settings
from app.services.food_nutrition import NutritionBasis, NutritionError, make_basis, to_decimal

logger = logging.getLogger(__name__)

HIT_TTL_SECONDS = 7 * 24 * 3600
MISS_TTL_SECONDS = 3600
FIELDS = (
    "code,product_name,product_name_uk,product_name_en,brands,quantity,"
    "product_quantity,product_quantity_unit,serving_size,serving_quantity,"
    "nutrition_data_per,nutriments,rev,lang"
)


class _Limiter:
    """Sliding-window limiter shared by all coroutines in the process."""

    def __init__(self) -> None:
        self._stamps: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self, per_minute: int) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._stamps = [s for s in self._stamps if now - s < 60]
                if len(self._stamps) < max(1, per_minute):
                    self._stamps.append(now)
                    return
                await asyncio.sleep(60 - (now - self._stamps[0]) + 0.05)


_limiter = _Limiter()


@dataclass(frozen=True)
class OffProduct:
    code: str
    name: Optional[str]
    brand: Optional[str]
    basis: Optional[NutritionBasis]
    revision: Optional[str]
    liquid: bool
    raw_basis_unit: str

    def to_payload(self) -> dict:
        b = self.basis
        return {
            "code": self.code, "name": self.name, "brand": self.brand, "revision": self.revision,
            "liquid": self.liquid, "raw_basis_unit": self.raw_basis_unit,
            "basis": None if b is None else {
                "basis_quantity": str(b.basis_quantity), "basis_unit": b.basis_unit,
                "grams_per_basis": str(b.grams_per_basis) if b.grams_per_basis is not None else None,
                **{k: (str(v) if v is not None else None) for k, v in b.nutrients().items()},
            },
        }

    @classmethod
    def from_payload(cls, data: dict) -> "OffProduct":
        b = data.get("basis")
        basis = None
        if b:
            basis = NutritionBasis(
                basis_quantity=to_decimal(b["basis_quantity"]),
                basis_unit=b["basis_unit"],
                grams_per_basis=to_decimal(b.get("grams_per_basis")),
                **{k: to_decimal(b.get(k)) for k in (
                    "energy_kcal", "protein_g", "fat_g", "carbs_g", "fiber_g", "sugar_g", "salt_g")},
            )
        return cls(data["code"], data.get("name"), data.get("brand"), basis,
                   data.get("revision"), bool(data.get("liquid")), data.get("raw_basis_unit") or "g")


_LIQUID_RE = re.compile(r"\d\s*(ml|cl|dl|l)\b", re.IGNORECASE)


def parse_product(product: dict) -> OffProduct:
    """Normalize an API 3.4 product object. Missing nutrients stay None."""
    n = product.get("nutriments") or {}
    unit = str(product.get("product_quantity_unit") or "").lower()
    liquid = unit == "ml" or bool(_LIQUID_RE.search(str(product.get("quantity") or "")))
    name = (
        product.get("product_name_uk") or product.get("product_name")
        or product.get("product_name_en") or None
    )
    brand = (str(product.get("brands") or "").split(",")[0].strip() or None)
    basis = None
    try:
        kcal = n.get("energy-kcal_100g")
        kj = n.get("energy-kj_100g")
        if kcal is None and kj is None and n.get("energy_100g") is not None:
            # `energy_100g` is always kJ in the legacy structure.
            kj = n.get("energy_100g")
        basis = make_basis(
            basis_quantity=100,
            basis_unit="ml" if liquid else "g",
            energy_kcal=kcal,
            energy_kj=kj,
            protein_g=n.get("proteins_100g"),
            fat_g=n.get("fat_100g"),
            carbs_g=n.get("carbohydrates_100g"),
            fiber_g=n.get("fiber_100g"),
            sugar_g=n.get("sugars_100g"),
            salt_g=n.get("salt_100g"),
        )
        if not basis.has_energy:
            basis = None
    except NutritionError:
        basis = None
    return OffProduct(
        code=str(product.get("code") or ""),
        name=str(name).strip()[:255] if name else None,
        brand=brand[:255] if brand else None,
        basis=basis,
        revision=str(product.get("rev")) if product.get("rev") is not None else None,
        liquid=liquid,
        raw_basis_unit="ml" if liquid else "g",
    )


async def _cache_get(pool: Any, code: str) -> tuple[bool, Optional[OffProduct]]:
    try:
        row = await pool.fetchrow(
            """SELECT status, payload FROM external_lookup_cache
               WHERE provider = 'off' AND lookup_key = $1 AND expires_at > NOW()""",
            code,
        )
    except Exception:
        return False, None
    if row is None:
        return False, None
    if row["status"] == "miss":
        return True, None
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return True, OffProduct.from_payload(payload)


async def _cache_put(pool: Any, code: str, product: Optional[OffProduct]) -> None:
    try:
        await pool.execute(
            """INSERT INTO external_lookup_cache (provider, lookup_key, status, payload, expires_at)
               VALUES ('off', $1, $2, $3::jsonb, NOW() + make_interval(secs => $4))
               ON CONFLICT (provider, lookup_key) DO UPDATE
                   SET status = EXCLUDED.status, payload = EXCLUDED.payload,
                       expires_at = EXCLUDED.expires_at, created_at = NOW()""",
            code, "hit" if product else "miss",
            json.dumps(product.to_payload()) if product else None,
            HIT_TTL_SECONDS if product else MISS_TTL_SECONDS,
        )
    except Exception:
        logger.debug("OFF cache write failed", exc_info=True)


async def lookup_barcode(pool: Any, off_code: str) -> Optional[OffProduct]:
    """Product for an OFF-normalized code, or None (not found / no data).

    Transient errors raise ``httpx.HTTPError`` so the caller can say
    "try again later" instead of "unknown product".
    """
    cached, product = await _cache_get(pool, off_code)
    if cached:
        return product
    await _limiter.acquire(settings.off_reads_per_minute)
    url = f"{settings.off_base_url.rstrip('/')}/api/v{settings.off_api_version}/product/{off_code}"
    async with httpx.AsyncClient(
        timeout=settings.http_timeout_seconds,
        headers={"User-Agent": settings.off_user_agent, "Accept": "application/json"},
    ) as client:
        resp = await client.get(url, params={"fields": FIELDS})
    if resp.status_code == 404:
        await _cache_put(pool, off_code, None)
        return None
    if resp.status_code == 429:
        raise httpx.HTTPStatusError("rate limited", request=resp.request, response=resp)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") not in ("success", "success_with_warnings", 1) or not data.get("product"):
        await _cache_put(pool, off_code, None)
        return None
    product = parse_product({**data["product"], "code": data.get("code") or off_code})
    await _cache_put(pool, off_code, product)
    return product


async def upsert_off_product(conn: Any, product: OffProduct, *, barcode: str, symbology: str) -> int:
    """Shared OFF product + durable nutrition revision (only when changed)."""
    from app.services.food_catalog import add_nutrition_revision

    product_id = await conn.fetchval(
        """INSERT INTO food_products (provider, external_id, barcode, barcode_symbology, name, brand,
                                      preparation)
           VALUES ('off', $1, $2, $3, $4, $5, 'as_sold')
           ON CONFLICT (provider, external_id)
               WHERE owner_user_id IS NULL AND external_id IS NOT NULL
           DO UPDATE SET name = COALESCE(EXCLUDED.name, food_products.name),
                         brand = COALESCE(EXCLUDED.brand, food_products.brand),
                         barcode = COALESCE(food_products.barcode, EXCLUDED.barcode)
           RETURNING id""",
        product.code, barcode, symbology, product.name or f"EAN {barcode}", product.brand,
    )
    if product.basis is not None:
        current_rev = await conn.fetchval(
            """SELECT source_revision FROM food_nutrition_versions
               WHERE product_id = $1 AND owner_user_id IS NULL AND is_current AND source = 'off'""",
            product_id,
        )
        if current_rev is None or current_rev != product.revision:
            await add_nutrition_revision(
                conn, product_id, product.basis, source="off", owner_user_id=None,
                created_by_user_id=None, source_revision=product.revision or "unknown",
            )
    return product_id
