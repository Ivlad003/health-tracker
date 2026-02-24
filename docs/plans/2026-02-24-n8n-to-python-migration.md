# n8n to Python Migration — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace all 7 n8n workflows with a single FastAPI Python application, fixing the broken FatSecret OAuth 1.0 flows and implementing the placeholder Food Diary endpoint.

**Architecture:** Single FastAPI app serving webhook endpoints (WHOOP OAuth callback, FatSecret search/connect/callback/diary, IP check, health) plus an APScheduler background job for hourly WHOOP data sync. Uses asyncpg for raw SQL queries matching the existing PostgreSQL schema (INTEGER PKs, not UUID). All config from `.env` via pydantic-settings.

**Tech Stack:** Python 3.12+, FastAPI, uvicorn, asyncpg, httpx, APScheduler, pydantic-settings

**Reference files:**
- DB schema (production): `database/migrations/002_health_tracker_schema.sql`
- Session knowledge: `docs/en/session-knowledge.md`
- Existing n8n workflows: `n8n/workflows/*.json`
- Env vars: `.env.example`

---

## Task 1: Project Scaffolding — requirements.txt + config

**Files:**
- Create: `app/__init__.py`
- Create: `app/config.py`
- Create: `requirements.txt`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

**Step 1: Create `requirements.txt`**

```txt
fastapi==0.115.6
uvicorn[standard]==0.34.0
asyncpg==0.30.0
httpx==0.28.1
apscheduler==3.11.0
pydantic-settings==2.7.1
python-dotenv==1.0.1
pytest==8.3.4
pytest-asyncio==0.25.0
pytest-mock==3.14.0
```

**Step 2: Create `app/__init__.py`**

```python
```

(Empty file)

**Step 3: Create `app/config.py`**

```python
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str

    # Telegram
    telegram_bot_token: str = ""

    # WHOOP
    whoop_client_id: str
    whoop_client_secret: str
    whoop_redirect_uri: str

    # FatSecret
    fatsecret_client_id: str
    fatsecret_client_secret: str
    fatsecret_shared_secret: str = ""

    # OpenAI
    openai_api_key: str = ""

    # App
    app_base_url: str = "http://localhost:8000"
    log_level: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
```

**Step 4: Create `tests/__init__.py`**

```python
```

(Empty file)

**Step 5: Create `tests/conftest.py`**

```python
import pytest


@pytest.fixture
def mock_settings(monkeypatch):
    """Set minimal env vars for Settings to load."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
    monkeypatch.setenv("WHOOP_CLIENT_ID", "test_whoop_id")
    monkeypatch.setenv("WHOOP_CLIENT_SECRET", "test_whoop_secret")
    monkeypatch.setenv("WHOOP_REDIRECT_URI", "http://localhost:8000/whoop/callback")
    monkeypatch.setenv("FATSECRET_CLIENT_ID", "test_fs_id")
    monkeypatch.setenv("FATSECRET_CLIENT_SECRET", "test_fs_secret")
    monkeypatch.setenv("FATSECRET_SHARED_SECRET", "test_fs_shared")
```

**Step 6: Install dependencies**

Run: `pip install -r requirements.txt`

Expected: All packages install successfully.

**Step 7: Commit**

```bash
git add app/__init__.py app/config.py requirements.txt tests/__init__.py tests/conftest.py
git commit -m "feat: project scaffolding — requirements, config, test fixtures"
```

---

## Task 2: Database Connection Pool

**Files:**
- Create: `app/database.py`
- Create: `tests/test_database.py`

**Step 1: Write the failing test**

Create `tests/test_database.py`:

```python
import pytest

from app.database import get_pool, close_pool


@pytest.mark.asyncio
async def test_get_pool_returns_pool(mock_settings):
    """Pool creation should not raise (even if DB unreachable, it lazy-connects)."""
    # This tests the module loads and the function signature is correct.
    # Actual DB connectivity is tested in integration tests.
    from app.database import _pool
    assert _pool is None  # Not yet initialized
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_database.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'app.database'`

**Step 3: Write `app/database.py`**

```python
import asyncpg
import logging

from app.config import settings

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=settings.database_url,
            min_size=2,
            max_size=10,
        )
        logger.info("Database connection pool created")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("Database connection pool closed")
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_database.py -v`

Expected: PASS

**Step 5: Commit**

```bash
git add app/database.py tests/test_database.py
git commit -m "feat: asyncpg database connection pool"
```

---

## Task 3: FastAPI App Skeleton with Lifespan

**Files:**
- Create: `app/main.py`
- Create: `tests/test_main.py`

**Step 1: Write the failing test**

Create `tests/test_main.py`:

```python
import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app


@pytest.mark.asyncio
async def test_health_endpoint(mock_settings):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'app.main'`

**Step 3: Write `app/main.py`**

```python
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.database import get_pool, close_pool

    await get_pool()
    logger.info("App started")
    yield
    await close_pool()
    logger.info("App stopped")


app = FastAPI(title="Health Tracker API", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_main.py -v`

Expected: PASS (lifespan is skipped in test client by default unless we use it explicitly; the endpoint itself should work)

Note: The lifespan will try to connect to DB. If the test fails because of DB connection, update the test to mock the pool:

```python
@pytest.mark.asyncio
async def test_health_endpoint(mock_settings, monkeypatch):
    import app.database as db_mod
    monkeypatch.setattr(db_mod, "get_pool", lambda: None)
    monkeypatch.setattr(db_mod, "close_pool", lambda: None)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
```

**Step 5: Commit**

```bash
git add app/main.py tests/test_main.py
git commit -m "feat: FastAPI app skeleton with health endpoint and lifespan"
```

---

## Task 4: IP Check + Utility Router

**Files:**
- Create: `app/routers/__init__.py`
- Create: `app/routers/utils.py`
- Modify: `app/main.py` (add router include)
- Create: `tests/test_routers_utils.py`

**Step 1: Write the failing test**

Create `tests/test_routers_utils.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

from app.main import app


@pytest.mark.asyncio
async def test_ip_check(mock_settings):
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"ip": "84.54.23.99"}
    mock_response.raise_for_status = lambda: None

    with patch("app.routers.utils.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/ip-check")

    assert resp.status_code == 200
    assert resp.json()["ip"] == "84.54.23.99"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_routers_utils.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'app.routers'`

**Step 3: Create `app/routers/__init__.py`**

```python
```

(Empty file)

**Step 4: Create `app/routers/utils.py`**

```python
import httpx
from fastapi import APIRouter

router = APIRouter()


@router.get("/ip-check")
async def ip_check():
    async with httpx.AsyncClient() as client:
        resp = await client.get("https://api.ipify.org?format=json")
        resp.raise_for_status()
        return resp.json()
```

**Step 5: Register router in `app/main.py`**

Add after the `app = FastAPI(...)` line:

```python
from app.routers.utils import router as utils_router

app.include_router(utils_router)
```

