"""Dispatcher gives the parent's claim transaction a moment to commit before
auto-blocking a child task on a missing parent branch.

Race scenario: PM's `i_will_plan` and a child dev's spawn dispatch fire in the
same tick. `i_will_plan` claims the parent (transitions to `in_progress`,
populates `assigned_to`, then creates the parent branch via
`_finalize_claim -> _ensure_branch_for_task`). Until that transaction commits,
the dispatcher's `_check_parent_branch_ready` sees `branch_name=None` even
though the branch is microseconds away from landing.

Without retry the dispatcher auto-blocks the child immediately and the dev
sits idle until the next 30s dispatch tick. With a tight 3x250ms retry — only
when the parent is mid-claim (`status=in_progress` AND `assigned_to` set) — we
absorb the sub-second commit gap without delaying the legitimate-block path.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
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


@pytest.mark.asyncio
async def test_retries_when_parent_is_mid_claim_and_branch_lands() -> None:
    """First fetch sees null branch (PM transaction in flight); second fetch
    sees the committed branch. We must NOT auto-block in this race."""
    task_id = str(uuid4())
    parent_id = str(uuid4())

    orch = _make_orch()

    client = AsyncMock()
    client.get.side_effect = [
        _resp(
            {
                "id": parent_id,
                "branch_name": None,
                "status": "in_progress",
                "assigned_to": "main-pm",
            }
        ),
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
    auto-block immediately — preserves today's behavior for real misses."""
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
async def test_blocks_after_max_retries_exhausted() -> None:
    """If the parent stays in mid-claim shape with null branch through every
    retry, give up and auto-block. 3 retries -> 4 total fetches."""
    task_id = str(uuid4())
    parent_id = str(uuid4())

    orch = _make_orch()

    mid_claim = {
        "id": parent_id,
        "branch_name": None,
        "status": "in_progress",
        "assigned_to": "main-pm",
    }
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
    expected_get_calls = 4  # 1 initial + 3 retries
    expected_sleep_calls = 3
    assert client.get.await_count == expected_get_calls
    assert sleep_mock.await_count == expected_sleep_calls
