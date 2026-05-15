"""Task #155: server-side auto-progress on lifecycle milestones.

Smoke-9 ended with zero progress entries because the dev never called
progress() explicitly. The fix: server emits progress entries at
deterministic lifecycle milestones (open_pr → "opened PR #N",
i_am_done → "submitted for QA review") so the panel + audit have entries
regardless of agent chattiness. Progress is observability — write failures
must not break the verb path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


@pytest.mark.asyncio
async def test_record_milestone_progress_calls_add_progress() -> None:
    """The helper proxies to TaskService.add_progress with the right args."""
    task_svc = AsyncMock()
    deps = ChoreographerDeps(
        task=task_svc,
        work_session=AsyncMock(),
        git=AsyncMock(),
        a2a=AsyncMock(),
        journal=AsyncMock(),
        audit=AsyncMock(),
        evidence_repo=AsyncMock(),
        messaging=AsyncMock(),
    )
    c = Choreographer(deps)

    task_id = uuid4()
    agent_id = uuid4()
    await c._record_milestone_progress(task_id, agent_id, "opened PR #20", 70)

    task_svc.add_progress.assert_awaited_once_with(
        task_id=task_id,
        agent_id=agent_id,
        message="opened PR #20",
        percentage=70,
    )


@pytest.mark.asyncio
async def test_record_milestone_progress_swallows_errors() -> None:
    """Progress is observability. A failing add_progress must not raise
    so the verb body's main flow is unaffected."""
    task_svc = AsyncMock()
    task_svc.add_progress.side_effect = RuntimeError("db lock contention")
    deps = ChoreographerDeps(
        task=task_svc,
        work_session=AsyncMock(),
        git=AsyncMock(),
        a2a=AsyncMock(),
        journal=AsyncMock(),
        audit=AsyncMock(),
        evidence_repo=AsyncMock(),
        messaging=AsyncMock(),
    )
    c = Choreographer(deps)

    # Must NOT raise.
    await c._record_milestone_progress(uuid4(), uuid4(), "submitted for QA review", 90)
    task_svc.add_progress.assert_awaited_once()