**Step 6: Run test to verify it passes**

Run: `pytest tests/test_routers_utils.py -v`

Expected: PASS

**Step 7: Commit**

```bash
git add app/routers/__init__.py app/routers/utils.py app/main.py tests/test_routers_utils.py
git commit -m "feat: IP check utility endpoint"
```

---

## Task 5: FatSecret OAuth 2.0 Food Search

**Files:**
- Create: `app/services/__init__.py`
- Create: `app/services/fatsecret_api.py`
- Create: `app/routers/fatsecret.py`
- Modify: `app/main.py` (add router)
- Create: `tests/test_fatsecret_api.py`

**Step 1: Write the failing test**

Create `tests/test_fatsecret_api.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.asyncio
async def test_get_oauth2_token(mock_settings):
    from app.services.fatsecret_api import get_oauth2_token

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"access_token": "test_token", "expires_in": 86400}
    mock_response.raise_for_status = MagicMock()

    with patch("app.services.fatsecret_api.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_client

        token = await get_oauth2_token()

    assert token == "test_token"


@pytest.mark.asyncio
async def test_search_food(mock_settings):
    from app.services.fatsecret_api import search_food

    token_response = MagicMock()
    token_response.json.return_value = {"access_token": "tok", "expires_in": 86400}
    token_response.raise_for_status = MagicMock()

    search_response = MagicMock()
    search_response.json.return_value = {
        "foods": {
            "food": [
                {
                    "food_id": "123",
                    "food_name": "Chicken Breast",
                    "brand_name": "Generic",
                    "food_description": "Per 100g - Calories: 165kcal | Fat: 3.60g | Carbs: 0.00g | Protein: 31.02g",
                }
            ]
        }
    }
    search_response.raise_for_status = MagicMock()

    with patch("app.services.fatsecret_api.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=[token_response, search_response])
        mock_cls.return_value = mock_client

        result = await search_food("chicken breast")

    assert result["results_count"] == 1
    assert result["results"][0]["food_id"] == "123"
    assert result["results"][0]["name"] == "Chicken Breast"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_fatsecret_api.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'app.services'`

**Step 3: Create `app/services/__init__.py`**

```python
```

(Empty file)

**Step 4: Create `app/services/fatsecret_api.py`**

This replaces the n8n `FatSecret Food Search` workflow (n8n ID: `qTHRcgiqFx9SqTNm`).

```python
import httpx
import logging

from app.config import settings

logger = logging.getLogger(__name__)

FATSECRET_TOKEN_URL = "https://oauth.fatsecret.com/connect/token"
FATSECRET_API_URL = "https://platform.fatsecret.com/rest/server.api"


async def get_oauth2_token() -> str:
    """Get FatSecret OAuth 2.0 access token (server-to-server, client_credentials)."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            FATSECRET_TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": settings.fatsecret_client_id,
                "client_secret": settings.fatsecret_client_secret,
                "scope": "basic",
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


async def search_food(query: str, max_results: int = 5) -> dict:
    """Search FatSecret public food database. Returns formatted results."""
    token = await get_oauth2_token()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            FATSECRET_API_URL,
            headers={"Authorization": f"Bearer {token}"},
            data={
                "method": "foods.search",
                "search_expression": query,
                "format": "json",
                "max_results": str(max_results),
            },
        )
        resp.raise_for_status()
        data = resp.json()

    foods = data.get("foods", {}).get("food", [])
    if not isinstance(foods, list):
        foods = [foods]

    results = [
        {
            "name": f.get("food_name", ""),
            "brand": f.get("brand_name", "Generic"),
            "description": f.get("food_description", ""),
            "food_id": f.get("food_id", ""),
        }
        for f in foods
    ]

    return {"query": query, "results_count": len(results), "results": results}
```

**Step 5: Create `app/routers/fatsecret.py`**

```python
from fastapi import APIRouter, Query, HTTPException

from app.services.fatsecret_api import search_food

router = APIRouter()


@router.get("/food/search")
async def food_search(q: str = Query(..., min_length=1)):
    try:
        return await search_food(q)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"FatSecret API error: {e}")
```

**Step 6: Register router in `app/main.py`**

Add:

```python
from app.routers.fatsecret import router as fatsecret_router

app.include_router(fatsecret_router)
```

**Step 7: Run tests**

Run: `pytest tests/test_fatsecret_api.py -v`

Expected: PASS

**Step 8: Commit**

```bash
git add app/services/__init__.py app/services/fatsecret_api.py app/routers/fatsecret.py app/main.py tests/test_fatsecret_api.py
git commit -m "feat: FatSecret OAuth 2.0 food search endpoint"
```

---

## Task 6: WHOOP OAuth Callback

**Files:**
- Create: `app/routers/whoop.py`
- Modify: `app/main.py` (add router)
- Create: `tests/test_whoop_callback.py`

This replaces the n8n `WHOOP OAuth Callback` workflow (n8n ID: `sWOs9ycgABYKCQ8g`).

**Step 1: Write the failing test**

Create `tests/test_whoop_callback.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport

from app.main import app


@pytest.mark.asyncio
async def test_whoop_callback_missing_code(mock_settings):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/whoop/callback")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_whoop_callback_success(mock_settings):
    token_resp = MagicMock()
    token_resp.status_code = 200
    token_resp.json.return_value = {
        "access_token": "whoop_access",
        "refresh_token": "whoop_refresh",
        "expires_in": 3600,
    }
    token_resp.raise_for_status = MagicMock()

    recovery_resp = MagicMock()
    recovery_resp.status_code = 200
    recovery_resp.json.return_value = {"records": [{"user_id": 12345}]}
    recovery_resp.raise_for_status = MagicMock()

    mock_pool = AsyncMock()
    mock_pool.execute = AsyncMock(return_value="UPDATE 1")

    with (
        patch("app.routers.whoop.httpx.AsyncClient") as mock_cls,
        patch("app.routers.whoop.get_pool", return_value=mock_pool),
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=token_resp)
        mock_client.get = AsyncMock(return_value=recovery_resp)
        mock_cls.return_value = mock_client

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test", follow_redirects=False
        ) as client:
            resp = await client.get(
                "/whoop/callback", params={"code": "auth_code", "state": "999"}
            )

    assert resp.status_code == 200
    assert "WHOOP Connected" in resp.text
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_whoop_callback.py -v`

Expected: FAIL — `No module named 'app.routers.whoop'`

**Step 3: Create `app/routers/whoop.py`**

