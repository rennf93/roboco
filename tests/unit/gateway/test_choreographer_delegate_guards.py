"""Gate Set B: delegation-time guards in Choreographer.delegate.

Pre-gateway behavior: a PM could only call ``task_create`` while spawned
in-context, which only happened after the orchestrator saw them claim
and start a parent task. That implicit gate is restored explicitly here:

- PARENT_NOT_CLAIMED: parent must be in_progress AND assigned_to PM.
- SUBTASK_CAP: 8 children = soft warn (allowed), >12 = hard block.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.base import ValidationError
from roboco.services.gateway.choreographer import (
    Choreographer,
    ChoreographerDeps,
    DelegateInputs,
)


def _make_deps(**overrides: Any) -> ChoreographerDeps:
    base: dict[str, Any] = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    base.update(overrides)
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
    # C8: default-fresh journal:decision so PM-decision gate passes.
    # Tests that exercise the gate boundary stub their own value.
    # The check matches MagicMock and AsyncMock (the two default sentinel
    # types pytest's unittest.mock leaves on un-stubbed return_values).
    _ldef = base["journal"].latest_decision_at.return_value
    if type(_ldef).__name__ in ("MagicMock", "AsyncMock"):
        base["journal"].latest_decision_at.return_value = datetime.now(UTC)
    return ChoreographerDeps(**base)


def _delegate_inputs() -> DelegateInputs:
    return DelegateInputs(
        title="Implement endpoint",
        description="Add /v1/foo endpoint with tests",
        assigned_to="be-dev-1",
        team="backend",
        task_type="code",
        nature="technical",
        acceptance_criteria=["GET /v1/foo returns 200 with body"],
    )


@pytest.mark.asyncio
async def test_delegate_blocks_when_parent_not_in_progress() -> None:
    """Parent in 'pending' status (PM never called i_will_plan) blocks delegate."""
    pm_id = uuid4()
    parent_id = uuid4()
    parent = MagicMock(
        id=parent_id,
        project_id=uuid4(),
        status="pending",
        assigned_to=pm_id,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = parent
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.get_subtasks.return_value = []
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.delegate(pm_id, parent_id, _delegate_inputs())
    body = env.as_dict()
    # The spec's create_subtask gate requires the parent in_progress.
    # Per Section 6 of the design doc, rejection messages now come from
    # the spec — the spec rejects with 'invalid_state' citing the
    # source-status set, not the gateway-specific i_will_plan hint.
    assert body["error"] == "invalid_state"
    assert "in_progress" in body["message"]
    task_svc.create_subtask.assert_not_awaited()


@pytest.mark.asyncio
async def test_delegate_blocks_when_parent_assigned_to_other_agent() -> None:
    """Parent claimed by a different PM cannot be delegated against by us."""
    pm_id = uuid4()
    other_pm_id = uuid4()
    parent_id = uuid4()
    parent = MagicMock(
        id=parent_id,
        project_id=uuid4(),
        status="in_progress",
        assigned_to=other_pm_id,
        quick_context="Decomposition planned; cells implement their slice next.",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = parent
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.get_subtasks.return_value = []
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.delegate(pm_id, parent_id, _delegate_inputs())
    body = env.as_dict()
    assert body["error"] == "not_authorized"
    assert "i_will_plan" in body["remediate"]
    task_svc.create_subtask.assert_not_awaited()


@pytest.mark.asyncio
async def test_delegate_allows_when_parent_in_progress_and_owned() -> None:
    pm_id = uuid4()
    parent_id = uuid4()
    parent = MagicMock(
        id=parent_id,
        project_id=uuid4(),
        status="in_progress",
        assigned_to=pm_id,
        quick_context="Decomposition planned; cells implement their slice next.",
    )
    new_task = MagicMock(id=uuid4())
    task_svc = AsyncMock()
    task_svc.get.return_value = parent
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.get_subtasks.return_value = []
    task_svc.create_subtask.return_value = new_task
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.delegate(pm_id, parent_id, _delegate_inputs())
    body = env.as_dict()
    assert body["error"] is None
    assert body["status"] == "created"
    task_svc.create_subtask.assert_awaited_once()


@pytest.mark.asyncio
async def test_delegate_blocks_when_subtask_cap_exceeded() -> None:
    """13+ subtasks = hard block (cap is 12)."""
    pm_id = uuid4()
    parent_id = uuid4()
    parent = MagicMock(
        id=parent_id,
        project_id=uuid4(),
        status="in_progress",
        assigned_to=pm_id,
        quick_context="Decomposition planned; cells implement their slice next.",
    )
    too_many = [MagicMock(id=uuid4()) for _ in range(13)]
    task_svc = AsyncMock()
    task_svc.get.return_value = parent
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.get_subtasks.return_value = too_many
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.delegate(pm_id, parent_id, _delegate_inputs())
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "13" in body["message"] or "consolidate" in body["remediate"].lower()
    task_svc.create_subtask.assert_not_awaited()


@pytest.mark.asyncio
async def test_delegate_allows_when_subtask_cap_within_soft_zone() -> None:
    """8-12 subtasks: warn-but-allow."""
    pm_id = uuid4()
    parent_id = uuid4()
    parent = MagicMock(
        id=parent_id,
        project_id=uuid4(),
        status="in_progress",
        assigned_to=pm_id,
        quick_context="Decomposition planned; cells implement their slice next.",
    )
    many = [MagicMock(id=uuid4()) for _ in range(10)]
    new_task = MagicMock(id=uuid4())
    task_svc = AsyncMock()
    task_svc.get.return_value = parent
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.get_subtasks.return_value = many
    task_svc.create_subtask.return_value = new_task
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.delegate(pm_id, parent_id, _delegate_inputs())
    body = env.as_dict()
    assert body["error"] is None
    task_svc.create_subtask.assert_awaited_once()


@pytest.mark.asyncio
async def test_delegate_allows_at_zero_subtasks() -> None:
    """Empty subtask list is the common case."""
    pm_id = uuid4()
    parent_id = uuid4()
    parent = MagicMock(
        id=parent_id,
        project_id=uuid4(),
        status="in_progress",
        assigned_to=pm_id,
        quick_context="Decomposition planned; cells implement their slice next.",
    )
    new_task = MagicMock(id=uuid4())
    task_svc = AsyncMock()
    task_svc.get.return_value = parent
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.get_subtasks.return_value = []
    task_svc.create_subtask.return_value = new_task
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.delegate(pm_id, parent_id, _delegate_inputs())
    body = env.as_dict()
    assert body["error"] is None


@pytest.mark.asyncio
async def test_delegate_blocks_at_exact_cap_plus_one() -> None:
    """Cap is 12; 13th attempt blocks."""
    pm_id = uuid4()
    parent_id = uuid4()
    parent = MagicMock(
        id=parent_id,
        project_id=uuid4(),
        status="in_progress",
        assigned_to=pm_id,
        quick_context="Decomposition planned; cells implement their slice next.",
    )
    # Already 12 children — adding the 13th must be blocked.
    twelve = [MagicMock(id=uuid4()) for _ in range(12)]
    task_svc = AsyncMock()
    task_svc.get.return_value = parent
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.get_subtasks.return_value = twelve
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.delegate(pm_id, parent_id, _delegate_inputs())
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    task_svc.create_subtask.assert_not_awaited()


@pytest.mark.asyncio
async def test_delegate_blocks_when_parent_quick_context_empty() -> None:
    """delegate obligates the PM's quick_context resumption section on the
    parent; an empty quick_context (PM never called note(scope='handoff'))
    fails the tracing gate before any subtask is created."""
    pm_id = uuid4()
    parent_id = uuid4()
    parent = MagicMock(
        id=parent_id,
        project_id=uuid4(),
        status="in_progress",
        assigned_to=pm_id,
        quick_context="",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = parent
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.get_subtasks.return_value = []
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.delegate(pm_id, parent_id, _delegate_inputs())
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "quick_context>=min" in body["missing"]
    task_svc.create_subtask.assert_not_awaited()


@pytest.mark.asyncio
async def test_delegate_past_max_depth_returns_invalid_state_not_500() -> None:
    """Bug B: delegating past MAX_TASK_DEPTH raised a bare ValueError that
    escaped uncaught as a 500 ExceptionGroup. ``_validate_parent_depth`` now
    raises ``ValidationError`` (a ServiceError), and the choreographer
    translates it to an ``invalid_state`` envelope whose remediate carries the
    "create as a sibling" instruction — so the PM gets a clean, actionable
    rejection instead of a crash, and the agent loop doesn't respawn-blind.
    """
    pm_id = uuid4()
    parent_id = uuid4()
    parent = MagicMock(
        id=parent_id,
        project_id=uuid4(),
        status="in_progress",
        assigned_to=pm_id,
        quick_context="Decomposition planned; cells implement their slice next.",
    )
    depth_msg = (
        "Task hierarchy would exceed MAX_TASK_DEPTH=4. Create this work as a "
        "sibling of the deepest task instead of a further nested subtask."
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = parent
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.get_subtasks.return_value = []
    # create_subtask -> create -> _validate_parent_depth raises ValidationError.
    task_svc.create_subtask.side_effect = ValidationError(
        depth_msg, field="parent_task_id"
    )
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.delegate(pm_id, parent_id, _delegate_inputs())
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "MAX_TASK_DEPTH" in body["message"]
    # The remediation must reach the agent — the whole point of the fix.
    assert "sibling" in body["remediate"]
    task_svc.create_subtask.assert_awaited_once()
