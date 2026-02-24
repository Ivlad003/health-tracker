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