```python
import httpx
import logging

from fastapi import APIRouter, Query, Response
from fastapi.responses import HTMLResponse

from app.config import settings
from app.database import get_pool

logger = logging.getLogger(__name__)

router = APIRouter()

WHOOP_TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
WHOOP_RECOVERY_URL = "https://api.prod.whoop.com/developer/v2/recovery"

SUCCESS_HTML = """<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>WHOOP Connected</title><style>*{margin:0;padding:0;box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0a0a0a;color:#e4e4e7;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px}.card{background:#1a1a1a;border:1px solid rgba(255,255,255,0.05);border-radius:16px;padding:48px;text-align:center;max-width:400px}.icon{width:64px;height:64px;background:rgba(16,185,129,0.1);border-radius:16px;display:flex;align-items:center;justify-content:center;margin:0 auto 24px}h1{font-size:24px;font-weight:700;margin-bottom:8px}p{color:#a1a1aa;line-height:1.6;margin-bottom:24px}</style></head><body><div class="card"><div class="icon"><svg width="32" height="32" fill="none" viewBox="0 0 24 24" stroke="#10B981" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg></div><h1>WHOOP Connected!</h1><p>Your WHOOP account has been successfully linked to Health Tracker. You can close this window.</p></div></body></html>"""

ERROR_HTML = """<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Authorization Failed</title><style>*{margin:0;padding:0;box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0a0a0a;color:#e4e4e7;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px}.card{background:#1a1a1a;border:1px solid rgba(255,255,255,0.05);border-radius:16px;padding:48px;text-align:center;max-width:400px}.icon{width:64px;height:64px;background:rgba(244,63,94,0.1);border-radius:16px;display:flex;align-items:center;justify-content:center;margin:0 auto 24px}h1{font-size:24px;font-weight:700;margin-bottom:8px}p{color:#a1a1aa;line-height:1.6}</style></head><body><div class="card"><div class="icon"><svg width="32" height="32" fill="none" viewBox="0 0 24 24" stroke="#F43F5E" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg></div><h1>Authorization Failed</h1><p>Something went wrong during WHOOP authorization. Please try again.</p></div></body></html>"""


@router.get("/whoop/callback")
async def whoop_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
):
    if not code:
        return HTMLResponse(content=ERROR_HTML, status_code=400)

    try:
        async with httpx.AsyncClient() as client:
            # Exchange authorization code for tokens
            token_resp = await client.post(
                WHOOP_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": settings.whoop_client_id,
                    "client_secret": settings.whoop_client_secret,
                    "redirect_uri": settings.whoop_redirect_uri,
                },
            )
            token_resp.raise_for_status()
            tokens = token_resp.json()

            # Fetch recovery to get whoop_user_id (profile endpoint unavailable)
            recovery_resp = await client.get(
                WHOOP_RECOVERY_URL,
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
                params={"limit": "1"},
            )
            recovery_resp.raise_for_status()
            whoop_user_id = recovery_resp.json()["records"][0]["user_id"]

        # Store tokens in DB
        pool = await get_pool()
        await pool.execute(
            """UPDATE users
               SET whoop_access_token = $1,
                   whoop_refresh_token = $2,
                   whoop_token_expires_at = NOW() + make_interval(secs => $3),
                   whoop_user_id = $4,
                   updated_at = NOW()
               WHERE telegram_user_id = $5""",
            tokens["access_token"],
            tokens["refresh_token"],
            tokens["expires_in"],
            str(whoop_user_id),
            int(state) if state else 0,
        )

        logger.info("WHOOP connected for telegram_user_id=%s", state)
        return HTMLResponse(content=SUCCESS_HTML)

    except Exception:
        logger.exception("WHOOP OAuth callback failed")
        return HTMLResponse(content=ERROR_HTML, status_code=500)
```

**Step 4: Register router in `app/main.py`**

Add:

```python
from app.routers.whoop import router as whoop_router

app.include_router(whoop_router)
```

**Step 5: Run tests**

Run: `pytest tests/test_whoop_callback.py -v`

Expected: PASS

**Step 6: Commit**

```bash
git add app/routers/whoop.py app/main.py tests/test_whoop_callback.py
git commit -m "feat: WHOOP OAuth callback endpoint (replaces n8n workflow sWOs9ycgABYKCQ8g)"
```

---

## Task 7: WHOOP Data Sync Service

**Files:**
- Create: `app/services/whoop_sync.py`
- Create: `tests/test_whoop_sync.py`

This replaces the n8n `WHOOP Data Sync` workflow (n8n ID: `nAjGDfKdddSDH2MD`). The most complex workflow — hourly cron that refreshes tokens and fetches workouts/recovery/sleep in parallel.

**Step 1: Write the failing test**

Create `tests/test_whoop_sync.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone, timedelta


@pytest.mark.asyncio
async def test_refresh_token_if_expired(mock_settings):
    from app.services.whoop_sync import refresh_token_if_needed

    user = {
        "id": 1,
        "whoop_access_token": "old_token",
        "whoop_refresh_token": "refresh_tok",
        "whoop_token_expires_at": datetime.now(timezone.utc) - timedelta(hours=1),
    }

    new_token_resp = MagicMock()
    new_token_resp.json.return_value = {
        "access_token": "new_token",
        "refresh_token": "new_refresh",
        "expires_in": 3600,
    }
    new_token_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=new_token_resp)

    mock_pool = AsyncMock()
    mock_pool.execute = AsyncMock()

    token = await refresh_token_if_needed(user, mock_client, mock_pool)
    assert token == "new_token"
    mock_client.post.assert_called_once()
    mock_pool.execute.assert_called_once()


@pytest.mark.asyncio
async def test_no_refresh_if_not_expired(mock_settings):
    from app.services.whoop_sync import refresh_token_if_needed

    user = {
        "id": 1,
        "whoop_access_token": "valid_token",
        "whoop_refresh_token": "refresh_tok",
        "whoop_token_expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
    }

    mock_client = AsyncMock()
    mock_pool = AsyncMock()

    token = await refresh_token_if_needed(user, mock_client, mock_pool)
    assert token == "valid_token"
    mock_client.post.assert_not_called()


@pytest.mark.asyncio
async def test_process_workouts_empty(mock_settings):
    from app.services.whoop_sync import process_workouts

    result = process_workouts({"records": []}, user_id=1)
    assert result == []


@pytest.mark.asyncio
async def test_process_workouts_with_data(mock_settings):
    from app.services.whoop_sync import process_workouts

    data = {
        "records": [
            {
                "id": 100,
                "sport_name": "Running",
                "score_state": "SCORED",
                "score": {
                    "kilojoule": 1000,
                    "strain": 12.5,
                    "average_heart_rate": 145,
                    "max_heart_rate": 180,
                },
                "start": "2026-02-24T10:00:00Z",
                "end": "2026-02-24T11:00:00Z",
            }
        ]
    }

    result = process_workouts(data, user_id=1)
    assert len(result) == 1
    assert result[0]["whoop_workout_id"] == "100"
    assert result[0]["sport_name"] == "Running"
    assert result[0]["calories"] == pytest.approx(1000 / 4.184, rel=0.01)
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_whoop_sync.py -v`

Expected: FAIL — `ModuleNotFoundError`

**Step 3: Create `app/services/whoop_sync.py`**

