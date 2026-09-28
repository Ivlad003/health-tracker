"""Owner/admin Web App API (`/api/v1/admin/*`) — plan §13, §16, AC-17.

The admin role is re-checked from ``user_roles`` on every request (it is
resolved together with the session; hiding a tab is not authorization).
Admins manage the shared starter catalog, feature availability and safe
operational actions; they do NOT get access to other users' private
diaries — job listings contain ids/statuses/error codes only, never food
names or provider bodies. Every change and its audit row are written in one
transaction.

This is a deliberate parallel authorization path to ``require_admin``
(``ADMIN_API_TOKEN``): the Mini App never sees that token.
"""
from __future__ import annotations

from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, ConfigDict, Field

from app.database import get_pool
from app.routers.webapp import NutritionBody, Preparation, current_session, error, ok, transaction
from app.services import feature_flags, webapp_auth
from app.services import food_catalog as catalog
from app.services.food_catalog import CatalogError

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


async def require_webapp_admin(
    session: webapp_auth.WebSession = Depends(current_session),
) -> webapp_auth.WebSession:
    if not session.is_admin:
        raise error(403, "forbidden")
    return session


AdminSession = Annotated[webapp_auth.WebSession, Depends(require_webapp_admin)]


# ---------------------------------------------------------------------------
# Shared starter catalog
# ---------------------------------------------------------------------------

@router.get("/my-products")
async def my_products(session: AdminSession):
    """Personal foods for the signed-in admin, including the FatSecret create mark."""
    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT p.id AS product_id, p.name, p.custom_fs_state, p.custom_fs_food_id,
                  n.energy_kcal, n.protein_g, n.fat_g, n.carbs_g
           FROM food_products p
           LEFT JOIN food_nutrition_versions n
               ON n.product_id = p.id AND n.is_current AND n.owner_user_id = p.owner_user_id
           WHERE p.owner_user_id = $1 AND p.status = 'active' AND p.provider = 'manual'
           ORDER BY p.name
           LIMIT 200""",
        session.user_id,
    )
    return ok({"items": [dict(r) for r in rows]})


@router.get("/catalog")
async def starter_catalog(
    session: AdminSession,
    q: Optional[str] = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT p.id, p.name, p.brand, p.preparation, p.barcode, p.status, p.version, p.updated_at,
                  n.energy_kcal, n.protein_g, n.fat_g, n.carbs_g, n.grams_per_basis
           FROM food_products p
           LEFT JOIN food_nutrition_versions n
               ON n.product_id = p.id AND n.is_current AND n.owner_user_id IS NULL
           WHERE p.is_starter AND ($1::text IS NULL OR p.name ILIKE '%' || $1 || '%')
           ORDER BY p.name LIMIT $2 OFFSET $3""",
        q, limit, offset,
    )
    return ok({"items": [dict(r) for r in rows]})


class StarterBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    brand: Optional[str] = Field(default=None, max_length=255)
    preparation: Preparation = "unknown"
    nutrition: NutritionBody


@router.post("/catalog", status_code=201)
async def create_starter(body: StarterBody, session: AdminSession):
    async with transaction() as conn:
        basis = catalog.basis_from_payload(body.nutrition.model_dump())
        product_id = await catalog.create_starter_product(
            conn, name=body.name, brand=body.brand, preparation=body.preparation,
            created_by_user_id=session.user_id,
        )
        await catalog.add_nutrition_revision(
            conn, product_id, basis, source="manual", owner_user_id=None,
            created_by_user_id=session.user_id,
        )
        await webapp_auth.audit(conn, actor_user_id=session.user_id, action="starter.create",
                                target_type="food_product", target_id=product_id, revision=1)
    return ok({"product_id": product_id}, status_code=201)


class StarterPatch(BaseModel):
    """Omitted fields stay; ``brand: null`` clears the brand."""
    model_config = ConfigDict(extra="forbid")
    version: int
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    brand: Optional[str] = Field(default=None, max_length=255)
    preparation: Optional[Preparation] = None
    status: Optional[Literal["active", "archived"]] = None
    nutrition: Optional[NutritionBody] = None


