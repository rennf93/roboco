"""Reaper releases tasks whose last_heartbeat_at exceeds the TTL.

The orchestrator dispatcher periodically calls `_reap_stale_claims` to
release tasks whose holder has gone silent past the heartbeat TTL. The
schema-level column `last_heartbeat_at` (DateTime(timezone=True)) has
existed since migration 006; this test covers the runtime decision that
turns a stale heartbeat into a freed claim.

Datetimes used here are timezone-aware UTC because the underlying column
is tz-aware — comparing naive vs aware would raise TypeError in production
even though it would silently work against an in-memory mock.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator


@pytest.mark.asyncio
async def test_reap_stale_claims_releases_dead_holders() -> None:
    """A task past TTL is unclaimed; a fresh one is left alone."""
    stale_id = uuid4()
    fresh_id = uuid4()
    now = datetime.now(UTC)
    stale_task = type(
        "T",
        (),
        {"id": stale_id, "last_heartbeat_at": now - timedelta(seconds=600)},
    )()
    fresh_task = type(
        "T",
        (),
        {"id": fresh_id, "last_heartbeat_at": now - timedelta(seconds=10)},
    )()

    orch = AgentOrchestrator.__new__(AgentOrchestrator)  # bypass __init__
    orch._claim_heartbeat_ttl = 300
    svc = AsyncMock()
    svc.list_in_progress_or_claimed.return_value = [stale_task, fresh_task]
    svc.unclaim_for_reaper = AsyncMock()

    await orch._reap_with_service(svc)

    svc.unclaim_for_reaper.assert_awaited_once_with(stale_id)


@pytest.mark.asyncio
async def test_reap_stale_claims_releases_holders_with_null_heartbeat() -> None:
    """A claimed task that never heartbeated (NULL column) is treated as stale."""
    null_id = uuid4()
    null_task = type("T", (), {"id": null_id, "last_heartbeat_at": None})()

    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._claim_heartbeat_ttl = 300
    svc = AsyncMock()
    svc.list_in_progress_or_claimed.return_value = [null_task]
    svc.unclaim_for_reaper = AsyncMock()

    await orch._reap_with_service(svc)

    svc.unclaim_for_reaper.assert_awaited_once_with(null_id)


@pytest.mark.asyncio
async def test_reap_stale_claims_swallows_unclaim_errors() -> None:
    """An unclaim_for_reaper failure must not abort the reap loop."""
    stale_a = uuid4()
    stale_b = uuid4()
    now = datetime.now(UTC)
    task_a = type(
        "T", (), {"id": stale_a, "last_heartbeat_at": now - timedelta(seconds=600)}
    )()
    task_b = type(
        "T", (), {"id": stale_b, "last_heartbeat_at": now - timedelta(seconds=900)}
    )()

    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._claim_heartbeat_ttl = 300
    svc = AsyncMock()
    svc.list_in_progress_or_claimed.return_value = [task_a, task_b]
    svc.unclaim_for_reaper = AsyncMock(side_effect=[RuntimeError("transient"), None])

    await orch._reap_with_service(svc)

    # Both stale tasks attempted; second succeeded despite first raising.
    expected_attempts = 2
    assert svc.unclaim_for_reaper.await_count == expected_attempts
