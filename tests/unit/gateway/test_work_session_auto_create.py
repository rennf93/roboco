"""Wave C4 (2026-05-12): claim auto-creates a WorkSession row.

Smoke run 3 showed task.work_session_id null on every task. Pre-gateway
created the row at claim time so the panel/PR/merge subsystems could
track agent-per-task git activity (branch, commits, PR number/url,
merge status). Restoring that side-effect.

The choreographer's _claim_plan_start_run (and _resume_from_claimed)
calls TaskService.ensure_work_session(task_id, agent_id) after the
task reaches in_progress, which creates the WorkSession and stores
its id on the task.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_task_svc(agent_id, task_id, *, status: str):
    """Build a TaskService AsyncMock that completes the (claim, set_plan, start)
    sequence and returns a task with branch_name set (as the real service does
    after auto-creating the branch during claim side-effects).
    """
    in_progress_task = MagicMock(
        id=task_id,
        status="in_progress",
        plan={"text": "plan text"},
        assigned_to=agent_id,
        branch_name="feature/backend/abc",
        work_session_id=None,
        commits=[],
        pr_number=None,
        quick_context=None,
        team="backend",
        task_type="code",
        parent_task_id=None,
        sequence=0,
        project_id=uuid4(),
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = MagicMock(
        id=task_id,
        status=status,
        plan=None,
        assigned_to=None,
        branch_name="feature/backend/abc",
        work_session_id=None,
        commits=[],
        pr_number=None,
        quick_context=None,
        team="backend",
        task_type="code",
        parent_task_id=None,
        sequence=0,
        project_id=uuid4(),
    )
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role="developer", team="backend", slug=None
    )
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.get_subtasks.return_value = []
    task_svc.claim.return_value = MagicMock(
        id=task_id, status="claimed", plan=None, assigned_to=agent_id
    )
    task_svc.set_plan.return_value = MagicMock(
        id=task_id, status="claimed", plan={"text": "plan text"}, assigned_to=agent_id
    )
    task_svc.start.return_value = in_progress_task
    task_svc.ensure_work_session.return_value = None
    task_svc.session = MagicMock()
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    return task_svc


def _make_deps(task_svc) -> ChoreographerDeps:
    evidence_repo = AsyncMock()
    for method in (
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
        "journal_highlights_for_task",
    ):
        getattr(evidence_repo, method).return_value = []
    return ChoreographerDeps(
        task=task_svc,
        work_session=AsyncMock(),
        git=AsyncMock(),
        a2a=AsyncMock(),
        journal=AsyncMock(),
        audit=AsyncMock(),
        evidence_repo=evidence_repo,
    )


@pytest.mark.asyncio
async def test_i_will_work_on_calls_ensure_work_session() -> None:
    """After a successful i_will_work_on, TaskService.ensure_work_session is
    called once with (task_id, agent_id) so a WorkSession row is created and
    task.work_session_id is populated.

    Wave C4 (2026-05-12): pre-gateway parity. Smoke run 3 showed
    task.work_session_id null on every in_progress task.
    """
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = _make_task_svc(agent_id, task_id, status="pending")
    deps = _make_deps(task_svc)
    c = Choreographer(deps)

    env = await c.i_will_work_on(agent_id, task_id, plan="do x then y")

    assert env.error is None, f"Expected ok, got error={env.error} msg={env.message}"
    assert env.status == "in_progress"
    task_svc.ensure_work_session.assert_awaited_once_with(task_id, agent_id)


@pytest.mark.asyncio
async def test_i_will_plan_calls_ensure_work_session() -> None:
    """PMs also get a WorkSession via ensure_work_session (cell_pm role, planning
    task). Both i_will_work_on and i_will_plan share _claim_plan_start_run so
    the same hook fires for both.

    Wave C4 (2026-05-12): pre-gateway parity.
    """
    pm_agent_id = uuid4()
    task_id = uuid4()

    in_progress_task = MagicMock(
        id=task_id,
        status="in_progress",
        plan={"approach": "plan text", "sub_tasks": ["t1"]},
        assigned_to=pm_agent_id,
        branch_name="feature/main_pm/abc",
        work_session_id=None,
        commits=[],
        pr_number=None,
        quick_context=None,
        team="main_pm",
        task_type="planning",
        parent_task_id=None,
        sequence=0,
        project_id=uuid4(),
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = MagicMock(
        id=task_id,
        status="pending",
        plan=None,
        assigned_to=None,
        branch_name="feature/main_pm/abc",
        work_session_id=None,
        commits=[],
        pr_number=None,
        quick_context=None,
        team="main_pm",
        task_type="planning",
        parent_task_id=None,
        sequence=0,
        project_id=uuid4(),
    )
    task_svc.agent_for.return_value = MagicMock(
        id=pm_agent_id, role="cell_pm", team="backend", slug=None
    )
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.get_subtasks.return_value = []
    task_svc.claim.return_value = MagicMock(
        id=task_id, status="claimed", plan=None, assigned_to=pm_agent_id
    )
    task_svc.set_plan.return_value = MagicMock(
        id=task_id,
        status="claimed",
        plan={"approach": "plan text"},
        assigned_to=pm_agent_id,
    )
    task_svc.start.return_value = in_progress_task
    task_svc.ensure_work_session.return_value = None
    task_svc.session = MagicMock()
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    evidence_repo = AsyncMock()
    for method in (
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
        "journal_highlights_for_task",
    ):
        getattr(evidence_repo, method).return_value = []
    deps = ChoreographerDeps(
        task=task_svc,
        work_session=AsyncMock(),
        git=AsyncMock(),
        a2a=AsyncMock(),
        journal=AsyncMock(),
        audit=AsyncMock(),
        evidence_repo=evidence_repo,
    )
    c = Choreographer(deps)

    rich_plan = {
        "approach": "plan text with enough detail to pass the 20 char gate",
        "sub_tasks": [{"title": "t1", "description": "desc1"}],
    }
    env = await c.i_will_plan(
        pm_agent_id, task_id, plan="plan text", rich_plan=rich_plan
    )

    assert env.error is None, f"Expected ok, got error={env.error} msg={env.message}"
    assert env.status == "in_progress"
    task_svc.ensure_work_session.assert_awaited_once_with(task_id, pm_agent_id)


@pytest.mark.asyncio
async def test_ensure_work_session_not_called_when_start_fails() -> None:
    """If start() returns None (task in wrong state), ensure_work_session must
    NOT be called — the WorkSession must not be created for a failed transition.
    """
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = _make_task_svc(agent_id, task_id, status="pending")
    task_svc.start.return_value = None  # start fails
    deps = _make_deps(task_svc)
    c = Choreographer(deps)

    env = await c.i_will_work_on(agent_id, task_id, plan="do x then y")

    assert env.error == "invalid_state"
    task_svc.ensure_work_session.assert_not_awaited()
