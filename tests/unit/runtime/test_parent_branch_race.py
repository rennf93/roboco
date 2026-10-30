"""Dispatcher gives the parent's claim transaction a moment to commit, and
under load the time its branch actually takes to create, before auto-
blocking a waiting child.

Race scenario: PM's `i_will_plan` claims the parent (transitions to
`claimed`/`in_progress`, populates `assigned_to`, commits via
`_apply_claim_fields`), then `_provision_claim` creates the branch via
`_ensure_branch_for_task` and commits again right after. A child dev's spawn
dispatch can fire microseconds after the first commit (sub-second gap) or
minutes into a slow branch creation under load, and sees `branch_name=None`
either way.

Two tolerances, checked in order, only while the parent is mid-claim:
- Fast retry: a tight 3x250ms retry absorbs the sub-second commit gap without
  delaying the legitimate-block path.
- Provisioning grace (`parent_branch_provisioning_grace_seconds`, measured in
  fleet-active time via `_active_age`): once the fast retry is exhausted, a
  parent still inside the grace since its `claimed_at` is skipped this tick
  (no auto-block, next tick retries); past the grace it auto-blocks as
  before.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.runtime.orchestrator import AgentOrchestrator


def _make_orch() -> AgentOrchestrator:
    """Build a bare orchestrator without running __init__ (no settings I/O)."""
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    return orch


def _resp(payload: dict[str, Any]) -> MagicMock:
    """httpx.Response stand-in: is_success + .json() match the real API."""
    r = MagicMock()
    r.is_success = True
    r.json.return_value = payload
    return r


def _mid_claim(
    parent_id: str, claimed_at: datetime | None, status: str = "in_progress"
) -> dict[str, Any]:
    """A parent stuck mid-claim: claimed/in_progress, assigned, branchless."""
    return {
        "id": parent_id,
        "branch_name": None,
        "status": status,
        "assigned_to": "main-pm",
        "claimed_at": claimed_at.isoformat() if claimed_at else None,
    }


class _FakeLedger:
    """Reports a fixed `active_elapsed` for any start/end - a stand-in for a
    real `UptimeLedger` carrying a known downtime window."""

    def __init__(self, active_elapsed: timedelta) -> None:
        self._active_elapsed = active_elapsed

    def active_elapsed(self, start: datetime, end: datetime | None = None) -> timedelta:
        del start, end
        return self._active_elapsed


@pytest.mark.asyncio
async def test_retries_when_parent_is_mid_claim_and_branch_lands() -> None:
    """First fetch sees null branch (PM transaction in flight); second fetch
    sees the committed branch. We must NOT auto-block in this race."""
    task_id = str(uuid4())
    parent_id = str(uuid4())

    orch = _make_orch()

    client = AsyncMock()
    client.get.side_effect = [
        _resp(_mid_claim(parent_id, datetime.now(UTC))),
        _resp(
            {
                "id": parent_id,
                "branch_name": "feature/backend/PARENT01",
                "status": "in_progress",
                "assigned_to": "main-pm",
            }
        ),
    ]

    with (
        patch.object(orch, "_auto_block_task", new=AsyncMock()) as auto_block,
        patch(
            "roboco.runtime.orchestrator.asyncio.sleep", new=AsyncMock()
        ) as sleep_mock,
    ):
        result = await orch._check_parent_branch_ready(client, task_id, parent_id)

    assert result is None, "branch landed on retry; child must not be blocked"
    auto_block.assert_not_awaited()
    expected_get_calls = 2
    assert client.get.await_count == expected_get_calls
    sleep_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_retry_when_parent_not_mid_claim() -> None:
    """If the parent is NOT in mid-claim shape, retry is skipped and we
    auto-block immediately - preserves today's behavior for real misses."""
    task_id = str(uuid4())
    parent_id = str(uuid4())

    orch = _make_orch()

    client = AsyncMock()
    client.get.return_value = _resp(
        {
            "id": parent_id,
            "branch_name": None,
            "status": "pending",
            "assigned_to": None,
        }
    )

    with (
        patch.object(orch, "_auto_block_task", new=AsyncMock()) as auto_block,
        patch(
            "roboco.runtime.orchestrator.asyncio.sleep", new=AsyncMock()
        ) as sleep_mock,
    ):
        result = await orch._check_parent_branch_ready(client, task_id, parent_id)

    assert result is not None and "waiting for parent branch" in result
    auto_block.assert_awaited_once()
    client.get.assert_awaited_once()
    sleep_mock.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["claimed", "in_progress"])
