"""Open Food Facts v3.4 adapter (fixture recorded from the live API, 2026-09-26)."""
import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.food_nutrition import calculate_portion
from app.services.open_food_facts import OffProduct, lookup_barcode, parse_product

FIXTURE = Path(__file__).parent / "fixtures" / "off_v3_4_product_nutella.json"


def test_parse_recorded_v3_4_product():
    data = json.loads(FIXTURE.read_text())
    product = parse_product(data["product"])
    assert product.code == "3017620422003"
    assert product.brand == "Nutella"
    assert product.revision == "996"
    assert not product.liquid
    assert product.basis.energy_kcal == Decimal(539)
    assert calculate_portion(product.basis, 15).energy_kcal == Decimal("80.9")
    assert product.basis.fiber_g == Decimal(0)  # a real 0 stays 0


def test_kj_only_and_missing_energy():
    kj = parse_product({"code": "1", "nutriments": {"energy_100g": 1000, "proteins_100g": 3}})
    assert kj.basis.energy_kcal.quantize(Decimal("0.1")) == Decimal("239.0")
    none = parse_product({"code": "2", "nutriments": {"proteins_100g": 3}})
    assert none.basis is None  # no energy → no usable nutrition, never 0 kcal


def test_liquid_is_per_100ml_not_grams():
    drink = parse_product({"code": "3", "quantity": "0,5 l", "nutriments": {"energy-kcal_100g": 42}})
    assert drink.liquid and not drink.basis.mass_based


def test_payload_roundtrip():
    product = parse_product(json.loads(FIXTURE.read_text())["product"])
    again = OffProduct.from_payload(product.to_payload())
    assert again.basis.energy_kcal == product.basis.energy_kcal
    assert again.basis.grams_per_basis == Decimal(100)


class _Pool:
    def __init__(self):
        self.puts = []

    async def fetchrow(self, query, *args):
        return None

    async def execute(self, query, *args):
        self.puts.append(args)


def _client(resp):
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(return_value=resp)
    return client


@pytest.mark.asyncio
async def test_lookup_uses_pinned_version_user_agent_and_caches(mock_settings):
    body = json.loads(FIXTURE.read_text())
    resp = MagicMock(status_code=200, json=MagicMock(return_value=body), raise_for_status=MagicMock())
    pool = _Pool()
    with patch("app.services.open_food_facts.httpx.AsyncClient", return_value=_client(resp)) as cls:
        product = await lookup_barcode(pool, "3017620422003")
    assert product.name == "Nutella"
    url = cls.return_value.get.await_args.args[0]
    assert "/api/v3.4/product/3017620422003" in url
    assert "User-Agent" in cls.call_args.kwargs["headers"]
    assert pool.puts and pool.puts[0][1] == "hit"


@pytest.mark.asyncio
async def test_not_found_is_negative_cached(mock_settings):
    resp = MagicMock(status_code=404)
    pool = _Pool()
    with patch("app.services.open_food_facts.httpx.AsyncClient", return_value=_client(resp)):
        assert await lookup_barcode(pool, "4006381333931") is None
    assert pool.puts[0][1] == "miss"
