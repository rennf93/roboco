"""Choreographer hot verbs must touch last_heartbeat_at via task.heartbeat()."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_deps(**overrides: AsyncMock) -> ChoreographerDeps:
    task = overrides.get("task", AsyncMock())
    # VerbRunner uses task.session.begin_nested() as a savepoint context
    # manager; ensure the mock satisfies that protocol.
    task.session = MagicMock()
    task.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    work_session = overrides.get("work_session", AsyncMock())
    git = overrides.get("git", AsyncMock())
    a2a = overrides.get("a2a", AsyncMock())
    journal = overrides.get("journal", AsyncMock())
    audit = overrides.get("audit", AsyncMock())
    evidence_repo = overrides.get("evidence_repo", AsyncMock())
    for method in (
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
    ):
        getattr(evidence_repo, method).return_value = []
    return ChoreographerDeps(
        task=task,
        work_session=work_session,
        git=git,
        a2a=a2a,
        journal=journal,
        audit=audit,
        evidence_repo=evidence_repo,
    )


@pytest.mark.asyncio
async def test_i_will_work_on_calls_heartbeat() -> None:
    aid = uuid4()
    tid = uuid4()
    pending = MagicMock(
        id=tid,
        status="pending",
        plan=None,
        assigned_to=None,
        parent_task_id=None,
        sequence=0,
        task_type="code",
        commits=[],
        pr_number=None,
        branch_name="feature/backend/abc",
        quick_context=None,
        team="backend",
    )
    in_progress = MagicMock(
        id=tid, status="in_progress", plan={"text": "go"}, assigned_to=aid
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = pending
    task_svc.agent_for.return_value = MagicMock(
        id=aid, role="developer", team="backend", slug=None
    )
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.get_subtasks.return_value = []
    task_svc.claim.return_value = MagicMock(
        id=tid, status="claimed", plan=None, assigned_to=aid
    )
    task_svc.set_plan.return_value = MagicMock(
        id=tid, status="claimed", plan={"text": "go"}, assigned_to=aid
    )
    task_svc.start.return_value = in_progress
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    await c.i_will_work_on(aid, tid, plan="go")

    task_svc.heartbeat.assert_awaited_with(tid)


@pytest.mark.asyncio
async def test_i_am_done_calls_heartbeat() -> None:
    aid = uuid4()
    tid = uuid4()
    t = MagicMock(
        id=tid,
        status="verifying",
        assigned_to=aid,
        plan="x",
        self_verified=True,
        commits=[MagicMock()],
        pr_number=42,
        pr_url="http://example/pr/42",
        team="backend",
        work_session_id=None,
    )
    submitted = MagicMock(
        id=tid,
        status="awaiting_qa",
        assigned_to=None,
        plan="x",
        team="backend",
        work_session_id=None,
        pr_url="http://example/pr/42",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(
        id=aid, role="developer", team="backend", slug=None
    )
    task_svc.submit_qa.return_value = submitted
    task_svc.qa_agent_for_team.return_value = None
    journal_svc = AsyncMock()
    journal_svc.has_reflect_for_task.return_value = True
    # JOURNAL_DURING_WORK_AT_LEAST_ONE: ≥1 decision/learning/struggle entry.
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_learning_for_task.return_value = False
    journal_svc.has_struggle_for_task.return_value = False
    evidence_repo = AsyncMock()
    for method in (
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
    ):
        getattr(evidence_repo, method).return_value = []
    evidence_repo.journal_highlights_for_task.return_value = []
    deps = _make_deps(task=task_svc, journal=journal_svc, evidence_repo=evidence_repo)
    c = Choreographer(deps)

    # Patch tracing_gate to pass — _check_tracing_gates uses
    # check_requirements, which inspects the Mock's progress_updates etc.
    # By configuring t with all-required attrs we let the real gate pass.
    t.progress_updates = [MagicMock()]
    t.acceptance_criteria = []

    await c.i_am_done(aid, tid, "done")

    task_svc.heartbeat.assert_awaited_with(tid)


@pytest.mark.asyncio
async def test_i_am_blocked_calls_heartbeat() -> None:
    aid = uuid4()
    tid = uuid4()
    t = MagicMock(
        id=tid,
        status="in_progress",
        assigned_to=aid,
        task_type="code",
        team="backend",
    )
    blocked = MagicMock(id=tid, status="blocked", assigned_to=aid)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(
        id=aid, role="developer", team="backend", slug=None
    )
    task_svc.escalate.return_value = blocked
    journal_svc = AsyncMock()
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    await c.i_am_blocked(aid, tid, "stuck on X")

    task_svc.heartbeat.assert_awaited_with(tid)


@pytest.mark.asyncio
async def test_pm_give_me_work_calls_heartbeat_when_returning_task() -> None:
    pm_id = uuid4()
    tid = uuid4()
    assigned = MagicMock(id=tid, status="pending")
    task_svc = AsyncMock()
    task_svc.list_pending_for_agent.return_value = []
    task_svc.list_assigned_for_agent.return_value = [assigned]
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    await c.pm_give_me_work(pm_id)

    task_svc.heartbeat.assert_awaited_with(tid)


@pytest.mark.asyncio
async def test_pm_give_me_work_does_not_heartbeat_on_idle() -> None:
    pm_id = uuid4()
    task_svc = AsyncMock()
    task_svc.list_pending_for_agent.return_value = []
    task_svc.list_assigned_for_agent.return_value = []
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    await c.pm_give_me_work(pm_id)

    task_svc.heartbeat.assert_not_awaited()


@pytest.mark.asyncio
async def test_i_will_plan_calls_heartbeat() -> None:
    pm_id = uuid4()
    tid = uuid4()
    pending = MagicMock(
        id=tid,
        status="pending",
        plan=None,
        assigned_to=None,
        parent_task_id=None,
        sequence=0,
        task_type="planning",
    )
    in_progress = MagicMock(
        id=tid, status="in_progress", plan="plan-text", assigned_to=pm_id
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = pending
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.get_subtasks.return_value = []
    task_svc.claim.return_value = MagicMock(
        id=tid, status="claimed", plan=None, assigned_to=pm_id
    )
    task_svc.set_plan.return_value = MagicMock(
        id=tid, status="claimed", plan="plan-text", assigned_to=pm_id
    )
    task_svc.start.return_value = in_progress
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    await c.i_will_plan(
        pm_id,
        tid,
        plan="plan-text",
        rich_plan={
            "approach": "Single-cell decomposition: backend handles all scope.",
            "sub_tasks": [{"title": "Slice A", "description": "backend API work"}],
        },
    )

    task_svc.heartbeat.assert_awaited_with(tid)
