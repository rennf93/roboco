"""Gate Set C: exit-time guards in Choreographer.i_am_idle.

Pre-gateway behavior: an agent that signaled idle while owning unclaimed
pending work was implicitly stuck because the orchestrator's PM-closure
dispatcher would respawn them. The gateway makes this explicit:

- pending-parent guard: refuse i_am_idle if caller has any pending task
  assigned. They must call i_will_work_on / i_will_plan first.
- in_progress preserved: existing auto-pause for in_progress remains.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


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


@pytest.mark.asyncio
async def test_i_am_idle_refuses_when_pending_assignment_exists() -> None:
    """An agent with a pending (unclaimed) task assigned cannot exit."""
    agent_id = uuid4()
    pending_id = uuid4()
    pending = MagicMock(id=pending_id, status="pending")
    task_svc = AsyncMock()
    task_svc.list_assigned_for_agent.return_value = [pending]
    task_svc.list_in_progress_for_agent.return_value = []
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_am_idle(agent_id)
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert str(pending_id) in body["message"] or str(pending_id) in body["remediate"]
    assert "i_will_work_on" in body["remediate"] or "i_will_plan" in body["remediate"]
    task_svc.mark_agent_idle.assert_not_awaited()
    task_svc.pause_for_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_i_am_idle_lets_pm_through_with_pending_remediate() -> None:
    """The pending guard recommends i_will_plan when the agent is a PM."""
    agent_id = uuid4()
    pending_id = uuid4()
    pending = MagicMock(id=pending_id, status="pending")
    task_svc = AsyncMock()
    task_svc.list_assigned_for_agent.return_value = [pending]
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_am_idle(agent_id)
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "i_will_plan" in body["remediate"]


@pytest.mark.asyncio
async def test_i_am_idle_lets_dev_through_with_pending_remediate() -> None:
    """The pending guard recommends i_will_work_on for a developer."""
    agent_id = uuid4()
    pending_id = uuid4()
    pending = MagicMock(id=pending_id, status="pending")
    task_svc = AsyncMock()
    task_svc.list_assigned_for_agent.return_value = [pending]
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.agent_for.return_value = MagicMock(role="developer", team="backend")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_am_idle(agent_id)
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "i_will_work_on" in body["remediate"]


@pytest.mark.asyncio
async def test_i_am_idle_ignores_non_pending_assigned_tasks() -> None:
    """Other active states (claimed/in_progress/etc) don't trigger pending guard."""
    agent_id = uuid4()
    in_progress = MagicMock(id=uuid4(), status="in_progress")
    task_svc = AsyncMock()
    # Even though list_assigned_for_agent includes in_progress, only pending
    # triggers the guard.
    task_svc.list_assigned_for_agent.return_value = [in_progress]
    task_svc.list_in_progress_for_agent.return_value = [in_progress]
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_am_idle(agent_id)
    body = env.as_dict()
    assert body["error"] is None
    # The auto-pause path should still fire for in_progress.
    task_svc.pause_for_agent.assert_awaited_once_with(agent_id, in_progress.id)
    task_svc.mark_agent_idle.assert_awaited_once()


@pytest.mark.asyncio
async def test_i_am_idle_with_no_assigned_tasks_proceeds_normally() -> None:
    """Empty assignment list: no pending guard, no auto-pause, idle goes through."""
    agent_id = uuid4()
    task_svc = AsyncMock()
    task_svc.list_assigned_for_agent.return_value = []
    task_svc.list_in_progress_for_agent.return_value = []
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_am_idle(agent_id)
    body = env.as_dict()
    assert body["error"] is None
    assert body["status"] == "idle"
    task_svc.pause_for_agent.assert_not_awaited()
    task_svc.mark_agent_idle.assert_awaited_once()


@pytest.mark.asyncio
async def test_i_am_idle_pending_guard_runs_after_unread_check() -> None:
    """Unread A2A check still wins; pending guard only runs after."""
    agent_id = uuid4()
    pending = MagicMock(id=uuid4(), status="pending")
    task_svc = AsyncMock()
    task_svc.list_assigned_for_agent.return_value = [pending]
    deps = _make_deps(task=task_svc)
    deps.evidence_repo.list_unread_a2a.return_value = ["mention"]
    c = Choreographer(deps)

    env = await c.i_am_idle(agent_id)
    body = env.as_dict()
    # Unread takes precedence and returns ok+idle_with_unread, NOT invalid_state
    assert body["error"] is None
    assert body["status"] == "idle_with_unread"
    task_svc.mark_agent_idle.assert_not_awaited()
