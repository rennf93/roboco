"""`_detect_stuck_tasks` must skip CEO-held sources (release_manager / x_post /
x_reply / video_post / PR-review / self-heal-pre-approve) before the age and
issues check. A held artifact sits PENDING by design until the CEO acts on it;
auto-blocking it on a "short description" wedges the held-artifact flow.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator
from roboco.services.task import VIDEO_POST_SOURCE


def _make_orch() -> AgentOrchestrator:
    """Bare orchestrator without running __init__ (no settings I/O)."""
    return AgentOrchestrator.__new__(AgentOrchestrator)


def _old_task(tid: str, source: str) -> dict[str, Any]:
    """A PENDING task older than the 10-minute stuck threshold with a short
    description — exactly the shape that triggers _auto_block_task today."""
    created = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
    return {
        "id": tid,
        "source": source,
        "status": "pending",
        "description": "short",  # < _MIN_DESCRIPTION_LEN (10)
        "branch_name": None,
        "assigned_to": None,
        "estimated_complexity": "low",
        "parent_task_id": None,
        "created_at": created,
    }


@pytest.mark.asyncio
async def test_held_video_post_skipped_by_detect_stuck_tasks() -> None:
    """A PENDING video_post draft older than the threshold with a short
    description must NOT be auto-blocked — it is a CEO-held artifact."""
    orch = _make_orch()
    held = _old_task("held-1", VIDEO_POST_SOURCE)
    client: Any = MagicMock()

    with (
        patch.object(orch, "_fetch_tasks", new=AsyncMock(return_value=[held])),
        patch.object(orch, "_check_dev_subtask_issue", new=AsyncMock(return_value=[])),
        patch.object(orch, "_detect_sla_exceeded", new=AsyncMock()),
        patch.object(orch, "_auto_block_task", new=AsyncMock()) as auto_block,
    ):
        await orch._detect_stuck_tasks(client)

    auto_block.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_held_pending_task_still_auto_blocked() -> None:
    """A non-held PENDING task of the same age + short description is still
    auto-blocked — the held-source skip must not over-widen."""
    orch = _make_orch()
    normal = _old_task("normal-1", "manual")
    client: Any = MagicMock()

    with (
        patch.object(orch, "_fetch_tasks", new=AsyncMock(return_value=[normal])),
        patch.object(orch, "_check_dev_subtask_issue", new=AsyncMock(return_value=[])),
        patch.object(orch, "_detect_sla_exceeded", new=AsyncMock()),
        patch.object(orch, "_auto_block_task", new=AsyncMock()) as auto_block,
    ):
        await orch._detect_stuck_tasks(client)

    auto_block.assert_awaited_once()
    assert auto_block.await_args is not None
    assert auto_block.await_args.args[1] == "normal-1"


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
