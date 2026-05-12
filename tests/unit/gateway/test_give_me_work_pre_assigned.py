"""Wave B6 (2026-05-12): give_me_work returns tasks pre-assigned to the agent.

Smoke run 3 showed Main PM's first give_me_work() returning idle even
though c7935d2c was pending and assigned to Main PM. The filter
missed the pre-assigned case — list_assigned_for_agent ordered by
priority/updated_at and could rank pending below in_progress tasks;
also, pm_give_me_work fell through to idle if all assigned tasks were
pending and not yet distinguished from the triage queue.

Pre-assigned pending tasks must be returned FIRST by pm_give_me_work
(and give_me_work for developer/QA/doc roles).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_deps(**overrides: Any) -> ChoreographerDeps:
    base = {
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
async def test_pm_give_me_work_returns_pre_assigned_pending_main_pm() -> None:
    """Main PM has a pending task assigned to them: pm_give_me_work returns it.

    Smoke run 3: task c7935d2c was pending + assigned_to=main-pm but
    pm_give_me_work returned {status: idle, next: 'no Main PM work'}.
    """
    pm_id = uuid4()
    task_id = uuid4()
    pending_task = MagicMock(
        id=task_id,
        status="pending",
        assigned_to=pm_id,
        task_type="planning",
        title="Main PM root task — pre-assigned pending",
        parent_task_id=None,
        sequence=0,
        priority=1,
    )

    task_svc = AsyncMock()
    task_svc.list_pending_for_agent.return_value = [pending_task]
    task_svc.list_assigned_for_agent.return_value = []

    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.pm_give_me_work(pm_id)
    body = env.as_dict()

    assert body["error"] is None, f"Expected no error, got: {body.get('error')}"
    assert body["task_id"] == str(task_id), (
        f"Expected task {task_id}, got {body.get('task_id')} — "
        "pm_give_me_work did not return the pre-assigned pending task"
    )
    assert body["status"] == "pending"
    assert "i_will_plan" in body["next"], (
        f"Expected i_will_plan hint for PM pending task, got: {body.get('next')}"
    )


@pytest.mark.asyncio
async def test_pm_give_me_work_returns_pre_assigned_pending_cell_pm() -> None:
    """Cell PM has a pending task assigned to them: pm_give_me_work returns it."""
    pm_id = uuid4()
    task_id = uuid4()
    pending_task = MagicMock(
        id=task_id,
        status="pending",
        assigned_to=pm_id,
        task_type="planning",
        title="Cell PM task — pre-assigned pending",
        parent_task_id=None,
        sequence=0,
        priority=1,
    )

    task_svc = AsyncMock()
    task_svc.list_pending_for_agent.return_value = [pending_task]
    task_svc.list_assigned_for_agent.return_value = []

    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.pm_give_me_work(pm_id)
    body = env.as_dict()

    assert body["error"] is None
    assert body["task_id"] == str(task_id)
    assert body["status"] == "pending"


@pytest.mark.asyncio
async def test_pm_give_me_work_pending_beats_other_assigned() -> None:
    """Pre-assigned pending takes priority over other non-pending assigned tasks."""
    pm_id = uuid4()
    pending_id = uuid4()
    other_id = uuid4()

    pending_task = MagicMock(
        id=pending_id,
        status="pending",
        assigned_to=pm_id,
        task_type="planning",
        title="Pre-assigned pending — should win",
        parent_task_id=None,
        sequence=0,
        priority=5,
    )
    other_task = MagicMock(
        id=other_id,
        status="awaiting_pm_review",
        assigned_to=pm_id,
        task_type="planning",
        title="Awaiting review",
        parent_task_id=None,
        sequence=0,
        priority=1,
    )

    task_svc = AsyncMock()
    task_svc.list_pending_for_agent.return_value = [pending_task]
    task_svc.list_assigned_for_agent.return_value = [other_task]

    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.pm_give_me_work(pm_id)
    body = env.as_dict()

    assert body["task_id"] == str(pending_id), (
        f"Pre-assigned pending task should beat awaiting_pm_review; "
        f"got {body.get('task_id')}"
    )


@pytest.mark.asyncio
async def test_give_me_work_returns_pre_assigned_pending_developer() -> None:
    """Developer has a pending task assigned: give_me_work returns it first."""
    dev_id = uuid4()
    task_id = uuid4()
    pending_task = MagicMock(
        id=task_id,
        status="pending",
        assigned_to=dev_id,
        task_type="code",
        title="Dev task — pre-assigned pending",
        parent_task_id=None,
        sequence=0,
        priority=1,
    )

    task_svc = AsyncMock()
    task_svc.list_pending_for_agent.return_value = [pending_task]
    task_svc.list_assigned_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.agent_for.return_value = MagicMock(role="developer")

    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.give_me_work(dev_id)
    body = env.as_dict()

    assert body["error"] is None
    assert body["task_id"] == str(task_id)
    assert body["status"] == "pending"
    assert "i_will_work_on" in body["next"]


@pytest.mark.asyncio
async def test_pm_give_me_work_falls_through_to_assigned_when_no_pending() -> None:
    """When no pre-assigned pending tasks, pm_give_me_work still checks assigned."""
    pm_id = uuid4()
    assigned_id = uuid4()
    assigned_task = MagicMock(
        id=assigned_id,
        status="awaiting_pm_review",
        assigned_to=pm_id,
        task_type="planning",
        title="Awaiting PM review task",
        parent_task_id=None,
        sequence=0,
        priority=1,
    )

    task_svc = AsyncMock()
    task_svc.list_pending_for_agent.return_value = []
    task_svc.list_assigned_for_agent.return_value = [assigned_task]

    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.pm_give_me_work(pm_id)
    body = env.as_dict()

    assert body["task_id"] == str(assigned_id)
    assert body["status"] == "awaiting_pm_review"


@pytest.mark.asyncio
async def test_pm_give_me_work_idle_when_no_pre_assigned_and_no_assigned() -> None:
    """When no pending and no assigned tasks, pm_give_me_work returns idle."""
    pm_id = uuid4()
    task_svc = AsyncMock()
    task_svc.list_pending_for_agent.return_value = []
    task_svc.list_assigned_for_agent.return_value = []

    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.pm_give_me_work(pm_id)
    body = env.as_dict()

    assert body["status"] == "idle"
    assert body["task_id"] is None
