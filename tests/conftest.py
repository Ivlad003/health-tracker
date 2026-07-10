import os

import pytest


os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("WHOOP_CLIENT_ID", "test_whoop_id")
os.environ.setdefault("WHOOP_CLIENT_SECRET", "test_whoop_secret")
os.environ.setdefault("WHOOP_REDIRECT_URI", "http://localhost:8000/whoop/callback")
os.environ.setdefault("FATSECRET_CLIENT_ID", "test_fs_id")
os.environ.setdefault("FATSECRET_CLIENT_SECRET", "test_fs_secret")
os.environ.setdefault("FATSECRET_SHARED_SECRET", "test_fs_shared")
os.environ.setdefault("APP_BASE_URL", "http://localhost:8000")


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
    monkeypatch.setenv("APP_BASE_URL", "http://localhost:8000")