```python
import asyncio
import httpx
import logging
from datetime import datetime, timezone

from app.config import settings
from app.database import get_pool

logger = logging.getLogger(__name__)

WHOOP_TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
WHOOP_API_BASE = "https://api.prod.whoop.com/developer/v2"


async def refresh_token_if_needed(
    user: dict, client: httpx.AsyncClient, pool
) -> str:
    """Check if token is expired, refresh if needed, return valid access_token."""
    expires_at = user["whoop_token_expires_at"]
    if expires_at and expires_at > datetime.now(timezone.utc):
        return user["whoop_access_token"]

    logger.info("Refreshing WHOOP token for user_id=%s", user["id"])
    resp = await client.post(
        WHOOP_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": user["whoop_refresh_token"],
            "client_id": settings.whoop_client_id,
            "client_secret": settings.whoop_client_secret,
        },
    )
    resp.raise_for_status()
    tokens = resp.json()

    await pool.execute(
        """UPDATE users
           SET whoop_access_token = $1,
               whoop_refresh_token = $2,
               whoop_token_expires_at = NOW() + make_interval(secs => $3),
               updated_at = NOW()
           WHERE id = $4""",
        tokens["access_token"],
        tokens["refresh_token"],
        tokens["expires_in"],
        user["id"],
    )

    return tokens["access_token"]


def process_workouts(data: dict, user_id: int) -> list[dict]:
    """Transform WHOOP workout API response into DB-ready dicts."""
    records = data.get("records", [])
    if not records:
        return []
    return [
        {
            "user_id": user_id,
            "whoop_workout_id": str(w["id"]),
            "sport_name": w.get("sport_name", "unknown"),
            "score_state": w.get("score_state", "PENDING_SCORE"),
            "kilojoules": w.get("score", {}).get("kilojoule", 0),
            "calories": (w.get("score", {}).get("kilojoule", 0) or 0) / 4.184,
            "strain": w.get("score", {}).get("strain", 0),
            "avg_heart_rate": w.get("score", {}).get("average_heart_rate", 0),
            "max_heart_rate": w.get("score", {}).get("max_heart_rate", 0),
            "started_at": w["start"],
            "ended_at": w["end"],
        }
        for w in records
    ]


def process_recovery(data: dict, user_id: int) -> list[dict]:
    """Transform WHOOP recovery API response into DB-ready dicts."""
    records = data.get("records", [])
    if not records:
        return []
    return [
        {
            "user_id": user_id,
            "whoop_cycle_id": str(r["cycle_id"]),
            "recovery_score": r.get("score", {}).get("recovery_score", 0),
            "resting_heart_rate": r.get("score", {}).get("resting_heart_rate", 0),
            "hrv_rmssd_milli": r.get("score", {}).get("hrv_rmssd_milli", 0),
            "spo2_percentage": r.get("score", {}).get("spo2_percentage", 0),
            "skin_temp_celsius": r.get("score", {}).get("skin_temp_celsius", 0),
            "recorded_at": r["created_at"],
        }
        for r in records
    ]


def process_sleep(data: dict, user_id: int) -> list[dict]:
    """Transform WHOOP sleep API response into DB-ready dicts."""
    records = data.get("records", [])
    if not records:
        return []
    return [
        {
            "user_id": user_id,
            "whoop_sleep_id": str(s["id"]),
            "score_state": s.get("score_state", "PENDING_SCORE"),
            "sleep_performance": s.get("score", {}).get("sleep_performance_percentage", 0),
            "sleep_consistency": s.get("score", {}).get("sleep_consistency_percentage", 0),
            "sleep_efficiency": s.get("score", {}).get("sleep_efficiency_percentage", 0),
            "total_sleep_milli": s.get("score", {}).get("stage_summary", {}).get("total_in_bed_time_milli", 0),
            "total_rem_milli": s.get("score", {}).get("stage_summary", {}).get("total_rem_sleep_time_milli", 0),
            "total_sws_milli": s.get("score", {}).get("stage_summary", {}).get("total_slow_wave_sleep_time_milli", 0),
            "total_light_milli": s.get("score", {}).get("stage_summary", {}).get("total_light_sleep_time_milli", 0),
            "total_awake_milli": s.get("score", {}).get("stage_summary", {}).get("total_awake_time_milli", 0),
            "respiratory_rate": s.get("score", {}).get("respiratory_rate", 0),
            "started_at": s["start"],
            "ended_at": s["end"],
        }
        for s in records
    ]


async def store_workouts(pool, workouts: list[dict]) -> int:
    """UPSERT workouts into whoop_activities table."""
    if not workouts:
        return 0
    for w in workouts:
        await pool.execute(
            """INSERT INTO whoop_activities
                   (user_id, whoop_workout_id, sport_name, score_state, kilojoules,
                    calories, strain, avg_heart_rate, max_heart_rate, started_at, ended_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
               ON CONFLICT (whoop_workout_id) DO UPDATE SET
                   score_state = EXCLUDED.score_state,
                   calories = EXCLUDED.calories,
                   strain = EXCLUDED.strain,
                   updated_at = NOW()""",
            w["user_id"], w["whoop_workout_id"], w["sport_name"], w["score_state"],
            w["kilojoules"], w["calories"], w["strain"],
            w["avg_heart_rate"], w["max_heart_rate"], w["started_at"], w["ended_at"],
        )
    return len(workouts)


async def store_recovery(pool, records: list[dict]) -> int:
    """UPSERT recovery records into whoop_recovery table."""
    if not records:
        return 0
    for r in records:
        await pool.execute(
            """INSERT INTO whoop_recovery
                   (user_id, whoop_cycle_id, recovery_score, resting_heart_rate,
                    hrv_rmssd_milli, spo2_percentage, skin_temp_celsius, recorded_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
               ON CONFLICT (whoop_cycle_id) DO UPDATE SET
                   recovery_score = EXCLUDED.recovery_score,
                   resting_heart_rate = EXCLUDED.resting_heart_rate,
                   hrv_rmssd_milli = EXCLUDED.hrv_rmssd_milli""",
            r["user_id"], r["whoop_cycle_id"], r["recovery_score"],
            r["resting_heart_rate"], r["hrv_rmssd_milli"],
            r["spo2_percentage"], r["skin_temp_celsius"], r["recorded_at"],
        )
    return len(records)


async def store_sleep(pool, records: list[dict]) -> int:
    """UPSERT sleep records into whoop_sleep table."""
    if not records:
        return 0
    for s in records:
        await pool.execute(
            """INSERT INTO whoop_sleep
                   (user_id, whoop_sleep_id, score_state, sleep_performance_percentage,
                    sleep_consistency_percentage, sleep_efficiency_percentage,
                    total_sleep_time_milli, total_rem_sleep_milli,
                    total_slow_wave_sleep_milli, total_light_sleep_milli,
                    total_awake_milli, respiratory_rate, started_at, ended_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
               ON CONFLICT (whoop_sleep_id) DO UPDATE SET
                   score_state = EXCLUDED.score_state,
                   sleep_performance_percentage = EXCLUDED.sleep_performance_percentage""",
            s["user_id"], s["whoop_sleep_id"], s["score_state"],
            s["sleep_performance"], s["sleep_consistency"], s["sleep_efficiency"],
            s["total_sleep_milli"], s["total_rem_milli"], s["total_sws_milli"],
            s["total_light_milli"], s["total_awake_milli"], s["respiratory_rate"],
            s["started_at"], s["ended_at"],
        )
    return len(records)


async def sync_whoop_data():
    """Main sync job: runs hourly. Fetches all WHOOP users, refreshes tokens, syncs data."""
    logger.info("Starting WHOOP data sync")

    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT id, telegram_user_id, whoop_user_id, whoop_access_token,
                  whoop_refresh_token, whoop_token_expires_at
           FROM users
           WHERE whoop_access_token IS NOT NULL AND whoop_user_id IS NOT NULL"""
    )

    if not rows:
        logger.info("No WHOOP users to sync")
        return

    for row in rows:
        user = dict(row)
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                access_token = await refresh_token_if_needed(user, client, pool)
                headers = {"Authorization": f"Bearer {access_token}"}

                # Fetch workouts, recovery, sleep in parallel (2-hour lookback)
                from datetime import timedelta
                start = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

                workout_resp, recovery_resp, sleep_resp = await asyncio.gather(
                    client.get(f"{WHOOP_API_BASE}/activity/workout",
                               headers=headers, params={"limit": "25", "start": start}),
                    client.get(f"{WHOOP_API_BASE}/recovery",
                               headers=headers, params={"limit": "10", "start": start}),
                    client.get(f"{WHOOP_API_BASE}/activity/sleep",
                               headers=headers, params={"limit": "10", "start": start}),
                )

                for resp in (workout_resp, recovery_resp, sleep_resp):
                    resp.raise_for_status()

                workouts = process_workouts(workout_resp.json(), user["id"])
                recovery = process_recovery(recovery_resp.json(), user["id"])
                sleep = process_sleep(sleep_resp.json(), user["id"])

                w_count = await store_workouts(pool, workouts)
                r_count = await store_recovery(pool, recovery)
                s_count = await store_sleep(pool, sleep)

                logger.info(
                    "Synced user_id=%s: %d workouts, %d recovery, %d sleep",
                    user["id"], w_count, r_count, s_count,
                )

        except Exception:
            logger.exception("Failed to sync WHOOP data for user_id=%s", user["id"])
            continue

    logger.info("WHOOP data sync complete")
```

