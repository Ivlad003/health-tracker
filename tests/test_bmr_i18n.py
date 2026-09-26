import re
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def test_mifflin_st_jeor(mock_settings):
    from app.services.bmr import compute_bmr, mifflin_st_jeor

    # 10*80 + 6.25*180 - 5*36 + 5 = 1750
    assert mifflin_st_jeor(weight_kg=80, height_cm=180, age=36, sex="male") == 1750
    # 10*60 + 6.25*165 - 5*30 - 161 = 1320.25
    assert mifflin_st_jeor(weight_kg=60, height_cm=165, age=30, sex="female") == 1320
    assert compute_bmr(birth_year=1990, sex="male", height_cm=180, weight_kg=80,
                       today=date(2026, 9, 1)) == 1750
    assert compute_bmr(birth_year=1990, sex="male", height_cm=180, weight_kg=None) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1990 m 180", {"birth_year": 1990, "sex": "male", "height_cm": 180.0}),
        ("ж 1995 165см", {"birth_year": 1995, "sex": "female", "height_cm": 165.0}),
        ("1990 x 180", None),
        ("1990 m", None),
        ("1800 m 180", None),
        ("1990 m 20", None),
    ],
)
def test_parse_profile_args(mock_settings, text, expected):
    from app.services.bmr import parse_profile_args

    assert parse_profile_args(text) == expected


def test_prorated_bmr(mock_settings):
    from app.services.bmr import prorated_bmr

    assert prorated_bmr(2400, datetime(2026, 9, 20, 12, 0)) == 1200
    assert prorated_bmr(2400, datetime(2026, 9, 20, 0, 0)) == 0


@pytest.mark.asyncio
async def test_get_today_stats_adds_prorated_bmr_to_apple_active_energy(mock_settings):
    from tests.test_apple_health_stats import StatsPool, _kyiv_today, daily_row

    from app.services import ai_assistant

    class ProfilePool(StatsPool):
        async def fetchrow(self, query, *args):
            if "fatsecret_access_token" in query:
                return {"fatsecret_access_token": None, "fatsecret_access_secret": None,
                        "timezone": "Europe/Kyiv", "birth_year": 1990, "sex": "male",
                        "height_cm": Decimal("180")}
            return await super().fetchrow(query, *args)

    pool = ProfilePool(apple_rows=[daily_row(_kyiv_today(), active_energy_kcal=300,
                                             records_by_type={"active_energy": 1})])
    pool.latest_body_mass = {"metric_date": date(2026, 9, 1), "average_value": Decimal("80")}

    with (
        patch.object(ai_assistant, "get_pool", AsyncMock(return_value=pool)),
        patch("app.services.bmr.prorated_bmr", return_value=875),
    ):
        stats = await ai_assistant.get_today_stats(7)

    assert stats["bmr_kcal"] is not None and 1700 <= stats["bmr_kcal"] <= 1760
    assert stats["calories_burned_source"] == "apple_health_bmr"
    assert stats["today_calories_out"] == 300 + 875


def test_catalogs_have_same_keys_and_placeholders(mock_settings):
    from app.i18n import MESSAGES

    uk, en = MESSAGES["uk"], MESSAGES["en"]
    assert set(uk) == set(en)
    for key in uk:
        assert set(re.findall(r"{(\w+)}", uk[key])) == set(re.findall(r"{(\w+)}", en[key])), key


@pytest.mark.parametrize(
    ("code", "expected"),
    [(None, "uk"), ("uk", "uk"), ("en-US", "en"), ("ru", "uk"), ("pl", "en"), ("EN", "en")],
)
def test_normalize_language(mock_settings, code, expected):
    from app.i18n import normalize_language

    assert normalize_language(code) == expected


