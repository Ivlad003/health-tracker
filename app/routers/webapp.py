"""Telegram Web App JSON API (`/api/v1/webapp/*`) — plan §13–16.

Every route resolves the caller from a server session created from verified
Telegram ``initData``; resource ids are always checked against the caller's
ownership/visibility. Mutations carry a ``version`` (HTTP 409 on conflict)
and web-created records an ``idempotency_key``. The routes call the same
services as the bot (catalog, resolver, ledger, sync).

Error shape (all 4xx): ``{"detail": {"error": "<code>", ...}}`` — request
validation errors use the same envelope (see ``app.main``).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import date, datetime, time as dt_time, timedelta, timezone
from decimal import Decimal
from typing import Annotated, Any, AsyncIterator, Literal, Optional
from urllib.parse import quote, urlencode, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.database import get_pool
from app.services import catalog_import, feature_flags
from app.services import food_catalog as catalog
from app.services import food_logging as ledger
from app.services import webapp_auth
from app.services.catalog_import import ImportError_
from app.services.food_catalog import CatalogError, VersionConflict
from app.services.food_logging import LedgerError
from app.services.food_nutrition import NutritionError
from app.services.preferences import PreferencesError, get_preferences, goal_for_date, set_goal, update_preferences

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/webapp", tags=["webapp"])

SESSION_COOKIE = "ht_session"
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
MAX_HISTORY_RANGE_DAYS = 31

# Shared field types (also used by the admin router).
MealType = Literal["breakfast", "lunch", "dinner", "snack"]
Preparation = Literal["raw", "cooked", "as_sold", "prepared", "unknown"]
IDEMPOTENCY_PATTERN = r"^[A-Za-z0-9_\-]+$"
IdempotencyKey = Annotated[str, Field(min_length=8, max_length=64, pattern=IDEMPOTENCY_PATTERN)]
HHMM = Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]

DOMAIN_ERRORS = (CatalogError, LedgerError, ImportError_, NutritionError, PreferencesError)


# ---------------------------------------------------------------------------
# Serialization / errors
# ---------------------------------------------------------------------------

def jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def ok(payload: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(content=jsonable(payload), status_code=status_code)


def error(status: int, code: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": code, **jsonable(extra)})


def translate(exc: Exception) -> HTTPException:
    if isinstance(exc, VersionConflict):
        return error(409, "version_conflict", current_version=exc.current)
    if isinstance(exc, PreferencesError):
        if exc.code == "version_conflict":
            return error(409, "version_conflict", current_version=exc.current_version)
        return error(422, exc.code, fields=exc.details)
    if isinstance(exc, (CatalogError, LedgerError, ImportError_, NutritionError)):
        code = str(exc.args[0]) if exc.args else "invalid"
        status = 404 if code.endswith("not_found") else 400
        return error(status, code)
    raise exc


@asynccontextmanager
async def transaction() -> AsyncIterator[Any]:
    """One pooled connection inside a transaction; domain errors → HTTP errors."""
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                yield conn
    except DOMAIN_ERRORS as exc:
        raise translate(exc) from exc


# ---------------------------------------------------------------------------
# Session dependency
# ---------------------------------------------------------------------------

def _origin_allowed(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True  # Telegram WebViews may omit Origin on same-origin requests
    expected = urlparse(settings.effective_webapp_url)
    got = urlparse(origin)
    if (got.scheme, got.netloc) == (expected.scheme, expected.netloc):
        return True
    logger.info("Web App cookie mutation from foreign origin %s (expected %s)", got.netloc, expected.netloc)
    return False


async def current_session(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_csrf_token: Optional[str] = Header(default=None),
) -> webapp_auth.WebSession:
    pool = await get_pool()
    token = None
    transport = "bearer"
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif request.cookies.get(SESSION_COOKIE):
        token = request.cookies[SESSION_COOKIE]
        transport = "cookie"
    session = await webapp_auth.resolve_session(pool, token or "")
    if session is None:
        raise error(401, "session_invalid")
    session.transport = transport
    if transport == "cookie" and request.method not in SAFE_METHODS:
        # Ambient credential → CSRF token + same-origin check on mutations.
        if not webapp_auth.csrf_matches(session, x_csrf_token) or not _origin_allowed(request):
            raise error(403, "csrf_failed")
    return session


Session = Annotated[webapp_auth.WebSession, Depends(current_session)]


async def _ctx(session: webapp_auth.WebSession) -> ledger.UserContext:
    pool = await get_pool()
    return await ledger.load_user_context(pool, session.user_id)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class AuthBody(BaseModel):
    init_data: str = Field(min_length=1, max_length=8192)
    # "bearer" (default): the token is returned in the body, no cookie.
    # "cookie": Secure/HttpOnly cookie + CSRF token; the body has no token.
    transport: Literal["bearer", "cookie"] = "bearer"


@router.post("/auth/telegram")
async def auth_telegram(body: AuthBody):
    try:
        verified = webapp_auth.validate_init_data(
            body.init_data, settings.telegram_bot_token,
            max_age_seconds=settings.webapp_auth_max_age_seconds,
        )
    except webapp_auth.InitDataError as exc:
        logger.info("Web App login rejected: %s", exc)
        raise error(401, str(exc))
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                user = await webapp_auth.ensure_user(conn, verified["user"])
                token, csrf, expires = await webapp_auth.create_session(
                    conn, user["id"], init_data_hash=verified["hash"],
                )
                admin = await webapp_auth.is_admin(conn, user["id"])
    except webapp_auth.InitDataError as exc:
        logger.info("Web App login rejected: %s", exc)
        raise error(401, str(exc))
    payload: dict[str, Any] = {
        "csrf_token": csrf,
        "expires_at": expires,
        "idle_timeout_seconds": settings.webapp_session_ttl_seconds,
        "user": {"id": user["id"], "language": user["language"], "timezone": user["timezone"],
                 "is_admin": admin},
    }
    if body.transport == "bearer":
        return ok({**payload, "session_token": token})
    result = ok(payload)
    # SameSite=None: Telegram Web (web.telegram.org) embeds the Mini App in a
    # cross-site iframe. CSRF token + Origin check guard cookie mutations.
    result.set_cookie(
        SESSION_COOKIE, token, max_age=int(webapp_auth.SESSION_MAX_LIFETIME.total_seconds()), httponly=True,
        secure=True, samesite="none", path="/api/v1/",
    )
    return result


@router.post("/auth/logout")
async def logout(session: Session):
    pool = await get_pool()
    await webapp_auth.revoke_session(pool, session.session_id)
    result = ok({"ok": True})
    result.delete_cookie(SESSION_COOKIE, path="/api/v1/")
    return result


# ---------------------------------------------------------------------------
# Profile, preferences, goals
# ---------------------------------------------------------------------------

_CONNECTED_SQL = """(fatsecret_access_token IS NOT NULL AND fatsecret_access_token <> '') AS fatsecret_connected,
                    (whoop_access_token IS NOT NULL AND whoop_access_token <> '') AS whoop_connected"""


@router.get("/me")
async def me(session: Session):
    pool = await get_pool()
    row = await pool.fetchrow(
        f"""SELECT id, telegram_user_id, language, timezone, daily_calorie_goal, birth_year, sex,
                   height_cm, journal_enabled, journal_time_1, journal_time_2, updated_at,
                   {_CONNECTED_SQL}
            FROM users WHERE id = $1""",
        session.user_id,
    )
    data = dict(row)
    data["profile_version"] = data.pop("updated_at").isoformat() if data.get("updated_at") else None
    for key in ("journal_time_1", "journal_time_2"):
        data[key] = data[key].strftime("%H:%M") if data.get(key) else None
    data["is_admin"] = session.is_admin
    data["features"] = await feature_flags.enabled_map(pool)
    return ok(data)


class ProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_version: Optional[str] = Field(default=None, max_length=64)
    language: Optional[Literal["uk", "en"]] = None
    timezone: Optional[str] = Field(default=None, max_length=64)
    birth_year: Optional[int] = Field(default=None, ge=1900, le=2100)
    sex: Optional[Literal["male", "female"]] = None
    height_cm: Optional[float] = Field(default=None, ge=50, le=260)
    journal_enabled: Optional[bool] = None
    journal_time_1: Optional[HHMM] = None
    journal_time_2: Optional[HHMM] = None


# Columns PATCH /me may write (the f-string below only ever sees these names).
_PROFILE_COLUMNS = frozenset(ProfilePatch.model_fields) - {"profile_version"}


def _same_version(current: Optional[datetime], supplied: Optional[str]) -> bool:
    if current is None:
        return True
    if not supplied:
        return False
    try:
        return datetime.fromisoformat(supplied) == current
    except ValueError:
        return False


@router.patch("/me")
async def patch_me(body: ProfilePatch, session: Session):
    changes = body.model_dump(exclude_unset=True, exclude={"profile_version"})
    if changes.get("timezone") is not None:
        try:
            ZoneInfo(changes["timezone"])
        except (ZoneInfoNotFoundError, ValueError):
            raise error(422, "validation_error", fields=[{"field": "timezone", "message": "unknown"}])
    for key in ("journal_time_1", "journal_time_2"):
        if changes.get(key):
            hh, mm = changes[key].split(":")
            changes[key] = dt_time(int(hh), int(mm))
    if not set(changes) <= _PROFILE_COLUMNS:  # extra="forbid" already guarantees this
        raise error(422, "validation_error")
    async with transaction() as conn:
        current = await conn.fetchrow(
            "SELECT updated_at FROM users WHERE id = $1 FOR UPDATE", session.user_id,
        )
        if not _same_version(current["updated_at"], body.profile_version):
            raise error(409, "version_conflict", current_version=current["updated_at"])
        if changes:
            sets = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(changes))
            await conn.execute(
                f"UPDATE users SET {sets}, updated_at = NOW() WHERE id = $1",
                session.user_id, *changes.values(),
            )
    if "language" in changes:
        # Bot and Web App share the setting: drop the bot's cached language.
        from app.services.telegram_bot import invalidate_language_cache

        invalidate_language_cache(session.telegram_user_id)
    return await me(session)


@router.get("/preferences")
async def get_prefs(session: Session):
    pool = await get_pool()
    prefs, version = await get_preferences(pool, session.user_id)
    return ok({"preferences": prefs.model_dump(), "version": version})


class PrefsBody(BaseModel):
    version: int = Field(ge=0)
    # Keys are allowlisted and typed by preferences.update_preferences.
    changes: dict[str, Any]


@router.put("/preferences")
async def put_prefs(body: PrefsBody, session: Session):
    pool = await get_pool()
    try:
        prefs, version = await update_preferences(pool, session.user_id, body.changes, body.version)
    except PreferencesError as exc:
        raise translate(exc)
    return ok({"preferences": prefs.model_dump(), "version": version})


@router.get("/goals")
async def get_goals(session: Session, on: Optional[date] = Query(default=None)):
    pool = await get_pool()
    ctx = await _ctx(session)
    goal = await goal_for_date(pool, session.user_id, on or ctx.today())
    history = await pool.fetch(
        """SELECT effective_date, calories, protein_g, fat_g, carbs_g FROM user_goal_history
           WHERE user_id = $1 ORDER BY effective_date DESC LIMIT 50""",
        session.user_id,
    )
    return ok({"current": goal, "history": [dict(r) for r in history]})


class GoalBody(BaseModel):
    calories: int = Field(ge=500, le=10000)
    protein_g: Optional[float] = Field(default=None, ge=0, le=1000)
    fat_g: Optional[float] = Field(default=None, ge=0, le=1000)
    carbs_g: Optional[float] = Field(default=None, ge=0, le=2000)
    effective_date: Optional[date] = None


@router.put("/goals")
async def put_goal(body: GoalBody, session: Session):
    ctx = await _ctx(session)
    effective = body.effective_date or ctx.today()
    async with transaction() as conn:
        row = await set_goal(
            conn, session.user_id, calories=body.calories, effective_date=effective,
            protein_g=body.protein_g, fat_g=body.fat_g, carbs_g=body.carbs_g,
        )
    return ok(row)


@router.get("/today")
async def today(session: Session):
    """Dashboard numbers: eaten calories, WHOOP, Apple Health, BMR.

    Same assembly the bot uses for briefings. WHOOP is fetched live.
    """
    from app.services.ai_assistant import get_today_stats  # heavy import (OpenAI client)

    return ok(await get_today_stats(session.user_id))


# ---------------------------------------------------------------------------
# Products (My Products) + default rules
# ---------------------------------------------------------------------------

class NutritionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    basis_quantity: Decimal = Field(default=Decimal(100), gt=0)
    basis_unit: Literal["g", "ml", "serving"] = "g"
    grams_per_basis: Optional[Decimal] = Field(default=None, gt=0)
    energy_kcal: Optional[Decimal] = Field(default=None, ge=0)
    energy_kj: Optional[Decimal] = Field(default=None, ge=0)
    protein_g: Optional[Decimal] = Field(default=None, ge=0)
    fat_g: Optional[Decimal] = Field(default=None, ge=0)
    carbs_g: Optional[Decimal] = Field(default=None, ge=0)
    fiber_g: Optional[Decimal] = Field(default=None, ge=0)
    sugar_g: Optional[Decimal] = Field(default=None, ge=0)
    salt_g: Optional[Decimal] = Field(default=None, ge=0)


class ProductCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    brand: Optional[str] = Field(default=None, max_length=255)
    preparation: Preparation = "unknown"
    barcode: Optional[str] = Field(default=None, max_length=14)
    nutrition: Optional[NutritionBody] = None
    usual_portion_g: Optional[Decimal] = Field(default=None, gt=0, le=5000)
    default_alias: Optional[str] = Field(default=None, max_length=255)


@router.get("/products")
async def products(
    session: Session,
    q: Optional[str] = Query(default=None, max_length=100),
    state: Literal["active", "excluded", "archived"] = Query(default="active"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    pool = await get_pool()
    items = await catalog.list_products(pool, session.user_id, query=q, state=state, limit=limit, offset=offset)
    return ok({"items": items, "limit": limit, "offset": offset})


@router.post("/products", status_code=201)
async def create_product(body: ProductCreate, session: Session):
    barcode = symbology = None
    if body.barcode:
        from app.services.barcode_reader import BarcodeError, normalize_barcode  # optional native deps

        try:
            gtin = normalize_barcode(body.barcode)
        except BarcodeError as exc:
            raise error(400, str(exc))
        barcode, symbology = gtin.code, gtin.symbology
    try:
        basis = catalog.basis_from_payload(body.nutrition.model_dump()) if body.nutrition else None
    except NutritionError as exc:
        raise translate(exc)
    async with transaction() as conn:
        created = await catalog.create_personal_product(
            conn, session.user_id, name=body.name, brand=body.brand,
            preparation=body.preparation, barcode=barcode, barcode_symbology=symbology,
            basis=basis, origin="manual", source="manual",
            usual_portion_g=body.usual_portion_g,
        )
        rule = None
        if body.default_alias:
            rule = await catalog.set_default_rule(
                conn, session.user_id, body.default_alias, created["product_id"],
                suggested_portion_g=body.usual_portion_g,
            )
    return ok({**created, "default_rule": rule}, status_code=201)


@router.get("/products/search")
async def product_search(session: Session, q: str = Query(min_length=2, max_length=100)):
    """Provider search for adding a product (nothing is stored until import)."""
    from app.services.food_resolver import FoodQuery, search_candidates

    pool = await get_pool()
    starters = await pool.fetch(
        """SELECT id, name, brand, preparation FROM food_products
           WHERE is_starter AND status = 'active' AND name ILIKE '%' || $1 || '%'
           ORDER BY name LIMIT 10""",
        q,
    )
    candidates = await search_candidates(FoodQuery(text=q, name_en=q), max_results=10)
    return ok({
        "starter": [dict(r) for r in starters],
        "items": [c.to_json() for c in candidates],
    })


class ImportProductBody(BaseModel):
    provider: Literal["fatsecret"]
    external_id: str = Field(pattern=r"^\d{1,20}$")
    display_name: Optional[str] = Field(default=None, max_length=255)


@router.post("/products/import", status_code=201)
async def import_product(body: ImportProductBody, session: Session):
    async with transaction() as conn:
        product_id = await catalog.upsert_fatsecret_product(conn, body.external_id)
        membership = await catalog.upsert_membership(
            conn, session.user_id, product_id, "web", display_name=body.display_name, explicit=True,
        )
    pool = await get_pool()
    ctx = await _ctx(session)
    try:
        await catalog.refresh_fatsecret_product(
            pool, product_id, body.external_id,
            access_token=ctx.fs_token, access_secret=ctx.fs_secret,
            language=ctx.language,
        )
    except Exception:
        logger.warning("FatSecret refresh failed for imported product", exc_info=True)
    return ok({"product_id": product_id, "membership": membership}, status_code=201)


@router.get("/products/{product_id}")
async def product_detail(product_id: int, session: Session):
    pool = await get_pool()
    product = await catalog.get_product(pool, product_id, session.user_id)
    if product is None:
        raise error(404, "not_found")
    nutrition = await catalog.current_nutrition(pool, product_id, session.user_id)
    rules = await pool.fetch(
        """SELECT id, alias_display, origin, enabled, serving_id, preparation, suggested_portion_g,
                  version FROM food_default_rules
           WHERE user_id = $1 AND product_id = $2 ORDER BY enabled DESC, id DESC""",
        session.user_id, product_id,
    )
    product.pop("owner_user_id", None)
    if not (product.get("provider_cached_until") and product["provider_cached_until"] > datetime.now(timezone.utc)):
        product["provider_name"] = product["provider_brand"] = None
    return ok({"product": product, "nutrition": nutrition, "rules": [dict(r) for r in rules]})


class ProductPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    display_name: Optional[str] = Field(default=None, max_length=255)
    preparation: Optional[Preparation] = None
    usual_portion_g: Optional[Decimal] = Field(default=None, gt=0, le=5000)
    preferred_serving_id: Optional[str] = Field(default=None, max_length=32)
    nutrition: Optional[NutritionBody] = None


@router.patch("/products/{product_id}")
async def patch_product(product_id: int, body: ProductPatch, session: Session):
    """Personal overrides; nutrition edits create a new personal revision
    (future meals only — past entries keep their revision)."""
    async with transaction() as conn:
        if not await catalog.product_visible(conn, session.user_id, product_id):
            raise CatalogError("not_found")
        result = await catalog.update_membership_fields(
            conn, session.user_id, product_id, body.version,
            display_name=body.display_name, preparation=body.preparation,
            usual_portion_g=body.usual_portion_g,
            preferred_serving_id=body.preferred_serving_id,
        )
        version_id = None
        if body.nutrition is not None:
            basis = catalog.basis_from_payload(body.nutrition.model_dump())
            version_id = await catalog.add_nutrition_revision(
                conn, product_id, basis, source="manual", owner_user_id=session.user_id,
                created_by_user_id=session.user_id,
            )
    return ok({**result, "nutrition_version_id": version_id})


MembershipAction = Literal["add", "exclude", "restore", "archive"]


class MembershipBody(BaseModel):
    action: MembershipAction
    version: Optional[int] = None


async def _membership_action(
    conn: Any, user_id: int, product_id: int, action: MembershipAction, version: Optional[int],
) -> dict:
    if action == "add":
        if not await catalog.product_visible(conn, user_id, product_id):
            raise CatalogError("not_found")
        return await catalog.upsert_membership(conn, user_id, product_id, "web", explicit=True)
    if action == "exclude":
        return await catalog.exclude_product(conn, user_id, product_id, version)
    if action == "restore":
        return await catalog.restore_product(conn, user_id, product_id, version)
    return await catalog.archive_product(conn, user_id, product_id, version)


@router.post("/products/{product_id}/membership")
async def membership(product_id: int, body: MembershipBody, session: Session):
    async with transaction() as conn:
        result = await _membership_action(conn, session.user_id, product_id, body.action, body.version)
    return ok(result)


class BulkMembershipBody(BaseModel):
    action: MembershipAction
    product_ids: list[int] = Field(min_length=1, max_length=500)


@router.post("/products/bulk-membership")
async def bulk_membership(body: BulkMembershipBody, session: Session):
    """One transaction; each product runs in a savepoint so one failure
    does not undo the others (partial success is reported)."""
    done: list[int] = []
    failed: list[dict] = []
    async with transaction() as conn:
        for product_id in dict.fromkeys(body.product_ids):
            try:
                async with conn.transaction():  # savepoint
                    await _membership_action(conn, session.user_id, product_id, body.action, None)
                done.append(product_id)
            except CatalogError as exc:
                failed.append({"product_id": product_id, "error": str(exc)})
    return ok({"done": done, "failed": failed})


@router.post("/products/{product_id}/refresh")
async def refresh_product(product_id: int, session: Session):
    pool = await get_pool()
    product = await catalog.get_product(pool, product_id, session.user_id)
    if product is None:
        raise error(404, "not_found")
    if product["provider"] != "fatsecret":
        raise error(409, "not_refreshable")
    ctx = await _ctx(session)
    try:
        await catalog.refresh_fatsecret_product(
            pool, product_id, product["external_id"],
            access_token=ctx.fs_token, access_secret=ctx.fs_secret,
            language=ctx.language,
        )
    except Exception:
        logger.warning("FatSecret refresh failed for product %s", product_id, exc_info=True)
        raise error(502, "provider_unavailable")
    return ok({"refreshed": True})


@router.get("/default-rules")
async def default_rules(session: Session, include_learned: bool = Query(default=False)):
    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT r.id, r.alias_display, r.alias_normalized, r.product_id, r.serving_id, r.preparation,
                  r.suggested_portion_g, r.origin, r.enabled, r.priority, r.use_count, r.version,
                  p.provider, p.external_id, p.name, p.provider_name, p.provider_cached_until,
                  m.display_name
           FROM food_default_rules r
           JOIN food_products p ON p.id = r.product_id
           LEFT JOIN user_product_memberships m ON m.product_id = p.id AND m.user_id = r.user_id
           WHERE r.user_id = $1 AND r.enabled AND ($2 OR r.origin = 'manual')
           ORDER BY r.origin, r.alias_normalized""",
        session.user_id, include_learned,
    )
    items = []
    for row in rows:
        data = dict(row)
        data["product_label"] = catalog.display_label(data)
        for key in ("provider_name", "provider_cached_until", "name"):
            data.pop(key, None)
        items.append(data)
    return ok({"items": items})


class RuleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alias: str = Field(min_length=1, max_length=255)
    product_id: int
    serving_id: Optional[str] = Field(default=None, max_length=32)
    preparation: Optional[Preparation] = None
    suggested_portion_g: Optional[Decimal] = Field(default=None, gt=0, le=5000)
    priority: int = Field(default=0, ge=0, le=100)
    replace_rule_id: Optional[int] = None
    version: Optional[int] = None


@router.post("/default-rules", status_code=201)
async def create_rule(body: RuleBody, session: Session):
    async with transaction() as conn:
        rule = await catalog.set_default_rule(
            conn, session.user_id, body.alias, body.product_id, serving_id=body.serving_id,
            preparation=body.preparation, suggested_portion_g=body.suggested_portion_g,
            priority=body.priority, replace_rule_id=body.replace_rule_id,
            expected_version=body.version,
        )
    return ok(rule, status_code=201)


@router.delete("/default-rules/{rule_id}")
async def delete_rule(rule_id: int, session: Session, version: int = Query(...)):
    pool = await get_pool()
    try:
        result = await catalog.disable_rule(pool, session.user_id, rule_id, version)
    except CatalogError as exc:
        raise translate(exc)
    return ok(result)


class PreviewBody(BaseModel):
    text: str = Field(min_length=1, max_length=255)
    brand: Optional[str] = Field(default=None, max_length=100)