**Step 4: Run tests**

Run: `pytest tests/test_whoop_sync.py -v`

Expected: PASS

**Step 5: Commit**

```bash
git add app/services/whoop_sync.py tests/test_whoop_sync.py
git commit -m "feat: WHOOP data sync service (replaces n8n workflow nAjGDfKdddSDH2MD)"
```

---

## Task 8: APScheduler Integration

**Files:**
- Create: `app/scheduler.py`
- Modify: `app/main.py` (add scheduler to lifespan)

**Step 1: Create `app/scheduler.py`**

```python
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.services.whoop_sync import sync_whoop_data

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


def start_scheduler():
    scheduler.add_job(
        sync_whoop_data,
        trigger=IntervalTrigger(hours=1),
        id="whoop_data_sync",
        name="WHOOP Data Sync (hourly)",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started — WHOOP sync every 1 hour")


def stop_scheduler():
    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")
```

**Step 2: Update `app/main.py` lifespan**

Replace the lifespan function:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.database import get_pool, close_pool
    from app.scheduler import start_scheduler, stop_scheduler

    await get_pool()
    start_scheduler()
    logger.info("App started")
    yield
    stop_scheduler()
    await close_pool()
    logger.info("App stopped")
```

**Step 3: Commit**

```bash
git add app/scheduler.py app/main.py
git commit -m "feat: APScheduler for hourly WHOOP data sync"
```

---

## Task 9: FatSecret OAuth 1.0 — HMAC-SHA1 Signing (THE FIX)

**Files:**
- Create: `app/services/fatsecret_auth.py`
- Add routes to: `app/routers/fatsecret.py`
- Create: `tests/test_fatsecret_auth.py`

This fixes the broken n8n workflows `FatSecret OAuth Connect` (5W40Z9r0cn5Z5Nyx) and `FatSecret OAuth Callback` (2kyFWt88FfOt14mw). The n8n Code nodes could not `require('crypto')` — Python has `hmac`/`hashlib` built-in.

**Step 1: Write the failing test**

Create `tests/test_fatsecret_auth.py`:

```python
import pytest


def test_hmac_sha1_signature(mock_settings):
    from app.services.fatsecret_auth import sign_oauth1_request

    # Known test vector for OAuth 1.0 signature
    sig = sign_oauth1_request(
        method="POST",
        url="https://example.com/request_token",
        params={"oauth_consumer_key": "key", "oauth_nonce": "nonce",
                "oauth_signature_method": "HMAC-SHA1", "oauth_timestamp": "123",
                "oauth_version": "1.0"},
        consumer_secret="secret",
        token_secret="",
    )
    assert isinstance(sig, str)
    assert len(sig) > 0  # Base64-encoded HMAC-SHA1


def test_build_auth_header(mock_settings):
    from app.services.fatsecret_auth import build_oauth1_header

    header = build_oauth1_header(
        params={"oauth_consumer_key": "key", "oauth_signature": "sig="},
    )
    assert header.startswith("OAuth ")
    assert "oauth_consumer_key" in header
    assert "oauth_signature" in header
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_fatsecret_auth.py -v`

Expected: FAIL — `ModuleNotFoundError`

**Step 3: Create `app/services/fatsecret_auth.py`**

```python
import hashlib
import hmac
import base64
import time
import secrets
import logging
from urllib.parse import quote

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

FATSECRET_REQUEST_TOKEN_URL = "https://authentication.fatsecret.com/oauth/request_token"
FATSECRET_AUTHORIZE_URL = "https://authentication.fatsecret.com/oauth/authorize"
FATSECRET_ACCESS_TOKEN_URL = "https://authentication.fatsecret.com/oauth/access_token"


def percent_encode(s: str) -> str:
    """RFC 5849 percent encoding."""
    return quote(str(s), safe="")


def sign_oauth1_request(
    method: str,
    url: str,
    params: dict,
    consumer_secret: str,
    token_secret: str = "",
) -> str:
    """Generate HMAC-SHA1 signature for OAuth 1.0 request."""
    sorted_params = sorted(params.items())
    param_string = "&".join(f"{percent_encode(k)}={percent_encode(v)}" for k, v in sorted_params)
    base_string = f"{method.upper()}&{percent_encode(url)}&{percent_encode(param_string)}"
    signing_key = f"{percent_encode(consumer_secret)}&{percent_encode(token_secret)}"

    hashed = hmac.new(
        signing_key.encode("utf-8"),
        base_string.encode("utf-8"),
        hashlib.sha1,
    )
    return base64.b64encode(hashed.digest()).decode("utf-8")