async def test_within_grace_skips_without_auto_block(status: str) -> None:
    """Parent claimed 10s ago, still branchless after the fast retry: well
    inside parent_branch_provisioning_grace_seconds, so skip this tick
    instead of auto-blocking - the common, healthy path under slow branch
    creation. `claimed` is the raw POST /tasks/{id}/claim shape (CLAIMED
    commits before a separate start()), `in_progress` the composed one."""
    task_id = str(uuid4())
    parent_id = str(uuid4())

    orch = _make_orch()
    mid_claim = _mid_claim(
        parent_id, datetime.now(UTC) - timedelta(seconds=10), status=status
    )
    client = AsyncMock()
    client.get.return_value = _resp(mid_claim)

    with (
        patch.object(orch, "_auto_block_task", new=AsyncMock()) as auto_block,
        patch(
            "roboco.runtime.orchestrator.asyncio.sleep", new=AsyncMock()
        ) as sleep_mock,
    ):
        result = await orch._check_parent_branch_ready(client, task_id, parent_id)

    assert result is not None and "provisioning" in result
    auto_block.assert_not_awaited()
    expected_get_calls = 4  # 1 initial + 3 fast retries
    expected_sleep_calls = 3
    assert client.get.await_count == expected_get_calls
    assert sleep_mock.await_count == expected_sleep_calls


@pytest.mark.asyncio
async def test_past_grace_auto_blocks() -> None:
    """Parent claimed longer ago than the grace, still branchless after the
    fast retry: a genuinely stuck claim, auto-block as before."""
    task_id = str(uuid4())
    parent_id = str(uuid4())

    orch = _make_orch()
    grace = settings.parent_branch_provisioning_grace_seconds
    stale_claimed_at = datetime.now(UTC) - timedelta(seconds=grace + 60)
    mid_claim = _mid_claim(parent_id, stale_claimed_at)
    client = AsyncMock()
    client.get.return_value = _resp(mid_claim)

    with (
        patch.object(orch, "_auto_block_task", new=AsyncMock()) as auto_block,
        patch(
            "roboco.runtime.orchestrator.asyncio.sleep", new=AsyncMock()
        ) as sleep_mock,
    ):
        result = await orch._check_parent_branch_ready(client, task_id, parent_id)

    assert result is not None and "waiting for parent branch" in result
    auto_block.assert_awaited_once()
    expected_get_calls = 4  # 1 initial + 3 fast retries
    expected_sleep_calls = 3
    assert client.get.await_count == expected_get_calls
    assert sleep_mock.await_count == expected_sleep_calls


@pytest.mark.asyncio
async def test_grace_measured_in_fleet_active_time() -> None:
    """A parent claimed_at that is wall-clock older than the grace, but with
    an uptime ledger reporting little active time since (fleet was paused in
    between), must still read as within grace - a paused-fleet window never
    eats into the provisioning budget."""
    task_id = str(uuid4())
    parent_id = str(uuid4())

    orch = _make_orch()
    orch._uptime = cast("Any", _FakeLedger(timedelta(seconds=5)))
    grace = settings.parent_branch_provisioning_grace_seconds
    wall_clock_stale = datetime.now(UTC) - timedelta(seconds=grace + 3600)
    mid_claim = _mid_claim(parent_id, wall_clock_stale)
    client = AsyncMock()
    client.get.return_value = _resp(mid_claim)

    with (
        patch.object(orch, "_auto_block_task", new=AsyncMock()) as auto_block,
        patch(
            "roboco.runtime.orchestrator.asyncio.sleep", new=AsyncMock()
        ) as sleep_mock,
    ):
        result = await orch._check_parent_branch_ready(client, task_id, parent_id)

    assert result is not None and "provisioning" in result
    auto_block.assert_not_awaited()
    expected_sleep_calls = 3  # fast retry still runs before the grace check
    assert sleep_mock.await_count == expected_sleep_calls