@router.post("/default-rules/preview")
async def preview_rule(body: PreviewBody, session: Session):
    """"Try phrase": run resolution without recording anything."""
    from app.services.food_resolver import FoodQuery, resolve

    pool = await get_pool()
    ctx = await _ctx(session)
    query = FoodQuery.from_item({"name_original": body.text, "brand": body.brand})
    resolution = await resolve(
        pool, session.user_id, query,
        review_all=ctx.prefs.recording_policy == "review_all", allow_search=False,
    )
    return ok({
        "decision": resolution.decision,
        "reason": resolution.reason,
        "candidates": [c.to_json() for c in resolution.candidates],
        "parsed": {"text": query.text, "brand": query.brand, "preparation": query.preparation,
                   "fat_pct": query.fat_pct},
    })


# ---------------------------------------------------------------------------
# FatSecret history → My Products
# ---------------------------------------------------------------------------

class ImportBody(BaseModel):
    days: Optional[int] = Field(default=None, ge=1, le=365)
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    mode: Optional[Literal["auto", "selective"]] = None


@router.post("/catalog-imports", status_code=201)
async def start_catalog_import(body: ImportBody, session: Session):
    pool = await get_pool()
    ctx = await _ctx(session)
    if not ctx.fs_connected:
        raise error(400, "fatsecret_not_connected")
    try:
        job = await catalog_import.start_import(
            pool, session.user_id, days=body.days or ctx.prefs.history_import_days,
            date_from=body.date_from, date_to=body.date_to,
            mode=body.mode or ctx.prefs.history_import_mode,
            kind="range" if body.date_from else "initial",
        )
    except ImportError_ as exc:
        raise translate(exc)
    return ok(catalog_import.job_to_json(job), status_code=201)