def build_oauth1_header(params: dict) -> str:
    """Build OAuth Authorization header string."""
    parts = ", ".join(
        f'{percent_encode(k)}="{percent_encode(v)}"'
        for k, v in sorted(params.items())
    )
    return f"OAuth {parts}"


async def get_request_token(callback_url: str) -> dict:
    """Step 1 of OAuth 1.0: Get request token from FatSecret."""
    params = {
        "oauth_consumer_key": settings.fatsecret_client_id,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_nonce": secrets.token_hex(16),
        "oauth_version": "1.0",
        "oauth_callback": callback_url,
    }

    signature = sign_oauth1_request(
        method="POST",
        url=FATSECRET_REQUEST_TOKEN_URL,
        params=params,
        consumer_secret=settings.fatsecret_shared_secret,
    )
    params["oauth_signature"] = signature

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            FATSECRET_REQUEST_TOKEN_URL,
            headers={"Authorization": build_oauth1_header(params)},
        )
        resp.raise_for_status()

    # Parse form-encoded response: oauth_token=X&oauth_token_secret=Y&oauth_callback_confirmed=true
    parsed = dict(pair.split("=", 1) for pair in resp.text.split("&"))
    return {
        "oauth_token": parsed.get("oauth_token", ""),
        "oauth_token_secret": parsed.get("oauth_token_secret", ""),
    }


async def exchange_access_token(
    oauth_token: str,
    oauth_verifier: str,
    token_secret: str,
) -> dict:
    """Step 3 of OAuth 1.0: Exchange request token for access token."""
    params = {
        "oauth_consumer_key": settings.fatsecret_client_id,
        "oauth_token": oauth_token,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_nonce": secrets.token_hex(16),
        "oauth_version": "1.0",
        "oauth_verifier": oauth_verifier,
    }

    signature = sign_oauth1_request(
        method="POST",
        url=FATSECRET_ACCESS_TOKEN_URL,
        params=params,
        consumer_secret=settings.fatsecret_shared_secret,
        token_secret=token_secret,
    )
    params["oauth_signature"] = signature

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            FATSECRET_ACCESS_TOKEN_URL,
            headers={"Authorization": build_oauth1_header(params)},
        )
        resp.raise_for_status()

    parsed = dict(pair.split("=", 1) for pair in resp.text.split("&"))
    return {
        "access_token": parsed.get("oauth_token", ""),
        "access_secret": parsed.get("oauth_token_secret", ""),
    }
```

**Step 4: Run tests**

Run: `pytest tests/test_fatsecret_auth.py -v`

Expected: PASS

**Step 5: Commit**

```bash
git add app/services/fatsecret_auth.py tests/test_fatsecret_auth.py
git commit -m "feat: FatSecret OAuth 1.0 HMAC-SHA1 signing (fixes n8n crypto blocker)"
```

---

## Task 10: FatSecret Connect + Callback Endpoints

**Files:**
- Modify: `app/routers/fatsecret.py` (add connect + callback routes)
- Create: `tests/test_fatsecret_oauth_flow.py`

**Step 1: Write the failing test**

Create `tests/test_fatsecret_oauth_flow.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport

from app.main import app


@pytest.mark.asyncio
async def test_fatsecret_connect_redirects(mock_settings):
    mock_pool = AsyncMock()
    mock_pool.execute = AsyncMock()

    with (
        patch("app.routers.fatsecret.get_pool", return_value=mock_pool),
        patch("app.routers.fatsecret.get_request_token", return_value={
            "oauth_token": "req_token_123",
            "oauth_token_secret": "req_secret_456",
        }),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test", follow_redirects=False
        ) as client:
            resp = await client.get(
                "/fatsecret/connect", params={"state": "999"}
            )

    assert resp.status_code == 307
    assert "oauth_token=req_token_123" in resp.headers["location"]


@pytest.mark.asyncio
async def test_fatsecret_callback_success(mock_settings):
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value={
        "id": 1,
        "request_secret": "stored_secret",
    })
    mock_pool.execute = AsyncMock()

    with (
        patch("app.routers.fatsecret.get_pool", return_value=mock_pool),
        patch("app.routers.fatsecret.exchange_access_token", return_value={
            "access_token": "final_token",
            "access_secret": "final_secret",
        }),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/fatsecret/callback", params={
                "oauth_token": "req_token",
                "oauth_verifier": "verifier_123",
                "state": "999",
            })

    assert resp.status_code == 200
    assert "FatSecret Connected" in resp.text
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_fatsecret_oauth_flow.py -v`

Expected: FAIL — routes don't exist yet

**Step 3: Update `app/routers/fatsecret.py`**

Add these imports and routes to the existing file:

```python
from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from app.database import get_pool
from app.services.fatsecret_api import search_food
from app.services.fatsecret_auth import (
    get_request_token,
    exchange_access_token,
    FATSECRET_AUTHORIZE_URL,
)
from app.config import settings

router = APIRouter()

FATSECRET_SUCCESS_HTML = """<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FatSecret Connected</title><style>*{margin:0;padding:0;box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0a0a0a;color:#e4e4e7;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px}.card{background:#1a1a1a;border:1px solid rgba(255,255,255,0.05);border-radius:16px;padding:48px;text-align:center;max-width:400px}.icon{width:64px;height:64px;background:rgba(16,185,129,0.1);border-radius:16px;display:flex;align-items:center;justify-content:center;margin:0 auto 24px}h1{font-size:24px;font-weight:700;margin-bottom:8px}p{color:#a1a1aa;line-height:1.6}</style></head><body><div class="card"><div class="icon"><svg width="32" height="32" fill="none" viewBox="0 0 24 24" stroke="#10B981" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg></div><h1>FatSecret Connected!</h1><p>Your FatSecret account has been linked. Your food diary will now sync automatically.</p></div></body></html>"""


@router.get("/food/search")
async def food_search(q: str = Query(..., min_length=1)):
    try:
        return await search_food(q)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"FatSecret API error: {e}")


@router.get("/fatsecret/connect")
async def fatsecret_connect(state: str = Query(...)):
    """OAuth 1.0 Step 1: Get request token, store secret, redirect user to FatSecret."""
    callback_url = f"{settings.app_base_url}/fatsecret/callback?state={state}"
    tokens = await get_request_token(callback_url)

    # Store request token secret in user settings for step 3
    pool = await get_pool()
    await pool.execute(
        """UPDATE users
           SET settings = jsonb_set(COALESCE(settings, '{}'),
                                    '{fatsecret_request_token_secret}',
                                    to_jsonb($1::text)),
               updated_at = NOW()
           WHERE telegram_user_id = $2""",
        tokens["oauth_token_secret"],
        int(state),
    )

    authorize_url = f"{FATSECRET_AUTHORIZE_URL}?oauth_token={tokens['oauth_token']}"
    return RedirectResponse(url=authorize_url)


