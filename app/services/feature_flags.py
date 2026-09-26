"""Runtime feature flags with a typed allowlist (plan FR-18).

A flag can only be switched on when the underlying capability exists
(configured credentials, installed native library, provider entitlement).
Service secrets stay deployment settings and are never editable here.
"""
from __future__ import annotations

import importlib.util
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from app.config import settings


@dataclass(frozen=True)
class FlagSpec:
    key: str
    description: str
    default: Callable[[], bool]
    available: Callable[[], tuple[bool, Optional[str]]]


def _always() -> tuple[bool, Optional[str]]:
    return True, None


def _zxing() -> tuple[bool, Optional[str]]:
    ok = importlib.util.find_spec("zxingcpp") is not None and importlib.util.find_spec("PIL") is not None
    return ok, None if ok else "zxing-cpp/Pillow not installed"


def _openai() -> tuple[bool, Optional[str]]:
    return (True, None) if settings.openai_api_key else (False, "OPENAI_API_KEY not set")


def _fs_barcode() -> tuple[bool, Optional[str]]:
    if not settings.fatsecret_barcode_enabled:
        return False, "FatSecret barcode add-on not enabled for this deployment"
    return True, None


FLAGS: dict[str, FlagSpec] = {
    spec.key: spec
    for spec in (
        FlagSpec("food_history", "History-first food matching and FatSecret catalog import",
                 lambda: settings.food_history_enabled, _always),
        FlagSpec("food_barcode", "Barcode photos (local decoding + Open Food Facts)",
                 lambda: settings.food_barcode_enabled, _zxing),
        FlagSpec("fatsecret_barcode", "FatSecret barcode lookup (Premier add-on)",
                 lambda: settings.fatsecret_barcode_enabled, _fs_barcode),
        FlagSpec("food_vision", "Nutrition-label / packaging photo extraction",
                 lambda: settings.food_vision_enabled, _openai),
        FlagSpec("food_plate_photos", "Plate photo recognition (P2)",
                 lambda: settings.food_plate_photos_enabled, _openai),
    )
}

_CACHE_TTL = 30.0
_cache: dict[str, tuple[bool, float]] = {}


class FlagError(ValueError):
    pass


def clear_cache() -> None:
    _cache.clear()


async def is_enabled(pool: Any, key: str) -> bool:
    spec = FLAGS[key]
    available, _ = spec.available()
    if not available:
        return False
    cached = _cache.get(key)
    if cached and cached[1] > time.monotonic():
        return cached[0]
    value = spec.default()
    try:
        row = await pool.fetchrow("SELECT enabled FROM feature_flags WHERE key = $1", key)
        if row is not None:
            value = bool(row["enabled"])
    except Exception:
        pass  # table missing in unit tests / transient DB error → default
    _cache[key] = (value, time.monotonic() + _CACHE_TTL)
    return value


async def enabled_map(pool: Any) -> dict[str, bool]:
    """Effective value of every flag with at most one query (fills the cache)."""
    now = time.monotonic()
    if not all(key in _cache and _cache[key][1] > now for key in FLAGS):
        try:
            stored = {r["key"]: bool(r["enabled"])
                      for r in await pool.fetch("SELECT key, enabled FROM feature_flags")}
        except Exception:
            stored = {}  # table missing in unit tests / transient DB error → defaults
        for key, spec in FLAGS.items():
            _cache[key] = (stored.get(key, spec.default()), now + _CACHE_TTL)
    return {key: spec.available()[0] and _cache[key][0] for key, spec in FLAGS.items()}


async def list_flags(pool: Any) -> list[dict]:
    rows = {r["key"]: r for r in await pool.fetch("SELECT key, enabled, version, updated_at FROM feature_flags")}
    out = []
    for key, spec in FLAGS.items():
        available, reason = spec.available()
        row = rows.get(key)
        out.append({
            "key": key,
            "description": spec.description,
            "enabled": bool(row["enabled"]) if row else spec.default(),
            "effective": available and (bool(row["enabled"]) if row else spec.default()),
            "available": available,
            "unavailable_reason": reason,
            "version": row["version"] if row else 0,
        })
    return out


async def set_flag(pool: Any, key: str, enabled: bool, *, actor_user_id: int, expected_version: int) -> dict:
    if key not in FLAGS:
        raise FlagError("unknown_flag")
    available, reason = FLAGS[key].available()
    if enabled and not available:
        raise FlagError(reason or "unavailable")
    row = await pool.fetchrow(
        """INSERT INTO feature_flags (key, enabled, version, updated_by_user_id)
           VALUES ($1, $2, 1, $3)
           ON CONFLICT (key) DO UPDATE
               SET enabled = EXCLUDED.enabled, version = feature_flags.version + 1,
                   updated_by_user_id = EXCLUDED.updated_by_user_id, updated_at = NOW()
               WHERE feature_flags.version = $4
           RETURNING key, enabled, version""",
        key, enabled, actor_user_id, expected_version,
    )
    if row is None:
        raise FlagError("version_conflict")
    _cache.pop(key, None)
    return dict(row)