@router.patch("/catalog/{product_id}")
async def patch_starter(product_id: int, body: StarterPatch, session: AdminSession):
    """Edits create a new revision; personal defaults/overrides are untouched."""
    changed = body.model_dump(exclude_unset=True, exclude={"version", "nutrition"})
    async with transaction() as conn:
        row = await conn.fetchrow(
            """UPDATE food_products
               SET name = COALESCE($3, name),
                   brand = CASE WHEN $7 THEN $4 ELSE brand END,
                   preparation = COALESCE($5, preparation), status = COALESCE($6, status),
                   version = version + 1
               WHERE id = $1 AND is_starter AND version = $2
               RETURNING id, version""",
            product_id, body.version, body.name, body.brand, body.preparation, body.status,
            "brand" in changed,
        )
        if row is None:
            current = await conn.fetchval(
                "SELECT version FROM food_products WHERE id = $1 AND is_starter", product_id,
            )
            if current is None:
                raise CatalogError("not_found")
            raise catalog.VersionConflict(current)
        if body.nutrition is not None:
            await catalog.add_nutrition_revision(
                conn, product_id, catalog.basis_from_payload(body.nutrition.model_dump()),
                source="manual", owner_user_id=None, created_by_user_id=session.user_id,
            )
        await webapp_auth.audit(conn, actor_user_id=session.user_id, action="starter.update",
                                target_type="food_product", target_id=product_id,
                                revision=row["version"],
                                details={"fields": sorted(changed), "nutrition": body.nutrition is not None})
    return ok(dict(row))


# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------

@router.get("/features")
async def features(session: AdminSession):
    pool = await get_pool()
    return ok({"items": await feature_flags.list_flags(pool)})


class FlagBody(BaseModel):
    enabled: bool
    version: int = Field(ge=0)


FlagKey = Annotated[str, Path(max_length=64, pattern=r"^[a-z0-9_]+$")]


@router.put("/features/{key}")
async def set_feature(key: FlagKey, body: FlagBody, session: AdminSession):
    if key not in feature_flags.FLAGS:
        raise error(404, "unknown_flag")
    try:
        async with transaction() as conn:
            row = await feature_flags.set_flag(
                conn, key, body.enabled, actor_user_id=session.user_id, expected_version=body.version,
            )
            await webapp_auth.audit(conn, actor_user_id=session.user_id, action="feature.set",
                                    target_type="feature_flag", target_id=key, revision=row["version"],
                                    details={"enabled": body.enabled})
    except feature_flags.FlagError as exc:
        # Nothing was written; record the rejected attempt on its own.
        pool = await get_pool()
        await webapp_auth.audit(pool, actor_user_id=session.user_id, action="feature.set",
                                target_type="feature_flag", target_id=key, result="rejected",
                                details={"reason": str(exc)})
        if str(exc) == "version_conflict":
            raise error(409, "version_conflict")
        raise error(409, "feature_unavailable", reason=str(exc))
    return ok(row)


# ---------------------------------------------------------------------------
# Jobs / operations (anonymized)
# ---------------------------------------------------------------------------

@router.get("/jobs")
async def jobs(session: AdminSession):
    pool = await get_pool()
    outbox = await pool.fetch(
        "SELECT operation, status, count(*) AS n FROM food_sync_outbox GROUP BY operation, status"
    )
    failures = await pool.fetch(
        """SELECT id, operation, status, attempts, last_error, updated_at
           FROM food_sync_outbox WHERE status IN ('failed', 'unknown')
           ORDER BY updated_at DESC LIMIT 50"""
    )
    imports = await pool.fetch("SELECT status, count(*) AS n FROM catalog_import_jobs GROUP BY status")
    import_failures = await pool.fetch(
        """SELECT id, status, kind, days_total, days_done, attempts, last_error, updated_at
           FROM catalog_import_jobs WHERE status IN ('failed', 'partial')
           ORDER BY updated_at DESC LIMIT 50"""
    )
    drafts = await pool.fetch(
        """SELECT state, count(*) AS n FROM food_log_drafts
           WHERE created_at > NOW() - INTERVAL '7 days' GROUP BY state"""
    )
    return ok({
        "outbox": [dict(r) for r in outbox],
        "outbox_failures": [dict(r) for r in failures],
        "imports": [dict(r) for r in imports],
        "import_failures": [dict(r) for r in import_failures],
        "drafts_7d": [dict(r) for r in drafts],
    })


