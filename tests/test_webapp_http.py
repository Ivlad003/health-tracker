"""Web App HTTP surface without a database: headers, error envelope, flags."""
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import WEBAPP_CSP, app, webapp_headers


class _Session:
    user_id = 7
    telegram_user_id = 70
    is_admin = False
    transport = "bearer"


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def test_webapp_header_policy():
    index = webapp_headers("/app/")
    assert index["Cache-Control"] == "no-cache"
    assert index["Content-Security-Policy"] == WEBAPP_CSP
    assert "https://telegram.org" in WEBAPP_CSP and "frame-ancestors" in WEBAPP_CSP
    asset = webapp_headers("/app/assets/index-abc123.js")
    assert "immutable" in asset["Cache-Control"]
    api = webapp_headers("/api/v1/webapp/me")
    assert api["Cache-Control"] == "no-store" and api["X-Content-Type-Options"] == "nosniff"
    assert webapp_headers("/api/v1/admin/jobs")["Cache-Control"] == "no-store"
    assert webapp_headers("/health") == {}
    assert webapp_headers("/application") == {}


@pytest.mark.asyncio
async def test_app_shell_is_served_with_security_headers():
    async with _client() as client:
        response = await client.get("/app/")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'self'" in response.headers["content-security-policy"]


@pytest.mark.asyncio
async def test_validation_errors_use_the_webapp_envelope(monkeypatch):
    async def fake_pool():
        return object()

    async def fake_resolve(_pool, _token):
        return _Session()

    monkeypatch.setattr("app.routers.webapp.get_pool", fake_pool)
    monkeypatch.setattr("app.services.webapp_auth.resolve_session", fake_resolve)
    async with _client() as client:
        response = await client.post(
            "/api/v1/webapp/food-entries", headers={"Authorization": "Bearer t"},
            json={"product_id": 1, "grams": "10", "meal_type": "brunch", "idempotency_key": "abcdefgh"},
        )
        login = await client.post("/api/v1/webapp/auth/telegram", json={})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error"] == "validation_error"
    assert detail["fields"] == [{"field": "meal_type", "message": "literal_error"}]
    assert response.headers["cache-control"] == "no-store"
    assert login.status_code == 422 and login.json()["detail"]["error"] == "validation_error"


@pytest.mark.asyncio
async def test_other_routers_keep_fastapi_validation_body():
    async with _client() as client:
        response = await client.get("/fatsecret/connect")  # required `state` missing
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


@pytest.mark.asyncio
async def test_upload_rejects_malformed_content_length(monkeypatch):
    async def fake_pool():
        return object()

    async def fake_resolve(_pool, _token):
        return _Session()

    monkeypatch.setattr("app.routers.webapp.get_pool", fake_pool)
    monkeypatch.setattr("app.services.webapp_auth.resolve_session", fake_resolve)
    async with _client() as client:
        response = await client.post(
            "/api/v1/webapp/uploads?idempotency_key=abcdefgh1",
            headers={"Authorization": "Bearer t", "Content-Type": "image/jpeg", "Content-Length": "abc"},
            content=b"",
        )
        too_big = await client.post(
            "/api/v1/webapp/uploads?idempotency_key=abcdefgh1",
            headers={"Authorization": "Bearer t", "Content-Type": "image/jpeg",
                     "Content-Length": str(10**12)},
            content=b"",
        )
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "invalid_content_length"
    assert too_big.status_code == 413 and too_big.json()["detail"]["error"] == "image_too_large"


@pytest.mark.asyncio
async def test_admin_routes_use_the_session_admin_flag(monkeypatch):
    async def fake_pool():
        return object()

    async def fake_resolve(_pool, _token):
        return _Session()

    monkeypatch.setattr("app.routers.webapp.get_pool", fake_pool)
    monkeypatch.setattr("app.services.webapp_auth.resolve_session", fake_resolve)
    async with _client() as client:
        response = await client.get("/api/v1/admin/features", headers={"Authorization": "Bearer t"})
    assert response.status_code == 403 and response.json()["detail"]["error"] == "forbidden"


class _FlagPool:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    async def fetch(self, _sql):
        self.calls += 1
        return self.rows


@pytest.mark.asyncio
async def test_enabled_map_reads_all_flags_once_and_caches():
    from app.services import feature_flags

    pool = _FlagPool([{"key": "food_history", "enabled": False}])
    first = await feature_flags.enabled_map(pool)
    second = await feature_flags.enabled_map(pool)
    assert first == second
    assert set(first) == set(feature_flags.FLAGS)
    assert first["food_history"] is False
    assert pool.calls == 1
