"""roboco.services.health coverage — DB / Redis connectivity probes.

Both probes are thin wrappers: they execute a trivial command and return
("ok", True) on success, (str(error), False) on failure. We mock the
underlying clients so the test doesn't depend on Redis/Postgres uptime.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.api.routes.health import health_check
from roboco.db.tables import RagIndexFailureTable
from roboco.services.health import check_database, check_redis
from roboco.services.rag_index_failures import reclaim_due

_EXPECTED_FAILURES = 3
_BUMPED_ATTEMPTS = 3

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


# ---------------------------------------------------------------------------
# health_check route — failed_index_count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_reports_failed_index_count() -> None:
    """The /health response carries failed_index_count from the dead-letter."""
    with (
        patch(
            "roboco.api.routes.health.count_failures",
            AsyncMock(return_value=3),
        ),
        patch(
            "roboco.api.routes.health.settings",
            MagicMock(app_version="9.9.9", environment="test"),
        ),
    ):
        resp = await health_check()
    assert resp.failed_index_count == _EXPECTED_FAILURES


@pytest.mark.asyncio
async def test_health_check_failed_index_count_zero_when_table_unavailable() -> None:
    """count_failures swallows errors and returns 0 — health never 500s."""
    with (
        patch(
            "roboco.api.routes.health.count_failures",
            AsyncMock(return_value=0),
        ),
        patch(
            "roboco.api.routes.health.settings",
            MagicMock(app_version="9.9.9", environment="test"),
        ),
    ):
        resp = await health_check()
    assert resp.failed_index_count == 0


# ---------------------------------------------------------------------------
# reclaim_due — janitor reclaims dead-lettered rows with backoff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reclaim_due_deletes_row_on_success() -> None:
    """A due row whose re-index succeeds is deleted from the dead-letter."""
    row = RagIndexFailureTable(
        id=uuid4(),
        doc_source="journal_entry",
        payload={
            "content": "x",
            "entry_type": "general",
            "entry_id": str(uuid4()),
            "agent_id": None,
            "task_id": None,
            "tags": [],
            "is_private": False,
        },
        attempts=1,
        last_error="boom",
        created_at=datetime.now(UTC),
        next_retry_at=datetime.now(UTC),
    )

    select_result = MagicMock()
    select_result.scalars.return_value.all.return_value = [row]
    session = MagicMock()
    session.execute = AsyncMock(return_value=select_result)
    session.add = MagicMock()
    # The delete path: execute returns a no-op.
    delete_session = MagicMock()
    delete_session.execute = AsyncMock(return_value=MagicMock())

    contexts = iter([session, delete_session])

    class _Ctx:
        async def __aenter__(self) -> MagicMock:
            return next(contexts)

        async def __aexit__(self, *_a: object) -> None:
            return None

    optimal = MagicMock()
    optimal.index_journal_entry = AsyncMock(return_value=None)

    with patch(
        "roboco.services.rag_index_failures.get_db_context", return_value=_Ctx()
    ):
        reclaimed = await reclaim_due(optimal)

    assert reclaimed == 1
    optimal.index_journal_entry.assert_awaited_once()


@pytest.mark.asyncio
async def test_reclaim_due_bumps_attempts_on_failure() -> None:
    """A due row whose re-index still fails has attempts bumped + backoff advanced."""
    row = RagIndexFailureTable(
        id=uuid4(),
        doc_source="journal_entry",
        payload={
            "content": "x",
            "entry_type": "general",
            "entry_id": str(uuid4()),
            "agent_id": None,
            "task_id": None,
            "tags": [],
            "is_private": False,
        },
        attempts=2,
        last_error="boom",
        created_at=datetime.now(UTC),
        next_retry_at=datetime.now(UTC),
    )

    select_result = MagicMock()
    select_result.scalars.return_value.all.return_value = [row]
    session = MagicMock()
    session.execute = AsyncMock(return_value=select_result)

    # Bump path: re-fetch the row, mutate, commit.
    db_row = MagicMock()
    db_row.attempts = 2
    bump_result = MagicMock()
    bump_result.scalar_one_or_none.return_value = db_row
    bump_session = MagicMock()
    bump_session.execute = AsyncMock(return_value=bump_result)

    contexts = iter([session, bump_session])

    class _Ctx:
        async def __aenter__(self) -> MagicMock:
            return next(contexts)

        async def __aexit__(self, *_a: object) -> None:
            return None

    optimal = MagicMock()
    optimal.index_journal_entry = AsyncMock(side_effect=RuntimeError("still down"))

    with patch(
        "roboco.services.rag_index_failures.get_db_context", return_value=_Ctx()
    ):
        reclaimed = await reclaim_due(optimal)

    assert reclaimed == 0
    # attempts bumped to 3
    assert db_row.attempts == _BUMPED_ATTEMPTS
    assert db_row.next_retry_at is not None
