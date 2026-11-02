"""The dispatcher releases a claim without removing its worktree.

``_release_claim_to_pending`` (a work author's second never-started claim, an
agentless claim with an unknown assignee) went through
``TaskService.unclaim_for_reaper``, whose ``git worktree remove --force``
deletes the owner's uncommitted edits and untracked files. The owner stays on
the task and comes back to it, so the release must leave the worktree alone.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator

_TASK_ID = "106f46e3-ad66-4a32-a546-3ba53c6ad0f3"


@pytest.mark.asyncio
async def test_release_goes_through_the_worktree_keeping_path() -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    db = MagicMock()
    db.commit = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db)
    ctx.__aexit__ = AsyncMock(return_value=False)
    keep = AsyncMock(return_value=True)
    reap = AsyncMock()

    with (
        patch(
            "roboco.db.base.get_session_factory",
            return_value=MagicMock(return_value=ctx),
        ),
        patch(
            "roboco.services.task.TaskService.release_claim_keeping_worktree",
            keep,
            create=True,
        ),
        patch("roboco.services.task.TaskService.unclaim_for_reaper", reap),
    ):
        await orch._release_claim_to_pending(_TASK_ID)

    keep.assert_awaited_once_with(UUID(_TASK_ID))
    reap.assert_not_awaited()
    db.commit.assert_awaited_once()