@router.get("/fatsecret/callback")
async def fatsecret_callback(
    oauth_token: str = Query(...),
    oauth_verifier: str = Query(...),
    state: str = Query(...),
):
    """OAuth 1.0 Step 3: Exchange request token for access token, store in DB."""
    pool = await get_pool()

    # Retrieve stored request token secret
    row = await pool.fetchrow(
        """SELECT id, settings->>'fatsecret_request_token_secret' as request_secret
           FROM users WHERE telegram_user_id = $1""",
        int(state),
    )
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    tokens = await exchange_access_token(
        oauth_token=oauth_token,
        oauth_verifier=oauth_verifier,
        token_secret=row["request_secret"],
    )

    # Store access token and clear temp secret
    await pool.execute(
        """UPDATE users
           SET fatsecret_access_token = $1,
               fatsecret_access_secret = $2,
               settings = settings - 'fatsecret_request_token_secret',
               updated_at = NOW()
           WHERE id = $3""",
        tokens["access_token"],
        tokens["access_secret"],
        row["id"],
    )

    return HTMLResponse(content=FATSECRET_SUCCESS_HTML)
```

**Step 4: Run tests**

Run: `pytest tests/test_fatsecret_oauth_flow.py -v`

Expected: PASS

**Step 5: Commit**

```bash
git add app/routers/fatsecret.py tests/test_fatsecret_oauth_flow.py
git commit -m "feat: FatSecret OAuth 1.0 connect + callback (fixes broken n8n workflows)"
```

---

## Task 11: FatSecret Food Diary Endpoint

**Files:**
- Modify: `app/services/fatsecret_api.py` (add diary fetch)
- Modify: `app/routers/fatsecret.py` (add diary route)
- Create: `tests/test_fatsecret_diary.py`

This implements the placeholder n8n workflow `FatSecret Food Diary` (5HazDbwcUsZPvXzS).

**Step 1: Write the failing test**

Create `tests/test_fatsecret_diary.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.asyncio
async def test_fetch_food_diary(mock_settings):
    from app.services.fatsecret_api import fetch_food_diary

    diary_response = MagicMock()
    diary_response.json.return_value = {
        "food_entries": {
            "food_entry": [
                {
                    "food_entry_name": "Chicken Breast",
                    "meal": "Lunch",
                    "calories": "165",
                    "protein": "31.02",
                    "fat": "3.60",
                    "carbohydrate": "0.00",
                    "number_of_units": "1.00",
                    "serving_description": "100g",
                }
            ]
        }
    }
    diary_response.raise_for_status = MagicMock()

    with patch("app.services.fatsecret_api.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=diary_response)
        mock_cls.return_value = mock_client

        result = await fetch_food_diary(
            access_token="tok",
            access_secret="sec",
        )

    assert result["entries_count"] == 1
    assert result["meals"][0]["food"] == "Chicken Breast"
    assert result["meals"][0]["calories"] == 165.0
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_fatsecret_diary.py -v`

Expected: FAIL — `ImportError: cannot import name 'fetch_food_diary'`

**Step 3: Add `fetch_food_diary` to `app/services/fatsecret_api.py`**

Append to the existing file:

```python
import time
import math
from app.services.fatsecret_auth import sign_oauth1_request, build_oauth1_header
import secrets as secrets_mod


async def fetch_food_diary(
    access_token: str,
    access_secret: str,
    date: int | None = None,
) -> dict:
    """Fetch user's food diary from FatSecret via OAuth 1.0 signed request.

    Args:
        access_token: User's OAuth 1.0 access token.
        access_secret: User's OAuth 1.0 token secret.
        date: Days since epoch (Jan 1, 1970). Defaults to today.
    """
    if date is None:
        date = math.floor(time.time() / 86400)

    api_params = {
        "method": "food_entries.get.v2",
        "format": "json",
        "date": str(date),
    }

    oauth_params = {
        "oauth_consumer_key": settings.fatsecret_client_id,
        "oauth_token": access_token,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_nonce": secrets_mod.token_hex(16),
        "oauth_version": "1.0",
    }

    # Signature is computed over all params (OAuth + API)
    all_params = {**oauth_params, **api_params}
    signature = sign_oauth1_request(
        method="POST",
        url=FATSECRET_API_URL,
        params=all_params,
        consumer_secret=settings.fatsecret_shared_secret,
        token_secret=access_secret,
    )
    oauth_params["oauth_signature"] = signature

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            FATSECRET_API_URL,
            headers={"Authorization": build_oauth1_header(oauth_params)},
            data=api_params,
        )
        resp.raise_for_status()
        data = resp.json()

    entries = data.get("food_entries", {}).get("food_entry", [])
    if not isinstance(entries, list):
        entries = [entries]

    total_calories = 0.0
    meals = []
    for e in entries:
        cal = float(e.get("calories", 0))
        total_calories += cal
        meals.append({
            "food": e.get("food_entry_name", ""),
            "meal": e.get("meal", ""),
            "calories": cal,
            "protein": e.get("protein", "0"),
            "fat": e.get("fat", "0"),
            "carbs": e.get("carbohydrate", "0"),
            "serving": f"{e.get('number_of_units', '')} {e.get('serving_description', '')}".strip(),
        })

    return {
        "date": date,
        "total_calories": round(total_calories),
        "entries_count": len(meals),
        "meals": meals,
    }
