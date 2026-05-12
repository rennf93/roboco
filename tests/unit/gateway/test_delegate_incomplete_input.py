"""Gateway delegate must return Envelope.incomplete_input when fields missing.

Pre-migration: services/gateway/choreographer/_impl.py:1852 used
``acceptance_criteria=inputs.acceptance_criteria or []`` which let ``[]``
through and was caught later (or, before that fix, silently substituted
by services/task.py:5061). Now the gateway rejects at the boundary
with ``Envelope.incomplete_input``, so the agent receives a structured
field-by-field guide (the spec §5.2.1 interrogation pattern).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
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


def _parent_in_progress(pm_id: Any) -> MagicMock:
    return MagicMock(
        id=uuid4(),
        project_id=uuid4(),
        status="in_progress",
        assigned_to=pm_id,
        priority=2,
    )


@pytest.mark.asyncio
async def test_delegate_returns_incomplete_input_when_acceptance_criteria_empty() -> (
    None
):
    """Empty acceptance_criteria triggers the incomplete_input envelope."""
    pm_id = uuid4()
    parent = _parent_in_progress(pm_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = parent
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.get_subtasks.return_value = []
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.delegate(
        pm_id,
        parent.id,
        DelegateInputs(
            title="Implement endpoint",
            description="Add /v1/foo endpoint with passing tests please",
            assigned_to="be-dev-1",
            team="backend",
            task_type="code",
            nature="technical",
            acceptance_criteria=[],  # under-filled — must trigger interrogation
        ),
    )
    body = env.as_dict()
    assert body["error"] == "incomplete_input", body
    assert "acceptance_criteria" in body["missing"]
    assert "acceptance_criteria" in body["field_hints"]
    assert "verifiable" in body["field_hints"]["acceptance_criteria"].lower()
    task_svc.create_subtask.assert_not_awaited()


@pytest.mark.asyncio
async def test_delegate_returns_incomplete_input_when_acceptance_criteria_none() -> (
    None
):
    """None acceptance_criteria (missing field) triggers the same envelope."""
    pm_id = uuid4()
    parent = _parent_in_progress(pm_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = parent
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.get_subtasks.return_value = []
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.delegate(
        pm_id,
        parent.id,
        DelegateInputs(
            title="Implement endpoint",
            description="Add /v1/foo endpoint with passing tests please",
            assigned_to="be-dev-1",
            team="backend",
            task_type="code",
            nature="technical",
            acceptance_criteria=None,
        ),
    )
    body = env.as_dict()
    assert body["error"] == "incomplete_input", body
    assert "acceptance_criteria" in body["missing"]
    task_svc.create_subtask.assert_not_awaited()


@pytest.mark.asyncio
async def test_delegate_rejects_silent_fallback_phrase() -> None:
    """The denylist catches the legacy 'completed and reviewed by assignee' string."""
    pm_id = uuid4()
    parent = _parent_in_progress(pm_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = parent
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.get_subtasks.return_value = []
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.delegate(
        pm_id,
        parent.id,
        DelegateInputs(
            title="Implement endpoint",
            description="Add /v1/foo endpoint with passing tests please",
            assigned_to="be-dev-1",
            team="backend",
            task_type="code",
            nature="technical",
            acceptance_criteria=["completed and reviewed by assignee"],
        ),
    )
    body = env.as_dict()
    assert body["error"] == "incomplete_input", body
    assert "acceptance_criteria" in body["missing"]
    # The denylist hint should mention the placeholder/legacy fallback.
    hint = body["field_hints"]["acceptance_criteria"].lower()
    assert "placeholder" in hint or "legacy" in hint or "rejected" in hint
    task_svc.create_subtask.assert_not_awaited()


@pytest.mark.asyncio
async def test_delegate_returns_incomplete_input_when_nature_missing() -> None:
    """Missing nature is also caught by task_completeness check."""
    pm_id = uuid4()
    parent = _parent_in_progress(pm_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = parent
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.get_subtasks.return_value = []
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.delegate(
        pm_id,
        parent.id,
        DelegateInputs(
            title="Implement endpoint",
            description="Add /v1/foo endpoint with passing tests please",
            assigned_to="be-dev-1",
            team="backend",
            task_type="code",
            nature=None,  # not declared
            acceptance_criteria=["GET /v1/foo returns 200 with body"],
        ),
    )
    body = env.as_dict()
    assert body["error"] == "incomplete_input", body
    assert "nature" in body["missing"]
    task_svc.create_subtask.assert_not_awaited()


@pytest.mark.asyncio
async def test_delegate_passes_when_payload_complete() -> None:
    """Fully-populated payload still creates the subtask (no regression)."""
    pm_id = uuid4()
    parent = _parent_in_progress(pm_id)
    new_task = MagicMock(id=uuid4())
    task_svc = AsyncMock()
    task_svc.get.return_value = parent
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.get_subtasks.return_value = []
    task_svc.create_subtask.return_value = new_task
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.delegate(
        pm_id,
        parent.id,
        DelegateInputs(
            title="Implement endpoint",
            description="Add /v1/foo endpoint with passing tests please",
            assigned_to="be-dev-1",
            team="backend",
            task_type="code",
            nature="technical",
            acceptance_criteria=["GET /v1/foo returns 200 with body"],
        ),
    )
    assert env.error is None, env.as_dict()
    task_svc.create_subtask.assert_awaited_once()
    # Verify nature threaded through to TaskCreateRequest.
    req = task_svc.create_subtask.call_args.args[0]
    assert str(req.nature) == "technical" or req.nature.value == "technical"
