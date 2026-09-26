"""Telegram Web App authentication, sessions and roles (plan §16, FR-17, AC-17).

- ``initData`` is validated with Telegram's HMAC scheme using the bot token
  (secret = HMAC_SHA256(key="WebAppData", msg=bot_token)); stale/future
  ``auth_date`` and missing user identity are rejected.
- A successful login creates a revocable server session. Activity slides the
  expiry (idle timeout ``webapp_session_ttl_seconds``) up to an absolute
  lifetime (``SESSION_MAX_LIFETIME``). ``last_seen_at`` is
  written at most once a minute, not on every request.
  Only SHA-256 hashes of the session and CSRF tokens are stored.
- Replay: the initData ``hash`` is recorded on the session. Re-using the same
  initData (a legitimate re-login after a 401) revokes the session it created
  before, so one initData never backs more than one live session, and the
  number of exchanges is capped (``INIT_DATA_MAX_USES``).
- Transport: ``Authorization: Bearer <session>`` (default; no ambient
  credential, no CSRF needed) or, when the client asks for it at login, a
  Secure/HttpOnly cookie (the token is then *not* returned in the body) and
  every mutation needs ``X-CSRF-Token`` and a same-origin ``Origin`` header.
- Roles live in ``user_roles``; the owner is bootstrapped from
  ``WEBAPP_ADMIN_TELEGRAM_IDS`` once and can be revoked server-side. The
  admin flag is resolved together with the session on every request.
  ``ADMIN_API_TOKEN`` is never used by or exposed to the Mini App.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode

from app.config import settings
from app.i18n import normalize_language

MAX_CLOCK_SKEW_SECONDS = 60
LAST_SEEN_WRITE_INTERVAL = timedelta(seconds=60)

# TODO: move to Settings (WEBAPP_SESSION_MAX_LIFETIME_SECONDS /
#  WEBAPP_INIT_DATA_MAX_USES) if they ever need to differ per deployment.
# Absolute session lifetime: sliding expiry never goes past created_at + this.
SESSION_MAX_LIFETIME = timedelta(hours=12)
# One initData string may be exchanged this many times (re-login after a
# 401); every exchange revokes the session the previous one created.
INIT_DATA_MAX_USES = 5


class InitDataError(ValueError):
    """Invalid Telegram initData (reason in args[0])."""


def _secret_key(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()


def sign_init_data(fields: dict[str, str], bot_token: str) -> str:
    """Build a signed initData string (used by tests and local tooling)."""
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    digest = hmac.new(_secret_key(bot_token), check.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": digest})


def validate_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_seconds: int,
    now: Optional[float] = None,
) -> dict:
    if not bot_token:
        raise InitDataError("bot_token_missing")
    if not init_data or len(init_data) > 8192:
        raise InitDataError("init_data_missing")
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise InitDataError("init_data_malformed") from exc
    fields: dict[str, str] = {}
    for key, value in pairs:
        if key in fields:
            raise InitDataError("init_data_duplicate_field")
        fields[key] = value
    received = fields.pop("hash", None)
    if not received:
        raise InitDataError("hash_missing")
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    expected = hmac.new(_secret_key(bot_token), check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received.lower()):
        raise InitDataError("bad_signature")
    try:
        auth_date = int(fields.get("auth_date", ""))
    except ValueError as exc:
        raise InitDataError("auth_date_missing") from exc
    current = now if now is not None else time.time()
    if auth_date > current + MAX_CLOCK_SKEW_SECONDS:
        raise InitDataError("auth_date_in_future")
    if current - auth_date > max_age_seconds:
        raise InitDataError("init_data_expired")
    try:
        user = json.loads(fields.get("user") or "")
    except (TypeError, ValueError) as exc:
        raise InitDataError("user_missing") from exc
    if not isinstance(user, dict) or not isinstance(user.get("id"), int) or user["id"] <= 0:
        raise InitDataError("user_missing")
    return {
        "user": user,
        "auth_date": auth_date,
        "query_id": fields.get("query_id"),
        # The verified HMAC: unique per initData, used for replay bookkeeping.
        "hash": expected,
    }


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class WebSession:
    session_id: int
    user_id: int
    telegram_user_id: int
    csrf_hash: str
    expires_at: datetime
    is_admin: bool = False
    transport: str = "bearer"


async def ensure_user(conn: Any, tg_user: dict) -> dict:
    """Get or create the user for a verified Telegram identity.

    Safe under concurrent first logins: the insert does nothing on conflict
    and the row is re-read (a no-op DO UPDATE would bump ``updated_at``, which
    is the profile version).
    """
    select = "SELECT id, language, timezone FROM users WHERE telegram_user_id = $1"
    row = await conn.fetchrow(select, tg_user["id"])
    if row is None:
        row = await conn.fetchrow(
            """INSERT INTO users (telegram_user_id, telegram_username, language)
               VALUES ($1, $2, $3)
               ON CONFLICT (telegram_user_id) DO NOTHING
               RETURNING id, language, timezone""",
            tg_user["id"], str(tg_user.get("username") or "")[:100],
            normalize_language(tg_user.get("language_code")),
        )
        if row is None:
            row = await conn.fetchrow(select, tg_user["id"])
    if tg_user["id"] in settings.admin_telegram_ids:
        # Bootstrap once; a later server-side revocation is respected.
        await conn.execute(
            """INSERT INTO user_roles (user_id, role) VALUES ($1, 'admin')
               ON CONFLICT (user_id, role) DO NOTHING""",
            row["id"],
        )
    return dict(row)


async def create_session(
    conn: Any, user_id: int, *, init_data_hash: Optional[str] = None,
) -> tuple[str, str, datetime]:
    """Create a session. Must run inside a transaction when ``init_data_hash``
    is given (the advisory lock serialises concurrent exchanges of one initData)."""
    if init_data_hash:
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", init_data_hash)
        uses = await conn.fetchval(
            "SELECT count(*) FROM webapp_sessions WHERE init_data_hash = $1", init_data_hash,
        )
        if uses >= INIT_DATA_MAX_USES:
            raise InitDataError("init_data_replayed")
        await conn.execute(
            """UPDATE webapp_sessions SET revoked_at = NOW()
               WHERE init_data_hash = $1 AND revoked_at IS NULL""",
            init_data_hash,
        )
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    expires = datetime.now(timezone.utc) + timedelta(seconds=settings.webapp_session_ttl_seconds)
    await conn.execute(
        """INSERT INTO webapp_sessions (user_id, token_hash, csrf_hash, expires_at, init_data_hash)
           VALUES ($1, $2, $3, $4, $5)""",
        user_id, _hash(token), _hash(csrf), expires, init_data_hash,
    )
    return token, csrf, expires


async def resolve_session(conn: Any, token: str) -> Optional[WebSession]:
    """Session + admin flag in one read; the sliding-expiry write is throttled."""
    if not token or len(token) > 128:
        return None
    row = await conn.fetchrow(
        """SELECT s.id, s.user_id, s.csrf_hash, s.expires_at, s.last_seen_at, u.telegram_user_id,
                  EXISTS (SELECT 1 FROM user_roles r
                          WHERE r.user_id = s.user_id AND r.role = 'admin'
                            AND r.revoked_at IS NULL) AS is_admin
           FROM webapp_sessions s
           JOIN users u ON u.id = s.user_id
           WHERE s.token_hash = $1 AND s.revoked_at IS NULL AND s.expires_at > NOW()""",
        _hash(token),
    )
    if row is None:
        return None
    expires_at = row["expires_at"]
    last_seen = row["last_seen_at"]
    if last_seen is None or datetime.now(timezone.utc) - last_seen >= LAST_SEEN_WRITE_INTERVAL:
        slid = await conn.fetchval(
            """UPDATE webapp_sessions
               SET last_seen_at = NOW(),
                   expires_at = GREATEST(expires_at, LEAST(
                       created_at + make_interval(secs => $2),
                       NOW() + make_interval(secs => $3)))
               WHERE id = $1 AND revoked_at IS NULL
               RETURNING expires_at""",
            row["id"], int(SESSION_MAX_LIFETIME.total_seconds()),
            settings.webapp_session_ttl_seconds,
        )
        expires_at = slid or expires_at
    return WebSession(
        session_id=row["id"], user_id=row["user_id"], telegram_user_id=row["telegram_user_id"],
        csrf_hash=row["csrf_hash"], expires_at=expires_at, is_admin=bool(row["is_admin"]),
    )


def csrf_matches(session: WebSession, supplied: Optional[str]) -> bool:
    return bool(supplied) and hmac.compare_digest(session.csrf_hash, _hash(supplied))


async def revoke_session(conn: Any, session_id: int) -> None:
    await conn.execute("UPDATE webapp_sessions SET revoked_at = NOW() WHERE id = $1", session_id)


async def purge_sessions(conn: Any) -> None:
    await conn.execute(
        "DELETE FROM webapp_sessions WHERE expires_at < NOW() - INTERVAL '1 day' OR revoked_at < NOW() - INTERVAL '1 day'"
    )


async def is_admin(conn: Any, user_id: int) -> bool:
    return bool(await conn.fetchval(
        "SELECT 1 FROM user_roles WHERE user_id = $1 AND role = 'admin' AND revoked_at IS NULL",
        user_id,
    ))


async def audit(conn: Any, *, actor_user_id: Optional[int], action: str, target_type: str,
                target_id: Any = None, revision: Optional[int] = None, result: str = "ok",
                details: Optional[dict] = None) -> None:
    await conn.execute(
        """INSERT INTO admin_audit_log (actor_user_id, action, target_type, target_id, revision,
                                        result, details)
           VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)""",
        actor_user_id, action[:64], target_type[:32],
        str(target_id)[:64] if target_id is not None else None, revision, result[:16],
        json.dumps(details or {}, default=str),
    )
