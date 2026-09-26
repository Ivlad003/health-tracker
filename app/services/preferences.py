"""Typed, versioned user preferences shared by the bot and the Web App.

Only the keys declared in :class:`FoodPreferences` are accepted (no arbitrary
JSON). Canonical columns on ``users`` (language, timezone,
daily_calorie_goal, journal_*, profile) remain authoritative and are edited
through their own endpoints.
"""
from __future__ import annotations

import json
import re
from datetime import date
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class FoodPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Recording policy (plan §15): auto-record only exact confirmed matches,
    # or review every entry.
    recording_policy: Literal["auto_confirmed", "review_all"] = "auto_confirmed"
    catalog_auto_add: bool = True
    fatsecret_export: bool = True
    gram_presets: list[int] = Field(default_factory=lambda: [50, 100, 150, 200, 250])
    # FatSecret history → My Products
    history_import_days: int = Field(default=30, ge=1, le=365)
    history_import_mode: Literal["auto", "selective"] = "auto"
    history_daily_refresh: bool = True
    # Notifications (user-local times)
    briefing_morning_enabled: bool = True
    briefing_morning_time: str = "08:00"
    briefing_evening_enabled: bool = True
    briefing_evening_time: str = "21:00"
    sync_error_notices: bool = True

    @field_validator("gram_presets")
    @classmethod
    def _presets(cls, value: list[int]) -> list[int]:
        if len(value) > 8 or any(not 1 <= v <= 2000 for v in value):
            raise ValueError("gram presets: up to 8 values between 1 and 2000")
        return sorted(set(value))

    @field_validator("briefing_morning_time", "briefing_evening_time")
    @classmethod
    def _time(cls, value: str) -> str:
        if not _TIME_RE.match(value):
            raise ValueError("time must be HH:MM")
        return value


class PreferencesError(ValueError):
    def __init__(self, code: str, details: Any = None, current_version: Optional[int] = None):
        super().__init__(code)
        self.code = code
        self.details = details
        self.current_version = current_version


def _merge(stored: Any) -> FoodPreferences:
    if isinstance(stored, str):
        stored = json.loads(stored or "{}")
    data = {k: v for k, v in (stored or {}).items() if k in FoodPreferences.model_fields}
    try:
        return FoodPreferences(**data)
    except ValidationError:
        # A stored value that no longer validates falls back to defaults
        # field-by-field instead of breaking the bot.
        clean = {}
        for key, value in data.items():
            try:
                FoodPreferences(**{key: value})
                clean[key] = value
            except ValidationError:
                continue
        return FoodPreferences(**clean)


async def get_preferences(conn: Any, user_id: int) -> tuple[FoodPreferences, int]:
    try:
        row = await conn.fetchrow(
            "SELECT prefs, version FROM user_preferences WHERE user_id = $1", user_id,
        )
    except Exception:
        return FoodPreferences(), 0
    if row is None:
        return FoodPreferences(), 0
    return _merge(row["prefs"]), row["version"]


async def update_preferences(
    conn: Any, user_id: int, changes: dict, expected_version: int,
) -> tuple[FoodPreferences, int]:
    current, version = await get_preferences(conn, user_id)
    if version != expected_version:
        raise PreferencesError("version_conflict", current_version=version)
    unknown = set(changes) - set(FoodPreferences.model_fields)
    if unknown:
        raise PreferencesError("unknown_fields", sorted(unknown))
    try:
        merged = FoodPreferences(**{**current.model_dump(), **changes})
    except ValidationError as exc:
        raise PreferencesError(
            "validation_error",
            [{"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]} for e in exc.errors()],
        ) from exc
    payload = json.dumps(merged.model_dump())
    if version == 0:
        row = await conn.fetchrow(
            """INSERT INTO user_preferences (user_id, prefs, version) VALUES ($1, $2::jsonb, 1)
               ON CONFLICT (user_id) DO NOTHING RETURNING version""",
            user_id, payload,
        )
    else:
        row = await conn.fetchrow(
            """UPDATE user_preferences SET prefs = $2::jsonb, version = version + 1, updated_at = NOW()
               WHERE user_id = $1 AND version = $3 RETURNING version""",
            user_id, payload, expected_version,
        )
    if row is None:
        _, now_version = await get_preferences(conn, user_id)
        raise PreferencesError("version_conflict", current_version=now_version)
    return merged, row["version"]


# ---------------------------------------------------------------------------
# Date-effective goals
# ---------------------------------------------------------------------------

async def set_goal(
    conn: Any,
    user_id: int,
    *,
    calories: int,
    effective_date: date,
    protein_g: Optional[float] = None,
    fat_g: Optional[float] = None,
    carbs_g: Optional[float] = None,
) -> dict:
    if not 500 <= int(calories) <= 10000:
        raise PreferencesError("validation_error", [{"field": "calories", "message": "500..10000"}])
    row = await conn.fetchrow(
        """INSERT INTO user_goal_history (user_id, effective_date, calories, protein_g, fat_g, carbs_g)
           VALUES ($1, $2, $3, $4, $5, $6)
           ON CONFLICT (user_id, effective_date) DO UPDATE
               SET calories = EXCLUDED.calories, protein_g = EXCLUDED.protein_g,
                   fat_g = EXCLUDED.fat_g, carbs_g = EXCLUDED.carbs_g
           RETURNING effective_date, calories, protein_g, fat_g, carbs_g""",
        user_id, effective_date, int(calories), protein_g, fat_g, carbs_g,
    )
    # The canonical "current goal" column follows the newest effective goal.
    await conn.execute(
        """UPDATE users SET daily_calorie_goal = (
               SELECT calories FROM user_goal_history
               WHERE user_id = $1 AND effective_date <= CURRENT_DATE
               ORDER BY effective_date DESC LIMIT 1)
           WHERE id = $1
             AND EXISTS (SELECT 1 FROM user_goal_history
                         WHERE user_id = $1 AND effective_date <= CURRENT_DATE)""",
        user_id,
    )
    return dict(row)


async def goal_for_date(conn: Any, user_id: int, local_date: date) -> Optional[dict]:
    row = await conn.fetchrow(
        """SELECT effective_date, calories, protein_g, fat_g, carbs_g FROM user_goal_history
           WHERE user_id = $1 AND effective_date <= $2
           ORDER BY effective_date DESC LIMIT 1""",
        user_id, local_date,
    )
    if row:
        return dict(row)
    value = await conn.fetchval("SELECT daily_calorie_goal FROM users WHERE id = $1", user_id)
    return {"effective_date": None, "calories": value or 2000,
            "protein_g": None, "fat_g": None, "carbs_g": None}
