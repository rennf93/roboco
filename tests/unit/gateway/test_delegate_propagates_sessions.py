"""Task #156: delegate() must thread parent sessions onto the new subtask.

Pre-gateway flow created sessions for whole task trees at once, so subtasks
were visible in the group chat the PM was already using. The gateway's
delegate() creates subtasks one-by-one — without this step the new agent
spawns into an empty channel and can't see the PM's prior discussion.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps
from roboco.services.gateway.choreographer._impl import DelegateInputs


def _make_deps(**overrides: Any) -> ChoreographerDeps:
    base: dict[str, Any] = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
        "messaging": AsyncMock(),
    }
    base.update(overrides)
    task = base["task"]
    task.session = MagicMock()
    task.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    repo = base["evidence_repo"]
    for method in (
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
        "journal_highlights_for_task",
    ):
        getattr(repo, method).return_value = []
    _ldef = base["journal"].latest_decision_at.return_value
    if type(_ldef).__name__ in ("MagicMock", "AsyncMock"):
        base["journal"].latest_decision_at.return_value = datetime.now(UTC)
    return ChoreographerDeps(**base)


@pytest.mark.asyncio
async def test_create_subtask_propagates_parent_sessions() -> None:
    """When the choreographer creates a subtask, the parent's session
    links are auto-attached to it via MessagingService.propagate_sessions_to_subtask.
    """
    pm_id = uuid4()
    parent_id = uuid4()
    new_task_id = uuid4()
    parent = MagicMock(
        id=parent_id,
        project_id=uuid4(),
        team="backend",
        status="in_progress",
        task_type="planning",
        sequence=0,
        assigned_to=pm_id,
    )
    new_task = MagicMock(id=new_task_id, status="pending")
    task_svc = AsyncMock()
    task_svc.create_subtask.return_value = new_task
    messaging = AsyncMock()
    messaging.propagate_sessions_to_subtask.return_value = []
    deps = _make_deps(task=task_svc, messaging=messaging)
    c = Choreographer(deps)

    inputs = DelegateInputs(
        title="Backend slice",
        description="API + DB",
        acceptance_criteria=["api works", "schema migrated"],
        assigned_to="be-dev-1",
        team="backend",
        task_type="code",
        nature="technical",
        estimated_complexity="medium",
    )
    result = await c._create_subtask_from_inputs(pm_id, parent_id, parent, inputs)
    assert result is new_task

    messaging.propagate_sessions_to_subtask.assert_awaited_once_with(
        parent_task_id=parent_id,
        subtask_id=new_task_id,
        added_by=pm_id,
    )


@pytest.mark.asyncio
async def test_create_subtask_no_messaging_skips_propagation() -> None:
    """When messaging dep is None (e.g. lightweight test wiring), the
    subtask is still created — propagation is a soft enhancement, not a
    hard requirement."""
    pm_id = uuid4()
    parent_id = uuid4()
    new_task_id = uuid4()
    parent = MagicMock(
        id=parent_id,
        project_id=uuid4(),
        team="backend",
        status="in_progress",
        task_type="planning",
        sequence=0,
        assigned_to=pm_id,
    )
    new_task = MagicMock(id=new_task_id, status="pending")
    task_svc = AsyncMock()
    task_svc.create_subtask.return_value = new_task
    deps = _make_deps(task=task_svc, messaging=None)
    c = Choreographer(deps)

    inputs = DelegateInputs(
        title="Backend slice",
        description="API + DB",
        acceptance_criteria=["api works"],
        assigned_to="be-dev-1",
        team="backend",
        task_type="code",
        nature="technical",
        estimated_complexity="medium",
    )
    # Must not raise even though messaging is None.
    result = await c._create_subtask_from_inputs(pm_id, parent_id, parent, inputs)
    assert result is new_task