```

**Step 4: Add diary route to `app/routers/fatsecret.py`**

Add this import and route:

```python
from app.services.fatsecret_api import search_food, fetch_food_diary
```

```python
@router.get("/fatsecret/diary")
async def fatsecret_diary(
    user_id: int = Query(..., description="Telegram user ID"),
    date: int | None = Query(default=None, description="Days since epoch"),
):
    """Fetch user's FatSecret food diary. Requires OAuth 1.0 connection."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """SELECT fatsecret_access_token, fatsecret_access_secret
           FROM users WHERE telegram_user_id = $1""",
        user_id,
    )
    if not row or not row["fatsecret_access_token"]:
        raise HTTPException(
            status_code=400,
            detail="FatSecret not connected. Use /fatsecret/connect first.",
        )

    try:
        return await fetch_food_diary(
            access_token=row["fatsecret_access_token"],
            access_secret=row["fatsecret_access_secret"],
            date=date,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"FatSecret API error: {e}")
```

**Step 5: Run tests**

Run: `pytest tests/test_fatsecret_diary.py -v`

Expected: PASS

**Step 6: Commit**

```bash
git add app/services/fatsecret_api.py app/routers/fatsecret.py tests/test_fatsecret_diary.py
git commit -m "feat: FatSecret food diary endpoint (implements placeholder n8n workflow)"
```

---

## Task 12: Dockerfile + .env.example Update

**Files:**
- Create: `Dockerfile`
- Modify: `.env.example` (add new vars)

**Step 1: Create `Dockerfile`**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Step 2: Update `.env.example`**

Add these new variables to `.env.example`:

```
# =============================================================================
# FATSECRET OAUTH 1.0 (for user food diary access)
# =============================================================================
# Shared Secret (different from Client Secret!) — from FatSecret API key page
FATSECRET_SHARED_SECRET=

# =============================================================================
# APP SETTINGS
# =============================================================================
APP_BASE_URL=https://your-domain.com
LOG_LEVEL=INFO
```

**Step 3: Verify Docker build**

Run: `docker build -t health-tracker .`

Expected: Build succeeds.

**Step 4: Commit**

```bash
git add Dockerfile .env.example
git commit -m "feat: Dockerfile and updated env example for Python migration"
```

---

## Task 13: Final `app/main.py` Assembly + All Router Imports

**Files:**
- Modify: `app/main.py` (final version with all routers)

**Step 1: Write the final `app/main.py`**

Ensure the file has all routers registered:

```python
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.database import get_pool, close_pool
    from app.scheduler import start_scheduler, stop_scheduler

    await get_pool()
    start_scheduler()
    logger.info("App started")
    yield
    stop_scheduler()
    await close_pool()
    logger.info("App stopped")


app = FastAPI(title="Health Tracker API", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


from app.routers.utils import router as utils_router
from app.routers.fatsecret import router as fatsecret_router
from app.routers.whoop import router as whoop_router

app.include_router(utils_router)
app.include_router(fatsecret_router)
app.include_router(whoop_router)
```

**Step 2: Run all tests**

Run: `pytest tests/ -v`

Expected: All tests PASS.

**Step 3: Commit**

```bash
git add app/main.py
git commit -m "feat: final app assembly with all routers"
```

---

## Task 14: Run Full Test Suite + Verify Docker

**Step 1: Run all tests**

Run: `pytest tests/ -v --tb=short`

Expected: All tests pass. Fix any failures before proceeding.

**Step 2: Build and test Docker image**

Run: `docker build -t health-tracker . && docker run --rm --env-file .env -p 8000:8000 health-tracker`

Expected: App starts, logs show scheduler started, `/health` returns `{"status": "ok"}`.

**Step 3: Test endpoints manually (with running container)**

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ip-check
curl "http://localhost:8000/food/search?q=chicken"
```

Expected: All return JSON responses.

**Step 4: Commit any fixes**

```bash
git add -A
git commit -m "fix: test suite and Docker verification"
```

---

## Task 15: Documentation Update

**Files:**
- Modify: `docs/en/session-knowledge.md`
- Modify: `docs/uk/session-knowledge.md`

**Step 1: Update session-knowledge with migration info**

Add a new section to `docs/en/session-knowledge.md`:

```markdown
## 10. n8n to Python Migration (2026-02-24)

The n8n workflows have been replaced by a single FastAPI Python application.

### Workflow to Endpoint Mapping

| n8n Workflow | n8n ID | Python Equivalent |
|---|---|---|
| WHOOP Data Sync | nAjGDfKdddSDH2MD | `app/services/whoop_sync.py` (APScheduler hourly) |
| WHOOP OAuth Callback | sWOs9ycgABYKCQ8g | `GET /whoop/callback` |
| FatSecret Food Search | qTHRcgiqFx9SqTNm | `GET /food/search?q=` |
| FatSecret OAuth Connect | 5W40Z9r0cn5Z5Nyx | `GET /fatsecret/connect?state=` (FIXED) |
| FatSecret OAuth Callback | 2kyFWt88FfOt14mw | `GET /fatsecret/callback` (FIXED) |
| FatSecret Food Diary | 5HazDbwcUsZPvXzS | `GET /fatsecret/diary?user_id=` (IMPLEMENTED) |
| IP Check | g3IFs0z6mPYtwPI9 | `GET /ip-check` |

### New Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /health` | Dokploy health check |
| `GET /food/search?q=` | FatSecret public food search |
| `GET /fatsecret/connect?state=` | FatSecret OAuth 1.0 initiation |
| `GET /fatsecret/callback` | FatSecret OAuth 1.0 completion |
| `GET /fatsecret/diary?user_id=` | FatSecret user food diary |
| `GET /whoop/callback` | WHOOP OAuth 2.0 callback |
| `GET /ip-check` | Server public IP check |

### Deployment

Docker image: `health-tracker`, deployed on Dokploy replacing n8n container.

```bash
docker build -t health-tracker .
docker run --env-file .env -p 8000:8000 health-tracker
```
```

**Step 2: Mirror the same update in `docs/uk/session-knowledge.md`** (Ukrainian translation)

**Step 3: Commit**

```bash
git add docs/en/session-knowledge.md docs/uk/session-knowledge.md
git commit -m "docs: update session knowledge with n8n-to-Python migration details"
```

---

## Summary

| Task | What | Replaces n8n Workflow |
|------|------|-----------------------|
| 1 | Project scaffolding (requirements, config, tests) | — |
| 2 | Database connection pool (asyncpg) | — |
| 3 | FastAPI app skeleton + /health endpoint | — |
| 4 | IP check utility endpoint | IP Check (g3IFs0z6mPYtwPI9) |
| 5 | FatSecret OAuth 2.0 food search | FatSecret Food Search (qTHRcgiqFx9SqTNm) |
| 6 | WHOOP OAuth callback | WHOOP OAuth Callback (sWOs9ycgABYKCQ8g) |
| 7 | WHOOP data sync service | WHOOP Data Sync (nAjGDfKdddSDH2MD) |
| 8 | APScheduler integration | Schedule Trigger node |
| 9 | FatSecret OAuth 1.0 HMAC-SHA1 signing | **FIXES** broken crypto in n8n |
| 10 | FatSecret connect + callback endpoints | FatSecret OAuth Connect/Callback (5W40Z9r0cn5Z5Nyx, 2kyFWt88FfOt14mw) |
| 11 | FatSecret food diary endpoint | FatSecret Food Diary (5HazDbwcUsZPvXzS) |
| 12 | Dockerfile + .env.example | n8n Docker container |
| 13 | Final app assembly | — |
| 14 | Full test suite + Docker verification | — |
| 15 | Documentation update | — |

**Dependencies:**
- Tasks 1-3 are sequential (each builds on prior)
- Tasks 4-6 are independent of each other (can be parallelized)
- Task 7 depends on Task 2 (database)
- Task 8 depends on Task 7 (scheduler needs sync service)
- Task 9 is independent
- Task 10 depends on Task 9 (uses auth service)
- Task 11 depends on Tasks 9 + 10 (uses OAuth 1.0 signing)
- Task 12 depends on all code tasks (1-11)
- Tasks 13-15 are sequential wrap-up