def test_t_formats_and_falls_back(mock_settings):
    from app.i18n import t

    assert t("tz_set", "en", tz="UTC").startswith("✅ Timezone updated: UTC")
    assert t("tz_set", "de", tz="UTC").startswith("✅ Timezone updated")  # de -> en
    assert t("missing_key", "en") == "missing_key"


def _update(text="", language_code=None, uid=555):
    message = AsyncMock()
    message.text = text
    return SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=uid, username="u", language_code=language_code),
    )


@pytest.mark.asyncio
async def test_help_uses_telegram_language_for_unknown_users(mock_settings):
    from app.services import telegram_bot

    telegram_bot._language_cache.clear()
    update = _update(language_code="en")
    await telegram_bot.handle_help(update, None)
    assert update.message.reply_text.call_args.args[0].startswith("👋 Hi!")

    update = _update(language_code="uk", uid=556)
    await telegram_bot.handle_help(update, None)
    assert update.message.reply_text.call_args.args[0].startswith("👋 Привіт!")


@pytest.mark.asyncio
async def test_language_command_persists_and_switches(mock_settings):
    from app.services import telegram_bot

    telegram_bot._language_cache.clear()
    pool = AsyncMock()
    update = _update("/language en", language_code="uk")
    with (
        patch.object(telegram_bot, "_ensure_user", AsyncMock(return_value={"id": 7})),
        patch.object(telegram_bot, "get_pool", AsyncMock(return_value=pool)),
    ):
        await telegram_bot.handle_language(update, None)
        assert pool.execute.await_args.args[1:] == ("en", 7)
        assert update.message.reply_text.call_args.args[0] == "✅ Language switched to English."

        help_update = _update(language_code="uk")
        await telegram_bot.handle_help(help_update, None)
        assert help_update.message.reply_text.call_args.args[0].startswith("👋 Hi!")

        bad = _update("/language de")
        await telegram_bot.handle_language(bad, None)
        assert "Supported" in bad.message.reply_text.call_args.args[0]
    telegram_bot._language_cache.clear()


@pytest.mark.asyncio
async def test_profile_command_saves_and_reports_bmr(mock_settings):
    from app.services import telegram_bot

    telegram_bot._language_cache.clear()
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={"birth_year": 1990, "sex": "male",
                                            "height_cm": Decimal("180")})
    update = _update("/profile 1990 m 180", language_code="en")
    with (
        patch.object(telegram_bot, "_ensure_user", AsyncMock(return_value={"id": 7})),
        patch.object(telegram_bot, "get_pool", AsyncMock(return_value=pool)),
        patch("app.services.apple_health.get_latest_body_mass",
              AsyncMock(return_value={"metric_date": date(2026, 9, 1), "kg": 80.0})),
    ):
        await telegram_bot.handle_profile(update, None)

    assert pool.execute.await_args.args[1:] == (1990, "male", 180.0, 7)
    reply = update.message.reply_text.call_args.args[0]
    assert reply.startswith("✅ Profile saved. BMR:") and "kcal/day" in reply

    bad = _update("/profile hello", language_code="en")
    with (
        patch.object(telegram_bot, "_ensure_user", AsyncMock(return_value={"id": 7})),
        patch.object(telegram_bot, "get_pool", AsyncMock(return_value=pool)),
    ):
        await telegram_bot.handle_profile(bad, None)
    assert bad.message.reply_text.call_args.args[0].startswith("❌ Format")


def test_ingest_summary_is_localized(mock_settings):
    from app.routers.apple_health import _format_ingest_summary

    result = {"records_received": 2, "records_aggregated": 2, "records_by_type": {"step_count": 2},
              "workouts": {"workouts_received": 1, "workouts_inserted": 1, "workouts_updated": 0}}
    en = _format_ingest_summary(result, "en")
    uk = _format_ingest_summary(result, "uk")
    assert en.startswith("📊 Apple Health synced") and "2 step count" in en and "Workouts: 1" in en
    assert uk.startswith("📊 Apple Health синхронізовано") and "2 кроки" in uk
