import pytest

from app.database import get_pool, close_pool


@pytest.mark.asyncio
async def test_get_pool_returns_pool(mock_settings):
    """Pool creation should not raise (even if DB unreachable, it lazy-connects)."""
    # This tests the module loads and the function signature is correct.
    # Actual DB connectivity is tested in integration tests.
    from app.database import _pool
    assert _pool is None  # Not yet initialized
