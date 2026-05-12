"""Pin: Choreographer must call task.claim/start with (task_id, agent_id).

Service signatures in ``roboco/services/task.py`` are:

    async def claim(self, task_id: UUID, agent_id: UUID, ...) -> TaskTable | None
    async def start(self, task_id: UUID, agent_id: UUID | None = None, ...) -> ...

Earlier choreographer code passed (agent_id, task_id) — when the SQL lookup
ran ``WHERE id = <agent_uuid>``, no row matched and the call returned None,
which the choreographer interpreted as "task unchanged". The whole gateway
claim path was non-functional against a real DB. Existing unit tests pinned
the buggy order so the bug stayed invisible.

This test pins the correct order at every Choreographer call site that
forwards into the service.
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
    # VerbRunner uses task.session.begin_nested() as a savepoint context
    # manager. AsyncMock auto-attributes any access (so hasattr always
    # returns True); we always overwrite session to a MagicMock with the
    # correct async-context-manager protocol.
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
    # C8: default-fresh journal:decision so PM-decision gate passes.
    # Tests that exercise the gate boundary stub their own value.
    # The check matches MagicMock and AsyncMock (the two default sentinel
    # types pytest's unittest.mock leaves on un-stubbed return_values).
    _ldef = base["journal"].latest_decision_at.return_value
    if type(_ldef).__name__ in ("MagicMock", "AsyncMock"):
        base["journal"].latest_decision_at.return_value = datetime.now(UTC)
    return ChoreographerDeps(**base)


@pytest.mark.asyncio
async def test_i_will_work_on_pending_calls_claim_with_task_id_first() -> None:
    """Dev pending claim: positional args must be (task_id, agent_id)."""
    agent_id = uuid4()
    task_id = uuid4()
    pending = MagicMock(
        id=task_id,
        status="pending",
        plan=None,
        assigned_to=None,
        task_type="code",
        parent_task_id=None,
        sequence=0,
        team="backend",
    )
    claimed = MagicMock(
        id=task_id,
        status="claimed",
        plan=None,
        assigned_to=agent_id,
        task_type="code",
    )
    with_plan = MagicMock(
        id=task_id,
        status="claimed",
        plan={"text": "x"},
        assigned_to=agent_id,
        task_type="code",
    )
    started = MagicMock(
        id=task_id,
        status="in_progress",
        plan={"text": "x"},
        assigned_to=agent_id,
        task_type="code",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = pending
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role="developer", team="backend", slug=None
    )
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.get_subtasks.return_value = []
    task_svc.claim.return_value = claimed
    task_svc.set_plan.return_value = with_plan
    task_svc.start.return_value = started
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_work_on(agent_id, task_id, plan="x")

    # Service signature is (task_id, agent_id, ...) — pin that order.
    task_svc.claim.assert_awaited_once_with(task_id, agent_id)
    task_svc.start.assert_awaited_once_with(task_id, agent_id)
    assert env.error is None


@pytest.mark.asyncio
async def test_i_will_work_on_needs_revision_calls_start_with_task_id_first() -> None:
    """Dev needs_revision: spec composes (claim, set_plan, start), so all three
    run; positional args on every transition must be (task_id, agent_id)."""
    agent_id = uuid4()
    task_id = uuid4()
    nr = MagicMock(
        id=task_id,
        status="needs_revision",
        assigned_to=agent_id,
        plan={"x": 1},
        task_type="code",
        commits=[],
        pr_number=None,
        branch_name="feature/backend/abc",
        quick_context=None,
        parent_task_id=None,
        sequence=0,
        team="backend",
    )
    claimed = MagicMock(
        id=task_id,
        status="claimed",
        assigned_to=agent_id,
        plan={"x": 1},
        task_type="code",
    )
    started = MagicMock(
        id=task_id, status="in_progress", assigned_to=agent_id, plan={"x": 1}
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = nr
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role="developer", team="backend", slug=None
    )
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.get_subtasks.return_value = []
    task_svc.claim.return_value = claimed
    task_svc.set_plan.return_value = claimed
    task_svc.start.return_value = started
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_work_on(agent_id, task_id)

    task_svc.start.assert_awaited_once_with(task_id, agent_id)
    task_svc.claim.assert_awaited_once_with(task_id, agent_id)
    assert env.error is None


@pytest.mark.asyncio
async def test_i_will_work_on_claimed_resumption_calls_start_with_task_id_first() -> (
    None
):
    """Dev claimed resumption: spec's ``claim`` action does not list CLAIMED
    as a source-status, so the spec gate would reject. The verb body keeps
    a bespoke `claimed` re-entry (``_resume_from_claimed``) for the
    recovery scenario where an agent already owns the task and the
    orchestrator died mid-claim. start args still use (task_id, agent_id).
    """
    agent_id = uuid4()
    task_id = uuid4()
    claimed = MagicMock(
        id=task_id,
        status="claimed",
        plan={"x": 1},
        assigned_to=agent_id,
        parent_task_id=None,
        sequence=0,
        task_type="code",
        team="backend",
        branch_name="feature/backend/abc",
        commits=[],
        pr_number=None,
        quick_context=None,
    )
    started = MagicMock(
        id=task_id, status="in_progress", plan={"x": 1}, assigned_to=agent_id
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = claimed
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role="developer", team="backend", slug=None
    )
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.get_subtasks.return_value = []
    task_svc.start.return_value = started
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_work_on(agent_id, task_id)

    task_svc.start.assert_awaited_once_with(task_id, agent_id)
    assert env.error is None


@pytest.mark.asyncio
async def test_i_will_plan_calls_claim_and_start_with_task_id_first() -> None:
    """PM plan path: both claim and start must use (task_id, agent_id)."""
    pm_id = uuid4()
    task_id = uuid4()
    pending = MagicMock(
        id=task_id,
        status="pending",
        plan=None,
        assigned_to=None,
        task_type="planning",
        parent_task_id=None,
        sequence=0,
    )
    claimed = MagicMock(
        id=task_id,
        status="claimed",
        plan=None,
        assigned_to=pm_id,
        task_type="planning",
    )
    started = MagicMock(
        id=task_id,
        status="in_progress",
        plan={"text": "x"},
        assigned_to=pm_id,
        task_type="planning",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = pending
    task_svc.agent_for.return_value = MagicMock(
        id=pm_id, role="cell_pm", team="backend", slug=None
    )
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.get_subtasks.return_value = []
    task_svc.claim.return_value = claimed
    task_svc.set_plan.return_value = claimed
    task_svc.start.return_value = started
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_plan(
        pm_id,
        task_id,
        plan="break the work into 3 subtasks",
        rich_plan={
            "approach": (
                "Three-cell decomposition: backend, frontend, and ux each "
                "own a vertical slice of the work."
            ),
            "sub_tasks": [
                {"title": "Backend slice", "description": "API + DB"},
                {"title": "Frontend slice", "description": "UI integration"},
            ],
        },
    )

    task_svc.claim.assert_awaited_once_with(task_id, pm_id)
    task_svc.start.assert_awaited_once_with(task_id, pm_id)
    assert env.error is None
