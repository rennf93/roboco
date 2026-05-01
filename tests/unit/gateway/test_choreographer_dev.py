"""Tests for the developer-facing Choreographer methods."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_deps(**overrides: AsyncMock) -> ChoreographerDeps:
    task = overrides.get("task", AsyncMock())
    work_session = overrides.get("work_session", AsyncMock())
    git = overrides.get("git", AsyncMock())
    a2a = overrides.get("a2a", AsyncMock())
    journal = overrides.get("journal", AsyncMock())
    audit = overrides.get("audit", AsyncMock())
    evidence_repo = overrides.get("evidence_repo", AsyncMock())
    # Ensure evidence_repo returns empty lists by default
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
async def test_give_me_work_returns_assigned_task() -> None:
    agent_id = uuid4()
    task_obj = MagicMock(id=uuid4(), status="pending", title="t1")
    task_svc = AsyncMock()
    task_svc.list_assigned_for_agent.return_value = [task_obj]
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.give_me_work(agent_id)
    body = env.as_dict()
    assert body["status"] == "pending"
    assert body["task_id"] == str(task_obj.id)
    assert "i_will_work_on" in body["next"]


@pytest.mark.asyncio
async def test_give_me_work_returns_paused_when_no_assigned() -> None:
    agent_id = uuid4()
    paused_obj = MagicMock(id=uuid4(), status="paused")
    task_svc = AsyncMock()
    task_svc.list_assigned_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = [paused_obj]
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.give_me_work(agent_id)
    body = env.as_dict()
    assert body["task_id"] == str(paused_obj.id)
    assert "resume" in body["next"]


@pytest.mark.asyncio
async def test_give_me_work_returns_idle_when_no_work() -> None:
    agent_id = uuid4()
    task_svc = AsyncMock()
    task_svc.list_assigned_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.give_me_work(agent_id)
    body = env.as_dict()
    assert body["status"] == "idle"
    assert "i_am_idle" in body["next"]


@pytest.mark.asyncio
async def test_i_will_work_on_pending_with_plan() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    pending_task = MagicMock(id=task_id, status="pending", plan=None, assigned_to=None)
    in_progress_task = MagicMock(
        id=task_id, status="in_progress", plan={"text": "do x"}, assigned_to=agent_id
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = pending_task
    task_svc.claim.return_value = MagicMock(
        id=task_id, status="claimed", plan=None, assigned_to=agent_id
    )
    task_svc.set_plan.return_value = MagicMock(
        id=task_id, status="claimed", plan={"text": "do x"}, assigned_to=agent_id
    )
    task_svc.start.return_value = in_progress_task
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_work_on(agent_id, task_id, plan="do x then y")
    assert env.error is None
    assert env.status == "in_progress"
    task_svc.claim.assert_awaited_once_with(agent_id, task_id)
    task_svc.set_plan.assert_awaited_once()
    task_svc.start.assert_awaited_once_with(agent_id, task_id)


@pytest.mark.asyncio
async def test_i_will_work_on_pending_no_plan_returns_tracing_gap() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    pending_task = MagicMock(
        id=task_id,
        status="pending",
        plan=None,
        assigned_to=None,
        description="task description",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = pending_task
    task_svc.claim.return_value = MagicMock(
        id=task_id, status="claimed", plan=None, assigned_to=agent_id
    )
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_work_on(agent_id, task_id, plan=None)
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "plan" in body["missing"]
    assert "i_will_work_on" in body["remediate"]


@pytest.mark.asyncio
async def test_i_will_work_on_needs_revision_re_starts() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    nr_task = MagicMock(
        id=task_id, status="needs_revision", assigned_to=agent_id, plan={"x": 1}
    )
    in_progress_task = MagicMock(
        id=task_id, status="in_progress", assigned_to=agent_id, plan={"x": 1}
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = nr_task
    task_svc.start.return_value = in_progress_task
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_work_on(agent_id, task_id)
    assert env.status == "in_progress"
    task_svc.claim.assert_not_awaited()  # already assigned
    task_svc.start.assert_awaited_once_with(agent_id, task_id)


@pytest.mark.asyncio
async def test_i_will_work_on_task_not_found_returns_not_found() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_work_on(agent_id, task_id)
    body = env.as_dict()
    assert body["error"] == "not_found"


@pytest.mark.asyncio
async def test_i_will_work_on_invalid_state_returns_invalid_state() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    completed_task = MagicMock(id=task_id, status="completed", assigned_to=agent_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = completed_task
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_work_on(agent_id, task_id)
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "completed" in body["message"]


@pytest.mark.asyncio
async def test_i_have_committed_records_progress() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    active = MagicMock(
        id=task_id, status="in_progress", assigned_to=agent_id, plan={"x": 1}
    )
    task_svc = AsyncMock()
    task_svc.get_active_task_for_agent.return_value = active
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_have_committed(agent_id, "feat(api): add /healthz endpoint")
    assert env.error is None
    task_svc.add_progress.assert_awaited_once_with(
        task_id, agent_id, "feat(api): add /healthz endpoint"
    )


@pytest.mark.asyncio
async def test_i_have_committed_no_active_task_returns_invalid_state() -> None:
    agent_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get_active_task_for_agent.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_have_committed(agent_id, "feat: x")
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "give_me_work" in body["remediate"]


@pytest.mark.asyncio
async def test_i_have_committed_no_plan_returns_tracing_gap() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    active = MagicMock(
        id=task_id, status="in_progress", assigned_to=agent_id, plan=None
    )
    task_svc = AsyncMock()
    task_svc.get_active_task_for_agent.return_value = active
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_have_committed(agent_id, "feat: x")
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "plan" in body["missing"]
    task_svc.add_progress.assert_not_awaited()
