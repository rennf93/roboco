"""roboco.services.health coverage — DB / Redis connectivity probes.

Both probes are thin wrappers: they execute a trivial command and return
("ok", True) on success, (str(error), False) on failure. We mock the
underlying clients so the test doesn't depend on Redis/Postgres uptime.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.services.health import check_database, check_redis

# ---------------------------------------------------------------------------
# check_database
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_database_ok() -> None:
    """Successful SELECT 1 returns ('ok', True)."""
    mock_session = MagicMock()
    mock_session.execute = AsyncMock()

    class _Ctx:
        async def __aenter__(self) -> object:
            return mock_session

        async def __aexit__(self, *_args: object) -> None:
            return None

    with patch("roboco.services.health.get_db_context", return_value=_Ctx()):
        msg, ok = await check_database()
    assert ok is True
    assert msg == "ok"


@pytest.mark.asyncio
async def test_check_database_error() -> None:
    """Any exception inside the context is captured into ('<msg>', False)."""

    class _BadCtx:
        async def __aenter__(self) -> object:
            raise RuntimeError("connection refused")

        async def __aexit__(self, *_args: object) -> None:
            return None

    with patch("roboco.services.health.get_db_context", return_value=_BadCtx()):
        msg, ok = await check_database()
    assert ok is False
    assert "connection refused" in msg


# ---------------------------------------------------------------------------
# check_redis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_redis_ok() -> None:
    """Successful ping returns ('ok', True)."""
    mock_client = MagicMock()
    mock_client.ping = AsyncMock(return_value=True)
    mock_client.close = AsyncMock()

    with patch("roboco.services.health.redis.from_url", return_value=mock_client):
        msg, ok = await check_redis()
    assert ok is True
    assert msg == "ok"
    mock_client.ping.assert_awaited_once()
    mock_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_redis_error() -> None:
    """Connection failure is caught and reported as a tuple."""
    mock_client = MagicMock()
    mock_client.ping = AsyncMock(side_effect=ConnectionError("redis down"))

    with patch("roboco.services.health.redis.from_url", return_value=mock_client):
        msg, ok = await check_redis()
    assert ok is False
    assert "redis down" in msg