@router.post("/jobs/outbox/{op_id}/retry")
async def retry_outbox(op_id: int, session: AdminSession):
    """Known-safe actions only: a failed op is re-queued; an unknown create is
    sent to reconciliation (read the remote diary first), never re-sent."""
    pool = await get_pool()
    async with transaction() as conn:
        op = await conn.fetchrow(
            "SELECT id, status, operation FROM food_sync_outbox WHERE id = $1 FOR UPDATE", op_id,
        )
        if op is None:
            raise error(404, "not_found")
        if op["status"] == "failed":
            await conn.execute(
                """UPDATE food_sync_outbox SET status = 'pending', attempts = 0, next_attempt_at = NOW()
                   WHERE id = $1""",
                op_id,
            )
            action = "requeued"
        elif op["status"] == "unknown":
            action = "reconciled"
        else:
            raise error(409, "not_retryable", status=op["status"])
        await webapp_auth.audit(conn, actor_user_id=session.user_id, action=f"outbox.{action}",
                                target_type="food_sync_outbox", target_id=op_id)
    if action == "reconciled":
        # Reads the remote diary over HTTP: never inside a transaction.
        from app.services.food_sync import reconcile_unknown

        await reconcile_unknown(pool, limit=1, op_ids=[op_id])
    status = await pool.fetchval("SELECT status FROM food_sync_outbox WHERE id = $1", op_id)
    return ok({"action": action, "status": status})


@router.post("/jobs/imports/{job_id}/retry")
async def retry_import(job_id: int, session: AdminSession):
    async with transaction() as conn:
        row = await conn.fetchrow(
            """UPDATE catalog_import_jobs SET status = 'pending', next_attempt_at = NOW(), last_error = NULL
               WHERE id = $1 AND status IN ('failed', 'partial')
                 AND NOT EXISTS (SELECT 1 FROM catalog_import_jobs j2
                                 WHERE j2.user_id = catalog_import_jobs.user_id AND j2.id <> $1
                                   AND j2.status IN ('pending', 'running', 'partial'))
               RETURNING id, status""",
            job_id,
        )
        if row is None:
            raise error(409, "not_retryable")
        await webapp_auth.audit(conn, actor_user_id=session.user_id, action="import.retry",
                                target_type="catalog_import_job", target_id=job_id)
    return ok(dict(row))


@router.get("/audit")
async def audit_log(session: AdminSession,
                    limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0)):
    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT id, actor_user_id, action, target_type, target_id, revision, result, details, created_at
           FROM admin_audit_log ORDER BY id DESC LIMIT $1 OFFSET $2""",
        limit, offset,
    )
    return ok({"items": [dict(r) for r in rows]})


class RoleBody(BaseModel):
    telegram_user_id: int = Field(gt=0)
    grant: bool


@router.post("/roles")
async def set_role(body: RoleBody, session: AdminSession):
    async with transaction() as conn:
        target = await conn.fetchval(
            "SELECT id FROM users WHERE telegram_user_id = $1", body.telegram_user_id,
        )
        if target is None:
            raise error(404, "not_found")
        if not body.grant and target == session.user_id:
            raise error(400, "cannot_revoke_self")
        if body.grant:
            await conn.execute(
                """INSERT INTO user_roles (user_id, role, granted_by_user_id) VALUES ($1, 'admin', $2)
                   ON CONFLICT (user_id, role) DO UPDATE SET revoked_at = NULL,
                       granted_by_user_id = EXCLUDED.granted_by_user_id, granted_at = NOW()""",
                target, session.user_id,
            )
        else:
            await conn.execute(
                "UPDATE user_roles SET revoked_at = NOW() WHERE user_id = $1 AND role = 'admin'", target,
            )
        await webapp_auth.audit(conn, actor_user_id=session.user_id,
                                action="role.grant" if body.grant else "role.revoke",
                                target_type="user", target_id=target)
    return ok({"user_id": target, "admin": body.grant})