@router.get("/catalog-imports")
async def catalog_imports(session: Session):
    pool = await get_pool()
    jobs = await catalog_import.latest_jobs(pool, session.user_id)
    return ok({"items": [catalog_import.job_to_json(j) for j in jobs]})


@router.get("/catalog-imports/{job_id}")
async def catalog_import_detail(
    job_id: int,
    session: Session,
    state: Literal["pending", "added", "excluded", "already_member", "skipped"] = Query(default="pending"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    pool = await get_pool()
    job = await catalog_import.get_job(pool, session.user_id, job_id)
    if job is None:
        raise error(404, "not_found")
    candidates = await catalog_import.list_candidates(
        pool, session.user_id, job_id, state=state, limit=limit, offset=offset,
    )
    return ok({"job": catalog_import.job_to_json(job), "candidates": candidates})


class SelectionBody(BaseModel):
    selected: list[int] = Field(default_factory=list, max_length=2000)
    skipped: list[int] = Field(default_factory=list, max_length=2000)
    select_all: bool = False


@router.post("/catalog-imports/{job_id}/selection")
async def catalog_selection(job_id: int, body: SelectionBody, session: Session):
    async with transaction() as conn:
        result = await catalog_import.apply_selection(
            conn, session.user_id, job_id, selected=body.selected, skipped=body.skipped,
            select_all=body.select_all,
        )
    return ok(result)


@router.post("/catalog-imports/{job_id}/cancel")
async def catalog_cancel(job_id: int, session: Session):
    pool = await get_pool()
    if await catalog_import.cancel_job(pool, session.user_id, job_id):
        return ok({"cancelled": True})
    if await catalog_import.get_job(pool, session.user_id, job_id) is None:
        raise error(404, "not_found")
    raise error(409, "not_cancellable")


# ---------------------------------------------------------------------------
# Drafts and entries (shared with the bot)
# ---------------------------------------------------------------------------

@router.get("/food-drafts")
async def drafts(session: Session):
    pool = await get_pool()
    return ok({"items": await ledger.open_drafts(pool, session.user_id)})


@router.get("/food-drafts/{draft_id}")
async def draft_detail(draft_id: int, session: Session):
    pool = await get_pool()
    draft = await ledger.get_draft(pool, draft_id, session.user_id)
    if draft is None:
        raise error(404, "not_found")
    return ok(draft)


class DraftItemPatch(BaseModel):
    index: int = Field(ge=0, le=20)
    product_id: Optional[int] = None
    grams: Optional[Decimal] = None


class DraftPatch(BaseModel):
    version: int
    items: list[DraftItemPatch] = Field(default_factory=list, max_length=20)
    meal_type: Optional[MealType] = None
    local_date: Optional[date] = None


@router.patch("/food-drafts/{draft_id}")
async def patch_draft(draft_id: int, body: DraftPatch, session: Session):
    ctx = await _ctx(session)
    async with transaction() as conn:
        draft = await ledger.patch_draft_items(
            conn, ctx, draft_id, expected_version=body.version,
            patches=[p.model_dump() for p in body.items],
            meal_type=body.meal_type, local_date=body.local_date,
        )
    return ok(draft)


class CommitBody(BaseModel):
    version: int


@router.post("/food-drafts/{draft_id}/commit")
async def commit_draft(draft_id: int, body: CommitBody, session: Session):
    pool = await get_pool()
    ctx = await _ctx(session)
    try:
        draft, entries = await ledger.commit_draft(pool, ctx, draft_id, expected_version=body.version)
    except (LedgerError, CatalogError) as exc:
        raise translate(exc)
    await _kick_sync(pool, entries)
    return ok({"draft": draft, "entries": entries})


@router.post("/food-drafts/{draft_id}/cancel")
async def cancel_draft(draft_id: int, session: Session):
    pool = await get_pool()
    if await ledger.cancel_draft(pool, draft_id, session.user_id):
        return ok({"cancelled": True})
    if await ledger.get_draft(pool, draft_id, session.user_id) is None:
        raise error(404, "not_found")
    raise error(409, "not_cancellable")


async def _kick_sync(pool: Any, entries: list[dict]) -> None:
    ids = [e["id"] for e in entries if e.get("sync_status") in ("pending", "delete_pending")]
    if not ids:
        return
    from app.services.food_sync import process_outbox

    try:
        await process_outbox(pool, entry_ids=ids, limit=len(ids) * 2)
    except Exception:
        logger.warning("Immediate sync failed; the outbox job will retry", exc_info=True)


@router.get("/food-entries")
async def food_entries(session: Session, day: Optional[date] = Query(default=None, alias="date")):
    pool = await get_pool()
    ctx = await _ctx(session)
    try:
        local_date = ledger.validate_local_date(day, ctx) if day else ctx.today()
    except LedgerError as exc:
        raise translate(exc)
    view = await ledger.daily_view(pool, ctx, local_date)
    goal = await goal_for_date(pool, session.user_id, local_date)
    return ok({**view.to_json(), "goal": goal})


@router.get("/food-entries/range")
async def food_entries_range(
    session: Session,
    start: date = Query(alias="from"),
    end: Optional[date] = Query(default=None, alias="to"),
):
    """Daily views for ``from``..``to`` inclusive (max 31 days), newest first."""
    pool = await get_pool()
    ctx = await _ctx(session)
    try:
        last = ledger.validate_local_date(end, ctx) if end else ctx.today()
        first = ledger.validate_local_date(start, ctx)
    except LedgerError as exc:
        raise translate(exc)
    if first > last:
        raise error(400, "date_range_invalid")
    span = (last - first).days + 1
    if span > MAX_HISTORY_RANGE_DAYS:
        raise error(400, "date_range_too_long", max_days=MAX_HISTORY_RANGE_DAYS)
    dates = [last - timedelta(days=i) for i in range(span)]
    views = await ledger.daily_views(pool, ctx, dates)
    days = []
    for local_date, view in zip(dates, views):
        goal = await goal_for_date(pool, session.user_id, local_date)
        days.append({**view.to_json(), "goal": goal})
    return ok({"days": days})


class EntryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: int
    grams: Decimal
    meal_type: MealType
    local_date: Optional[date] = None
    serving_id: Optional[str] = Field(default=None, max_length=32)
    idempotency_key: IdempotencyKey


@router.post("/food-entries", status_code=201)
async def create_entry(body: EntryCreate, session: Session):
    """Manual entry without AI: same validation, calculation, ledger and sync."""
    pool = await get_pool()
    ctx = await _ctx(session)
    try:
        local_date = ledger.validate_local_date(body.local_date, ctx)
        item = await ledger.prepare_item(
            pool, ctx, product_id=body.product_id, grams=body.grams, serving_id=body.serving_id,
        )
        entries = await ledger.commit_items(
            pool, ctx, [item], meal_type=body.meal_type, local_date=local_date,
            origin="web_manual", idempotency_prefix=f"web:{session.user_id}:{body.idempotency_key}",
        )
    except (LedgerError, CatalogError, NutritionError) as exc:
        raise translate(exc)
    await _kick_sync(pool, entries)
    return ok({"entries": entries}, status_code=201)


class EntryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    grams: Optional[Decimal] = None
    meal_type: Optional[MealType] = None
    local_date: Optional[date] = None
    product_id: Optional[int] = None
    serving_id: Optional[str] = Field(default=None, max_length=32)


@router.patch("/food-entries/{entry_id}")
async def patch_entry(entry_id: int, body: EntryPatch, session: Session):
    pool = await get_pool()
    ctx = await _ctx(session)
    try:
        entry = await ledger.edit_entry(
            pool, ctx, entry_id, expected_version=body.version, grams=body.grams,
            meal_type=body.meal_type, local_date=body.local_date, product_id=body.product_id,
            serving_id=body.serving_id,
        )
    except (LedgerError, CatalogError, NutritionError) as exc:
        raise translate(exc)
    await _kick_sync(pool, [entry])
    return ok(entry)


@router.delete("/food-entries/{entry_id}")
async def delete_entry(entry_id: int, session: Session, version: int = Query(...)):
    pool = await get_pool()
    ctx = await _ctx(session)
    try:
        entry = await ledger.void_entry(pool, ctx, entry_id, expected_version=version)
    except (LedgerError, CatalogError) as exc:
        raise translate(exc)
    await _kick_sync(pool, [entry])
    return ok(entry)


class CopyBody(BaseModel):
    local_date: date
    meal_type: Optional[MealType] = None
    idempotency_key: IdempotencyKey


@router.post("/food-entries/{entry_id}/copy", status_code=201)
async def copy_entry(entry_id: int, body: CopyBody, session: Session):
    pool = await get_pool()
    ctx = await _ctx(session)
    source = await ledger.get_entry(pool, session.user_id, entry_id)
    if source is None:
        raise error(404, "not_found")
    if not source.get("product_id") or not source.get("grams"):
        raise error(400, "legacy_entry_not_copyable")
    try:
        local_date = ledger.validate_local_date(body.local_date, ctx)
        item = await ledger.prepare_item(
            pool, ctx, product_id=source["product_id"], grams=source["grams"],
            serving_id=source.get("remote_serving_id"), label=source["food_name"],
            quantity_source="reused",
        )
        entries = await ledger.commit_items(
            pool, ctx, [item], meal_type=body.meal_type or source["meal_type"], local_date=local_date,
            origin="web_manual", idempotency_prefix=f"web:{session.user_id}:{body.idempotency_key}",
        )
    except (LedgerError, CatalogError, NutritionError) as exc:
        raise translate(exc)
    await _kick_sync(pool, entries)
    return ok({"entries": entries}, status_code=201)


def _declared_length(request: Request) -> int:
    raw = request.headers.get("content-length")
    if not raw:
        return 0
    try:
        length = int(raw)
    except ValueError:
        raise error(400, "invalid_content_length")
    if length < 0:
        raise error(400, "invalid_content_length")
    return length


@router.post("/uploads", status_code=201)
async def upload(
    request: Request,
    session: Session,
    idempotency_key: str = Query(min_length=8, max_length=64, pattern=IDEMPOTENCY_PATTERN),
    caption: str = Query(default="", max_length=300),
    draft_id: Optional[int] = Query(default=None),
):
    """Bounded image intake (raw body, image/jpeg|png|webp) → shared draft.

    Barcode decoding happens on the backend; this is the MVP scan path
    (Telegram's scanner is QR-only).
    """
    from app.services import food_bot  # imports vision/barcode stacks
    from app.services.food_vision import MediaError, check_upload

    content_type = request.headers.get("content-type", "")
    if _declared_length(request) > settings.media_max_bytes:
        raise error(413, "image_too_large", max_bytes=settings.media_max_bytes)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > settings.media_max_bytes:
            raise error(413, "image_too_large", max_bytes=settings.media_max_bytes)
    try:
        check_upload(bytes(body), content_type)
    except MediaError as exc:
        raise error(415 if str(exc) == "image_type_unsupported" else 400, str(exc))
    pool = await get_pool()
    ctx = await _ctx(session)
    reply = await food_bot.handle_photo(
        pool, ctx, bytes(body), caption=caption, chat_id=None, message_id=None,
        commit_key=f"webup:{session.user_id}:{idempotency_key}", origin="web_upload",
        draft_id=draft_id, auto_commit=False,
    )
    draft = await ledger.get_draft(pool, reply.draft_id, session.user_id) if reply and reply.draft_id else None
    return ok({"draft": draft, "message": reply.text if reply and reply.text else None}, status_code=201)


# ---------------------------------------------------------------------------
# Integrations
# ---------------------------------------------------------------------------

@router.get("/integrations")
async def integrations(session: Session):
    pool = await get_pool()
    row = await pool.fetchrow(f"SELECT {_CONNECTED_SQL} FROM users WHERE id = $1", session.user_id)
    outbox = await pool.fetch(
        """SELECT status, count(*) AS n, max(updated_at) AS last
           FROM food_sync_outbox WHERE user_id = $1 GROUP BY status""",
        session.user_id,
    )
    last_error = await pool.fetchrow(
        """SELECT last_error, updated_at FROM food_sync_outbox
           WHERE user_id = $1 AND status IN ('failed', 'unknown') ORDER BY updated_at DESC LIMIT 1""",
        session.user_id,
    )
    apple = await pool.fetchrow(
        "SELECT last_sync_at, is_active FROM apple_health_sync WHERE user_id = $1", session.user_id,
    )
    jobs = await catalog_import.latest_jobs(pool, session.user_id, limit=1)
    by_status = {r["status"]: r for r in outbox}
    succeeded = by_status.get("succeeded")
    return ok({
        "fatsecret": {
            "connected": row["fatsecret_connected"],
            "outbox": {status: r["n"] for status, r in by_status.items()},
            "last_successful_sync": succeeded["last"] if succeeded else None,
            "last_error": dict(last_error) if last_error else None,
            "history_import": catalog_import.job_to_json(jobs[0]) if jobs else None,
        },
        "whoop": {"connected": row["whoop_connected"]},
        "apple_health": {
            "connected": bool(apple and apple["is_active"]),
            "last_sync_at": apple["last_sync_at"] if apple else None,
            # iPhone permissions/automations can only be changed on the device.
            "setup_hint": "/apple_health_help",
        },
    })


@router.post("/integrations/{provider}/connect-link")
async def connect_link(provider: str, session: Session):
    from app.security import sign_oauth_state

    if provider == "fatsecret":
        state = quote(sign_oauth_state(session.telegram_user_id, "fatsecret"), safe="")
        return ok({"url": f"{settings.app_base_url}/fatsecret/connect?state={state}"})
    if provider == "whoop":
        from app.services.whoop_sync import WHOOP_AUTH_URL, WHOOP_SCOPES

        url = f"{WHOOP_AUTH_URL}?" + urlencode({
            "client_id": settings.whoop_client_id, "redirect_uri": settings.whoop_redirect_uri,
            "response_type": "code", "scope": WHOOP_SCOPES,
            "state": sign_oauth_state(session.telegram_user_id, "whoop"),
        }, quote_via=quote)
        return ok({"url": url})
    raise error(404, "not_found")


@router.post("/integrations/fatsecret/disconnect")
async def fatsecret_disconnect(session: Session):
    """Forget FatSecret credentials. Local entries and My Products stay."""
    async with transaction() as conn:
        await ledger.detach_fatsecret(conn, session.user_id)
    return ok({"disconnected": True})
