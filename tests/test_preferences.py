"""Typed preferences (plan FR-18, §15)."""
import pytest

from app.services.preferences import FoodPreferences, PreferencesError, _merge, update_preferences


def test_defaults():
    prefs = FoodPreferences()
    assert prefs.recording_policy == "auto_confirmed"
    assert prefs.catalog_auto_add and prefs.fatsecret_export
    assert prefs.history_import_days == 30


def test_stored_invalid_values_fall_back_per_field():
    prefs = _merge({"history_import_days": 9999, "recording_policy": "review_all", "junk": 1})
    assert prefs.history_import_days == 30
    assert prefs.recording_policy == "review_all"


class _Conn:
    def __init__(self, version=0, prefs=None):
        self.version = version
        self.prefs = prefs or {}

    async def fetchrow(self, query, *args):
        if query.lstrip().startswith("SELECT"):
            return {"prefs": self.prefs, "version": self.version} if self.version else None
        if "INSERT" in query:
            self.version = 1
            return {"version": 1}
        if args[2] != self.version:
            return None
        self.version += 1
        return {"version": self.version}


@pytest.mark.asyncio
async def test_update_validates_and_versions():
    conn = _Conn()
    prefs, version = await update_preferences(conn, 1, {"recording_policy": "review_all"}, 0)
    assert prefs.recording_policy == "review_all" and version == 1
    with pytest.raises(PreferencesError) as exc:
        await update_preferences(conn, 1, {"catalog_auto_add": False}, 0)
    assert exc.value.code == "version_conflict"
    with pytest.raises(PreferencesError) as exc:
        await update_preferences(conn, 1, {"nope": 1}, 1)
    assert exc.value.code == "unknown_fields"
    with pytest.raises(PreferencesError) as exc:
        await update_preferences(conn, 1, {"briefing_morning_time": "25:00"}, 1)
    assert exc.value.code == "validation_error"
