"""Unit tests for TaskService gateway-backfill methods.

These cover the methods the Choreographer calls into; full end-to-end
behavior is exercised by the gateway tests. Each test mocks the DB
session boundary and checks the method's contract.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.db.tables import AgentTable, AuditLogTable, ProjectTable, TaskTable
from roboco.exceptions import TaskLifecycleError
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    BlockerResolverType,
    Complexity,
    TaskNature,
    TaskStatus,
    TaskType,
    Team,
)
from roboco.models.task import TaskCreateRequest
from roboco.services.task import GatewayAgentView, TaskService, get_task_service
from sqlalchemy import select


def _build_task(**overrides: object) -> MagicMock:
    base: dict[str, object] = {
        "id": uuid4(),
        "status": TaskStatus.PENDING,
        "branch_name": "feature/backend/abc12345",
        "assigned_to": None,
        "claimed_by": None,
        "claimed_at": None,
        "plan": None,
        "qa_evidence_inspected": False,
        "pre_block_state": None,
        "pre_block_assignee": None,
        "pre_block_metadata": None,
        "blocker_resolver_type": None,
        "blocker_raised_by": None,
        "commits": [],
        "dev_notes": None,
    }
    base.update(overrides)
    return MagicMock(**base)


def _service_with(execute_returns: object) -> TaskService:
    """Build a TaskService whose session.execute returns `execute_returns`."""
    session = MagicMock()
    session.execute = AsyncMock(return_value=execute_returns)
    session.flush = AsyncMock()
    return TaskService(session)


def _bind(svc: TaskService, name: str, value: object) -> None:
    """Stub `name` on `svc` without tripping mypy's method-assign check."""
    object.__setattr__(svc, name, value)


# ---------------------------------------------------------------------------
# Aliases / thin wrappers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_blocked_for_team_filters_by_team() -> None:
    svc = TaskService(MagicMock())
    list_blocked_mock = AsyncMock(return_value=[MagicMock(id="t1")])
    _bind(svc, "list_blocked", list_blocked_mock)
    out = await svc.list_blocked_for_team(Team.BACKEND)
    list_blocked_mock.assert_awaited_once_with(team=Team.BACKEND)
    assert len(out) == 1


@pytest.mark.asyncio
async def test_list_blocked_all_teams_passes_no_team() -> None:
    svc = TaskService(MagicMock())
    list_blocked_mock = AsyncMock(return_value=[])
    _bind(svc, "list_blocked", list_blocked_mock)
    await svc.list_blocked_all_teams()
    list_blocked_mock.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_list_awaiting_pm_review_for_team_passes_team() -> None:
    svc = TaskService(MagicMock())
    list_pm_mock = AsyncMock(return_value=[])
    _bind(svc, "list_awaiting_pm_review", list_pm_mock)
    await svc.list_awaiting_pm_review_for_team(Team.FRONTEND)
    list_pm_mock.assert_awaited_once_with(team=Team.FRONTEND)


@pytest.mark.asyncio
async def test_submit_verification_records_progress_when_notes_given() -> None:
    svc = TaskService(MagicMock())
    add_progress_mock = AsyncMock()
    submit_for_verification_mock = AsyncMock(return_value=MagicMock())
    _bind(svc, "add_progress", add_progress_mock)
    _bind(svc, "submit_for_verification", submit_for_verification_mock)
    agent_id = uuid4()
    task_id = uuid4()
    await svc.submit_verification(agent_id, task_id, "implemented login")
    add_progress_mock.assert_awaited_once_with(task_id, agent_id, "implemented login")
    submit_for_verification_mock.assert_awaited_once_with(
        task_id, agent_role="developer"
    )


@pytest.mark.asyncio
async def test_submit_verification_skips_progress_when_notes_empty() -> None:
    svc = TaskService(MagicMock())
    add_progress_mock = AsyncMock()
    _bind(svc, "add_progress", add_progress_mock)
    _bind(svc, "submit_for_verification", AsyncMock(return_value=MagicMock()))
    await svc.submit_verification(uuid4(), uuid4(), "")
    add_progress_mock.assert_not_called()


@pytest.mark.asyncio
async def test_submit_qa_records_progress_when_notes_given() -> None:
    svc = TaskService(MagicMock())
    add_progress_mock = AsyncMock()
    submit_for_qa_mock = AsyncMock(return_value=MagicMock())
    _bind(svc, "add_progress", add_progress_mock)
    _bind(svc, "submit_for_qa", submit_for_qa_mock)
    agent_id = uuid4()
    task_id = uuid4()
    await svc.submit_qa(agent_id, task_id, "ready for review")
    add_progress_mock.assert_awaited_once()
    submit_for_qa_mock.assert_awaited_once_with(task_id, agent_role="developer")


# ---------------------------------------------------------------------------
# list_assigned_for_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_assigned_for_agent_returns_active_tasks() -> None:
    expected_tasks = [MagicMock(id="t1"), MagicMock(id="t2")]
    scalars = MagicMock()
    scalars.all.return_value = expected_tasks
    result = MagicMock()
    result.scalars.return_value = scalars
    svc = _service_with(result)
    out = await svc.list_assigned_for_agent(uuid4())
    assert out == expected_tasks


# ---------------------------------------------------------------------------
# agent_for
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_for_returns_view_with_role_team_skills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_agent = MagicMock(
        id=uuid4(), slug="be-pm", role=AgentRole.CELL_PM, team=Team.BACKEND
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = fake_agent
    svc = _service_with(result)

    monkeypatch.setattr(
        "roboco.agents_config.get_escalation_target", lambda _slug: "main_pm"
    )
    monkeypatch.setattr(
        "roboco.agents_config.get_agent_skills",
        lambda _slug: [{"id": "task_management"}],
    )
    view = await svc.agent_for(uuid4())
    assert isinstance(view, GatewayAgentView)
    assert view.role == "cell_pm"
    assert view.team == "backend"
    assert view.escalation_target == "main_pm"
    assert view.skills == [{"id": "task_management"}]


@pytest.mark.asyncio
async def test_agent_for_returns_none_when_missing() -> None:
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    svc = _service_with(result)
    assert await svc.agent_for(uuid4()) is None


# ---------------------------------------------------------------------------
# qa/documenter/cell_pm for_team
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qa_agent_for_team_finds_qa() -> None:
    qa = MagicMock(id=uuid4(), role=AgentRole.QA, team=Team.BACKEND)
    scalars = MagicMock()
    scalars.first.return_value = qa
    result = MagicMock()
    result.scalars.return_value = scalars
    svc = _service_with(result)
    out = await svc.qa_agent_for_team(Team.BACKEND)
    assert out is qa


@pytest.mark.asyncio
async def test_documenter_for_team_returns_none_when_missing() -> None:
    scalars = MagicMock()
    scalars.first.return_value = None
    result = MagicMock()
    result.scalars.return_value = scalars
    svc = _service_with(result)
    assert await svc.documenter_for_team(Team.BACKEND) is None


@pytest.mark.asyncio
async def test_cell_pm_for_team_finds_pm() -> None:
    pm = MagicMock(id=uuid4(), role=AgentRole.CELL_PM, team=Team.UX_UI)
    scalars = MagicMock()
    scalars.first.return_value = pm
    result = MagicMock()
    result.scalars.return_value = scalars
    svc = _service_with(result)
    assert await svc.cell_pm_for_team(Team.UX_UI) is pm


# ---------------------------------------------------------------------------
# get_active_task_for_agent + list_paused_for_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_active_task_for_agent_returns_top_task() -> None:
    task = MagicMock(id=uuid4(), status=TaskStatus.IN_PROGRESS)
    result = MagicMock()
    result.scalar_one_or_none.return_value = task
    svc = _service_with(result)
    assert await svc.get_active_task_for_agent(uuid4()) is task


@pytest.mark.asyncio
async def test_list_paused_for_agent_returns_paused_tasks() -> None:
    paused = [MagicMock(id=uuid4(), status=TaskStatus.PAUSED)]
    scalars = MagicMock()
    scalars.all.return_value = paused
    result = MagicMock()
    result.scalars.return_value = scalars
    svc = _service_with(result)
    out = await svc.list_paused_for_agent(uuid4())
    assert out == paused


# ---------------------------------------------------------------------------
# list_awaiting_main_pm_all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_awaiting_main_pm_all_returns_root_tasks() -> None:
    roots = [MagicMock(id=uuid4(), parent_task_id=None)]
    scalars = MagicMock()
    scalars.all.return_value = roots
    result = MagicMock()
    result.scalars.return_value = scalars
    svc = _service_with(result)
    out = await svc.list_awaiting_main_pm_all()
    assert out == roots


# ---------------------------------------------------------------------------
# all_subtasks_terminal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_subtasks_terminal_true_when_all_completed() -> None:
    scalars = MagicMock()
    scalars.all.return_value = [TaskStatus.COMPLETED, TaskStatus.CANCELLED]
    result = MagicMock()
    result.scalars.return_value = scalars
    svc = _service_with(result)
    assert await svc.all_subtasks_terminal(uuid4()) is True


@pytest.mark.asyncio
async def test_all_subtasks_terminal_false_when_one_active() -> None:
    scalars = MagicMock()
    scalars.all.return_value = [TaskStatus.COMPLETED, TaskStatus.IN_PROGRESS]
    result = MagicMock()
    result.scalars.return_value = scalars
    svc = _service_with(result)
    assert await svc.all_subtasks_terminal(uuid4()) is False


@pytest.mark.asyncio
async def test_all_subtasks_terminal_true_when_no_subtasks() -> None:
    scalars = MagicMock()
    scalars.all.return_value = []
    result = MagicMock()
    result.scalars.return_value = scalars
    svc = _service_with(result)
    assert await svc.all_subtasks_terminal(uuid4()) is True


# ---------------------------------------------------------------------------
# set_plan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_plan_wraps_string_into_text_dict() -> None:
    task = _build_task()
    svc = TaskService(MagicMock(flush=AsyncMock()))
    _bind(svc, "get", AsyncMock(return_value=task))
    out = await svc.set_plan(task.id, "do the thing")
    assert task.plan == {"text": "do the thing"}
    assert out is task


@pytest.mark.asyncio
async def test_set_plan_passes_dict_through() -> None:
    task = _build_task()
    svc = TaskService(MagicMock(flush=AsyncMock()))
    _bind(svc, "get", AsyncMock(return_value=task))
    out = await svc.set_plan(task.id, {"steps": ["a", "b"]})
    assert task.plan == {"steps": ["a", "b"]}
    assert out is task


@pytest.mark.asyncio
async def test_set_plan_returns_none_when_task_missing() -> None:
    svc = TaskService(MagicMock(flush=AsyncMock()))
    _bind(svc, "get", AsyncMock(return_value=None))
    assert await svc.set_plan(uuid4(), "plan") is None


# ---------------------------------------------------------------------------
# mark_evidence_inspected + mark_agent_idle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_evidence_inspected_sets_flag() -> None:
    task = _build_task(qa_evidence_inspected=False)
    svc = TaskService(MagicMock(flush=AsyncMock()))
    _bind(svc, "get", AsyncMock(return_value=task))
    await svc.mark_evidence_inspected(task.id)
    assert task.qa_evidence_inspected is True


@pytest.mark.asyncio
async def test_mark_evidence_inspected_no_op_on_missing_task() -> None:
    svc = TaskService(MagicMock(flush=AsyncMock()))
    _bind(svc, "get", AsyncMock(return_value=None))
    await svc.mark_evidence_inspected(uuid4())  # must not raise


@pytest.mark.asyncio
async def test_reassign_sets_assigned_to_and_claimed_by() -> None:
    task = _build_task(assigned_to=None, claimed_by=None)
    svc = TaskService(MagicMock(flush=AsyncMock()))
    _bind(svc, "get", AsyncMock(return_value=task))
    new_assignee = uuid4()
    out = await svc.reassign(task.id, new_assignee)
    assert out is task
    assert task.assigned_to == new_assignee
    assert task.claimed_by == new_assignee


@pytest.mark.asyncio
async def test_reassign_clears_assignment_when_none() -> None:
    prev = uuid4()
    task = _build_task(assigned_to=prev, claimed_by=prev)
    svc = TaskService(MagicMock(flush=AsyncMock()))
    _bind(svc, "get", AsyncMock(return_value=task))
    out = await svc.reassign(task.id, None)
    assert out is task
    assert task.assigned_to is None
    assert task.claimed_by is None


@pytest.mark.asyncio
async def test_reassign_returns_none_when_task_missing() -> None:
    svc = TaskService(MagicMock(flush=AsyncMock()))
    _bind(svc, "get", AsyncMock(return_value=None))
    out = await svc.reassign(uuid4(), uuid4())
    assert out is None


@pytest.mark.asyncio
async def test_mark_agent_idle_sets_status_idle() -> None:
    agent = MagicMock(id=uuid4(), status=AgentStatus.ACTIVE)
    result = MagicMock()
    result.scalar_one_or_none.return_value = agent
    svc = _service_with(result)
    await svc.mark_agent_idle(agent.id)
    assert agent.status == AgentStatus.IDLE


# ---------------------------------------------------------------------------
# qa_claim / doc_claim
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qa_claim_sets_assignment_on_awaiting_qa() -> None:
    task = _build_task(status=TaskStatus.AWAITING_QA, active_claimant_id=None)
    result = MagicMock()
    result.scalar_one_or_none.return_value = task
    session = MagicMock(flush=AsyncMock())
    session.execute = AsyncMock(return_value=result)
    svc = TaskService(session)
    qa_id = uuid4()
    out = await svc.qa_claim(qa_id, task.id)
    assert out is task
    assert task.assigned_to == qa_id
    assert task.claimed_by == qa_id
    assert isinstance(task.claimed_at, datetime)


@pytest.mark.asyncio
async def test_qa_claim_rejects_wrong_status() -> None:
    task = _build_task(status=TaskStatus.IN_PROGRESS, active_claimant_id=None)
    result = MagicMock()
    result.scalar_one_or_none.return_value = task
    session = MagicMock(flush=AsyncMock())
    session.execute = AsyncMock(return_value=result)
    svc = TaskService(session)
    out = await svc.qa_claim(uuid4(), task.id)
    assert out is None


@pytest.mark.asyncio
async def test_doc_claim_sets_assignment_on_awaiting_documentation() -> None:
    task = _build_task(
        status=TaskStatus.AWAITING_DOCUMENTATION, active_claimant_id=None
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = task
    session = MagicMock(flush=AsyncMock())
    session.execute = AsyncMock(return_value=result)
    svc = TaskService(session)
    doc_id = uuid4()
    out = await svc.doc_claim(doc_id, task.id)
    assert out is task
    assert task.assigned_to == doc_id


# ---------------------------------------------------------------------------
# qa_pass / qa_fail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qa_pass_delegates_to_pass_qa() -> None:
    qa_id = uuid4()
    task_id = uuid4()
    task = _build_task(id=task_id, claimed_by=qa_id)
    svc = TaskService(MagicMock(flush=AsyncMock()))
    pass_qa_mock = AsyncMock(return_value=MagicMock())
    _bind(svc, "get", AsyncMock(return_value=task))
    _bind(svc, "pass_qa", pass_qa_mock)
    await svc.qa_pass(qa_id, task_id, "looks good")
    pass_qa_mock.assert_awaited_once_with(task_id, notes="looks good", agent_role="qa")
    # active_claimant_id cleared so the documenter can claim cleanly.
    assert task.active_claimant_id is None


@pytest.mark.asyncio
async def test_qa_fail_appends_issues_to_dev_notes() -> None:
    qa_id = uuid4()
    task = _build_task(dev_notes=None, claimed_by=qa_id)
    svc = TaskService(MagicMock(flush=AsyncMock()))
    fail_qa_mock = AsyncMock(return_value=task)
    _bind(svc, "get", AsyncMock(return_value=task))
    _bind(svc, "fail_qa", fail_qa_mock)
    issues = ["missing test", "no docstring"]
    await svc.qa_fail(qa_id, task.id, "blocking", issues)
    assert task.dev_notes is not None
    assert "missing test" in task.dev_notes
    assert "no docstring" in task.dev_notes
    fail_qa_mock.assert_awaited_once_with(task.id, notes="blocking", agent_role="qa")
    assert task.active_claimant_id is None


# ---------------------------------------------------------------------------
# unblock_with_restore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unblock_with_restore_returns_to_pre_block_state() -> None:
    pre_assignee = uuid4()
    task = _build_task(
        status=TaskStatus.BLOCKED,
        pre_block_state="in_progress",
        pre_block_assignee=pre_assignee,
        pre_block_metadata={"foo": "bar"},
        blocker_resolver_type=BlockerResolverType.AGENT,
        blocker_raised_by=pre_assignee,
    )
    svc = TaskService(MagicMock(flush=AsyncMock()))
    _bind(svc, "get", AsyncMock(return_value=task))
    out = await svc.unblock_with_restore(uuid4(), task.id, restore=True)
    assert out is task
    assert task.status == TaskStatus.IN_PROGRESS
    assert task.assigned_to == pre_assignee
    assert task.pre_block_state is None
    assert task.pre_block_assignee is None


@pytest.mark.asyncio
async def test_unblock_with_restore_falls_through_when_no_snapshot() -> None:
    task = _build_task(status=TaskStatus.BLOCKED, pre_block_state=None)
    svc = TaskService(MagicMock(flush=AsyncMock()))
    unblock_mock = AsyncMock(return_value=task)
    _bind(svc, "get", AsyncMock(return_value=task))
    _bind(svc, "unblock", unblock_mock)
    out = await svc.unblock_with_restore(uuid4(), task.id, restore=True)
    unblock_mock.assert_awaited_once()
    assert out is task


@pytest.mark.asyncio
async def test_unblock_with_restore_calls_legacy_unblock_when_restore_false() -> None:
    task = _build_task(
        status=TaskStatus.BLOCKED, pre_block_state=TaskStatus.IN_PROGRESS.value
    )
    svc = TaskService(MagicMock(flush=AsyncMock()))
    unblock_mock = AsyncMock(return_value=task)
    _bind(svc, "get", AsyncMock(return_value=task))
    _bind(svc, "unblock", unblock_mock)
    await svc.unblock_with_restore(uuid4(), task.id, restore=False)
    unblock_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_unblock_no_branch_returns_to_pending() -> None:
    # A task blocked before it was ever claimed (a dependency-gated claim that
    # got escalated) has no branch — unblock must return it to pending, not a
    # branchless in_progress the dispatcher refuses to spawn (spawn-loop).
    raiser = uuid4()
    task = _build_task(
        status=TaskStatus.BLOCKED, branch_name=None, blocker_raised_by=raiser
    )
    svc = TaskService(MagicMock(flush=AsyncMock()))
    _bind(svc, "get", AsyncMock(return_value=task))
    _bind(svc, "_index_lifecycle_event_background", AsyncMock())
    out = await svc.unblock(task.id)
    assert out is task
    assert task.status == TaskStatus.PENDING
    assert task.assigned_to == raiser


@pytest.mark.asyncio
async def test_admin_set_status_out_of_blocked_restores_pre_block_owner() -> None:
    # A code task a dev escalated to its cell PM (assigned_to=PM, BLOCKED,
    # snapshot=dev). Taking it out of blocked via the admin override (operator
    # PATCH, or the orchestrator's auto-recover/auto-resume) must hand ownership
    # back to the dev — otherwise it re-enters pending/in_progress still owned by
    # the PM and the dispatcher execute-spawns the PM on a dev code task (loop).
    dev = uuid4()
    pm = uuid4()
    task = _build_task(
        status=TaskStatus.BLOCKED,
        assigned_to=pm,
        claimed_by=pm,
        branch_name="feature/frontend/abc--def--ghi",
        pre_block_state="in_progress",
        pre_block_assignee=dev,
    )
    svc = TaskService(MagicMock(flush=AsyncMock()))
    _bind(svc, "get", AsyncMock(return_value=task))
    out = await svc.admin_set_status(task.id, TaskStatus.IN_PROGRESS)
    assert out is task
    assert task.status == TaskStatus.IN_PROGRESS
    assert task.assigned_to == dev
    assert task.claimed_by == dev
    assert task.pre_block_assignee is None
    assert task.pre_block_state is None


@pytest.mark.asyncio
async def test_admin_set_status_non_blocked_is_bare_status_set() -> None:
    # The restore branch fires ONLY on blocked -> pending/in_progress with a
    # snapshot. Every other override stays a plain status set; the owner is
    # untouched (no spurious restore/divert).
    owner = uuid4()
    task = _build_task(
        status=TaskStatus.AWAITING_PM_REVIEW,
        assigned_to=owner,
        claimed_by=owner,
    )
    svc = TaskService(MagicMock(flush=AsyncMock()))
    _bind(svc, "get", AsyncMock(return_value=task))
    out = await svc.admin_set_status(task.id, TaskStatus.COMPLETED)
    assert out is task
    assert task.status == TaskStatus.COMPLETED
    assert task.assigned_to == owner


@pytest.mark.asyncio
async def test_admin_set_status_force_emits_distinct_override_audit_row() -> None:
    """#13: a forced override (force=True) emits a distinct ``task.admin_override``
    audit row marking the bypass past the lifecycle gate — distinguishable from
    the in-band transition row. Without force, no override row is emitted."""
    owner = uuid4()
    task = _build_task(
        status=TaskStatus.AWAITING_PM_REVIEW,
        assigned_to=owner,
        claimed_by=owner,
    )
    added: list[object] = []
    session = MagicMock()
    session.flush = AsyncMock()
    session.add.side_effect = added.append
    svc = TaskService(session)
    _bind(svc, "get", AsyncMock(return_value=task))

    await svc.admin_set_status(
        task.id, TaskStatus.COMPLETED, actor_id=owner, actor_role="ceo", force=True
    )

    rows = [r for r in added if isinstance(r, AuditLogTable)]
    override_rows = [r for r in rows if r.event_type == "task.admin_override"]
    assert len(override_rows) == 1
    row = override_rows[0]
    assert row.target_id == task.id
    assert row.severity == "warning"
    assert row.details["forced"] is True
    assert row.details["from_status"] == "awaiting_pm_review"
    assert row.details["to_status"] == "completed"
    assert row.agent_id == owner


@pytest.mark.asyncio
async def test_admin_set_status_no_force_emits_no_override_audit_row() -> None:
    """#13: without force, only the transition row is emitted — no
    ``task.admin_override`` row (the bypass was not acknowledged)."""
    owner = uuid4()
    task = _build_task(
        status=TaskStatus.BLOCKED,
        assigned_to=owner,
        claimed_by=owner,
    )
    added: list[object] = []
    session = MagicMock()
    session.flush = AsyncMock()
    session.add.side_effect = added.append
    svc = TaskService(session)
    _bind(svc, "get", AsyncMock(return_value=task))

    await svc.admin_set_status(task.id, TaskStatus.PENDING, actor_id=owner)

    rows = [r for r in added if isinstance(r, AuditLogTable)]
    assert not any(r.event_type == "task.admin_override" for r in rows)


@pytest.mark.asyncio
async def test_admin_set_status_blocked_restore_attributes_admin_actor() -> None:
    """#2176: an admin restore out of BLOCKED (with a pre-block snapshot) must
    stamp the ADMIN actor on the audit rows — not the restored owner — and emit
    a task.admin_override row so the re-owning is traceable. Previously this
    branch returned early via _apply_pre_block_restore with agent_role=None and
    audit_agent_id=restored_owner, and (force=false here) wrote no override row,
    so the privilege use was untraceable."""
    dev = uuid4()
    pm = uuid4()
    admin = uuid4()
    task = _build_task(
        status=TaskStatus.BLOCKED,
        assigned_to=pm,
        claimed_by=pm,
        branch_name="feature/backend/abc--def",
        pre_block_state="in_progress",
        pre_block_assignee=dev,
    )
    added: list[object] = []
    session = MagicMock()
    session.flush = AsyncMock()
    session.add.side_effect = added.append
    svc = TaskService(session)
    _bind(svc, "get", AsyncMock(return_value=task))

    # force defaults to False — pending is not a hatch destination, so the only
    # way this override is recorded is the restore branch's own override row.
    out = await svc.admin_set_status(
        task.id, TaskStatus.PENDING, actor_id=admin, actor_role="ceo"
    )
    assert out is task
    assert task.status == TaskStatus.PENDING
    assert task.assigned_to == dev  # ownership restored to the pre-block dev

    rows = [r for r in added if isinstance(r, AuditLogTable)]
    override_rows = [r for r in rows if r.event_type == "task.admin_override"]
    assert len(override_rows) == 1
    override = override_rows[0]
    assert override.agent_id == admin  # the admin, not the restored dev
    assert override.details["restore"] is True
    assert override.details["forced"] is False
    # Every audit row (the transition row AND the override row) is attributed to
    # the admin actor — the restored owner is NOT recorded as the actor.
    assert all(r.agent_id == admin for r in rows)
    assert not any(r.agent_id == dev for r in rows)


@pytest.mark.asyncio
async def test_admin_set_status_blocked_to_review_state_clears_claim() -> None:
    """Forcing a BLOCKED task into a review/queue state must clear the claim.

    Live wedge (2026-07-01 22:13Z): the CEO forced blocked ->
    awaiting_pm_review, the stale escalation claim (main-pm) survived, and the
    respawned cell PM was handed the task by give_me_work while every
    note(task_id=...) bounced not_authorized "you do not hold the claim" — so
    it re-blocked. Review-state targets are re-claimed via the claim verbs, so
    the override must leave no stale claimant behind.
    """
    pm = uuid4()
    task = _build_task(
        status=TaskStatus.BLOCKED,
        assigned_to=pm,
        claimed_by=pm,
        claimed_at=datetime.now(UTC),
        active_claimant_id=pm,
        pre_block_state="awaiting_pm_review",
        pre_block_assignee=pm,
    )
    svc = TaskService(MagicMock(flush=AsyncMock()))
    _bind(svc, "get", AsyncMock(return_value=task))
    out = await svc.admin_set_status(task.id, TaskStatus.AWAITING_PM_REVIEW)
    assert out is task
    assert task.status == TaskStatus.AWAITING_PM_REVIEW
    assert task.claimed_by is None
    assert task.claimed_at is None
    assert task.active_claimant_id is None
    # The consumed snapshot must not survive to confuse a later unblock.
    assert task.pre_block_state is None
    assert task.pre_block_assignee is None


@pytest.mark.asyncio
async def test_admin_set_status_blocked_to_needs_revision_clears_claim() -> None:
    """blocked -> needs_revision (the other live recovery target) also clears
    the claim so the revision coordinator / re-claiming dev starts clean."""
    pm = uuid4()
    task = _build_task(
        status=TaskStatus.BLOCKED,
        assigned_to=pm,
        claimed_by=pm,
        claimed_at=datetime.now(UTC),
        active_claimant_id=pm,
        pre_block_state="awaiting_pm_review",
        pre_block_assignee=pm,
    )
    svc = TaskService(MagicMock(flush=AsyncMock()))
    _bind(svc, "get", AsyncMock(return_value=task))
    out = await svc.admin_set_status(task.id, TaskStatus.NEEDS_REVISION)
    assert out is task
    assert task.status == TaskStatus.NEEDS_REVISION
    assert task.claimed_by is None
    assert task.claimed_at is None
    assert task.active_claimant_id is None


@pytest.mark.asyncio
async def test_admin_set_status_non_blocked_source_keeps_claim() -> None:
    """The claim-clear fires only when leaving BLOCKED — a plain override on a
    non-blocked task (e.g. completing a reviewed task) must not strip the
    owner's claim."""
    owner = uuid4()
    claimed_at = datetime.now(UTC)
    task = _build_task(
        status=TaskStatus.AWAITING_PM_REVIEW,
        assigned_to=owner,
        claimed_by=owner,
        claimed_at=claimed_at,
        active_claimant_id=owner,
    )
    svc = TaskService(MagicMock(flush=AsyncMock()))
    _bind(svc, "get", AsyncMock(return_value=task))
    await svc.admin_set_status(task.id, TaskStatus.COMPLETED)
    assert task.claimed_by == owner
    assert task.claimed_at == claimed_at
    assert task.active_claimant_id == owner


@pytest.mark.asyncio
async def test_request_changes_routes_leaf_back_to_original_dev() -> None:
    """PM merge-review reject: awaiting_pm_review -> needs_revision, issues
    appended for the dev, task re-owned by the original developer (the QA-fail
    routing), stale claimant cleared."""
    dev = uuid4()
    pm = uuid4()
    task = _build_task(
        status=TaskStatus.AWAITING_PM_REVIEW,
        assigned_to=pm,
        claimed_by=pm,
        active_claimant_id=pm,
        dev_notes=None,
        orchestration_markers={"original_developer": str(dev)},
    )
    svc = TaskService(MagicMock(flush=AsyncMock()))
    _bind(svc, "get", AsyncMock(return_value=task))
    _bind(svc, "_validate_and_set_status", MagicMock())
    out = await svc.request_changes(
        pm, task.id, "scope violation", ["frontend/CLAUDE.md modified out of scope"]
    )
    assert out is task
    assert task.assigned_to == dev
    assert task.claimed_by == dev
    assert task.active_claimant_id is None
    assert "[PM REVIEW ISSUES]" in (task.dev_notes or "")
    assert "frontend/CLAUDE.md modified out of scope" in (task.dev_notes or "")


@pytest.mark.asyncio
async def test_request_changes_without_dev_marker_routes_to_revision_pm() -> None:
    """An assembled task (no original-developer marker) lands on the PM who
    owns its revision — same fallback pr_fail uses."""
    cell_pm = SimpleNamespace(id=uuid4())
    actor = uuid4()
    task = _build_task(
        status=TaskStatus.AWAITING_PM_REVIEW,
        assigned_to=actor,
        claimed_by=actor,
        active_claimant_id=actor,
        dev_notes=None,
        orchestration_markers=None,
    )
    svc = TaskService(MagicMock(flush=AsyncMock()))
    _bind(svc, "get", AsyncMock(return_value=task))
    _bind(svc, "_validate_and_set_status", MagicMock())
    _bind(svc, "_revision_pm_for_task", AsyncMock(return_value=cell_pm))
    out = await svc.request_changes(actor, task.id, "assembly issue", ["bad merge"])
    assert out is task
    assert task.assigned_to == cell_pm.id
    assert task.claimed_by == cell_pm.id
    assert task.active_claimant_id is None


@pytest.mark.asyncio
async def test_request_changes_rejects_wrong_status() -> None:
    """Only awaiting_pm_review is a valid source — anything else returns None
    (the gateway spec gate rejects earlier; this is the service backstop)."""
    task = _build_task(status=TaskStatus.IN_PROGRESS)
    svc = TaskService(MagicMock(flush=AsyncMock()))
    _bind(svc, "get", AsyncMock(return_value=task))
    out = await svc.request_changes(uuid4(), task.id, "notes", ["issue"])
    assert out is None


@pytest.mark.asyncio
async def test_admin_set_status_pre_block_restore_syncs_active_claimant() -> None:
    """The pending/in_progress restore path re-owns the task to the pre-block
    dev — active_claimant_id must follow, or the restored dev's content writes
    bounce off the stale claimant exactly like the review-state wedge."""
    dev = uuid4()
    pm = uuid4()
    task = _build_task(
        status=TaskStatus.BLOCKED,
        assigned_to=pm,
        claimed_by=pm,
        active_claimant_id=pm,
        branch_name="feature/backend/abc--def",
        pre_block_state="in_progress",
        pre_block_assignee=dev,
    )
    svc = TaskService(MagicMock(flush=AsyncMock()))
    _bind(svc, "get", AsyncMock(return_value=task))
    out = await svc.admin_set_status(task.id, TaskStatus.IN_PROGRESS)
    assert out is task
    assert task.assigned_to == dev
    assert task.claimed_by == dev
    assert task.active_claimant_id == dev


@pytest.mark.asyncio
async def test_pre_block_restore_skips_revision_count_bump() -> None:
    """#101 Gap B: restoring a blocked task to its snapshotted needs_revision
    state is a RESTORE, not a rework bounce — ``revision_count`` must not
    increment. The rework counter counts rejections INTO needs_revision, and
    this task was already rejected before it was blocked; unblocking it back to
    needs_revision is the same rework cycle resuming, not a new one."""
    REWORK_BOUNCES_BEFORE_BLOCK = 2
    dev = uuid4()
    task = _build_task(
        status=TaskStatus.BLOCKED,
        assigned_to=dev,
        claimed_by=dev,
        branch_name="feature/backend/abc--def--ghi",
        pre_block_state="needs_revision",
        pre_block_assignee=dev,
        revision_count=REWORK_BOUNCES_BEFORE_BLOCK,
    )
    svc = TaskService(MagicMock(flush=AsyncMock()))
    _bind(svc, "get", AsyncMock(return_value=task))
    out = await svc.unblock_with_restore(task.id, uuid4(), restore=True)
    assert out is task
    assert task.status == TaskStatus.NEEDS_REVISION
    # A restore must NOT bump the rework counter — only a fresh rejection does.
    assert task.revision_count == REWORK_BOUNCES_BEFORE_BLOCK


@pytest.mark.asyncio
async def test_activate_batch_root_subtasks_emits_audit_for_activated_child() -> None:
    """#101 Gap A: ``_activate_batch_root_subtasks`` sets a held root-subtask
    BACKLOG→PENDING directly. No status change may bypass the audit log — the
    transition journey (the metric source of truth) must record the activation,
    or the child's lifecycle reconstruction silently drops its start point."""
    batch = uuid4()
    child = _build_task(
        status=TaskStatus.BACKLOG,
        batch_id=batch,
        team=Team.BOARD,
        task_type=TaskType.CODE,
    )
    umbrella = _build_task(
        status=TaskStatus.PENDING,
        batch_id=batch,
        parent_task_id=None,
        team=Team.BOARD,
        task_type=TaskType.PLANNING,
    )
    added: list[object] = []
    session = MagicMock()
    session.flush = AsyncMock()
    session.add.side_effect = added.append
    svc = TaskService(session)
    _bind(svc, "get_subtasks", AsyncMock(return_value=[child]))

    await svc._activate_batch_root_subtasks(umbrella)

    assert child.status == TaskStatus.PENDING
    rows = [r for r in added if isinstance(r, AuditLogTable)]
    assert any(
        r.event_type == "task.pending" and r.target_id == child.id for r in rows
    ), "batch root-subtask activation must emit a task.pending audit row"


@pytest.mark.asyncio
async def test_create_generates_ac_ids_and_carries_parent_ac_refs() -> None:
    # Every task gets one stable id per acceptance criterion (1:1), and a
    # decomposition child carries the parent AC ids it covers — the linkage the
    # coverage + roll-up gates rely on.
    svc = TaskService(
        MagicMock(add=MagicMock(), flush=AsyncMock(), execute=AsyncMock())
    )
    req = TaskCreateRequest(
        title="t",
        description="d",
        acceptance_criteria=["crit a", "crit b", "crit c"],
        team=Team.BACKEND,
        created_by=uuid4(),
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        estimated_complexity=Complexity.MEDIUM,
        project_id=uuid4(),
        parent_ac_refs=["parent-ac-1", "parent-ac-2"],
    )
    task = await svc.create(req)
    n = len(req.acceptance_criteria)
    assert len(task.acceptance_criteria_ids) == n
    assert len(set(task.acceptance_criteria_ids)) == n
    assert list(task.parent_ac_refs) == ["parent-ac-1", "parent-ac-2"]


def _svc_with_children(parent: object, child_rows: list[tuple]) -> TaskService:
    """TaskService whose get() returns `parent` and whose execute() yields the
    (status, parent_ac_refs) child rows the coverage primitive selects."""
    rows = MagicMock()
    rows.all.return_value = child_rows
    svc = TaskService(MagicMock(execute=AsyncMock(return_value=rows)))
    _bind(svc, "get", AsyncMock(return_value=parent))
    return svc


@pytest.mark.asyncio
async def test_uncovered_parent_acs_inert_without_declared_coverage() -> None:
    # No child declares parent_ac_refs -> coverage tracking inactive -> the gate
    # is inert (legacy/in-flight tasks are never blocked).
    parent = _build_task(
        acceptance_criteria=["a", "b"], acceptance_criteria_ids=["id-a", "id-b"]
    )
    svc = _svc_with_children(
        parent, [(TaskStatus.COMPLETED, []), (TaskStatus.COMPLETED, [])]
    )
    assert await svc.uncovered_parent_acceptance_criteria(parent.id) == []


@pytest.mark.asyncio
async def test_uncovered_parent_acs_flags_unsatisfied_and_ignores_cancelled() -> None:
    parent = _build_task(
        acceptance_criteria=["crit a", "crit b", "crit c"],
        acceptance_criteria_ids=["id-a", "id-b", "id-c"],
    )
    svc = _svc_with_children(
        parent,
        [
            (TaskStatus.COMPLETED, ["id-a"]),  # covers crit a
            (TaskStatus.CANCELLED, ["id-b"]),  # cancelled -> does NOT cover crit b
        ],
    )
    assert await svc.uncovered_parent_acceptance_criteria(parent.id) == [
        "crit b",
        "crit c",
    ]


@pytest.mark.asyncio
async def test_uncovered_parent_acs_empty_when_all_covered() -> None:
    parent = _build_task(
        acceptance_criteria=["a", "b"], acceptance_criteria_ids=["id-a", "id-b"]
    )
    svc = _svc_with_children(parent, [(TaskStatus.COMPLETED, ["id-a", "id-b"])])
    assert await svc.uncovered_parent_acceptance_criteria(parent.id) == []


@pytest.mark.asyncio
async def test_uncovered_parent_acs_recognizes_text_declared_coverage() -> None:
    # Regression (phantom re-delegation): a PM may declare covers_parent_criteria
    # by the criterion's full TEXT instead of its id. Matching is by id, so a
    # COMPLETED child that declared coverage by text used to read "uncovered" —
    # and the PM re-delegated the already-finished work as an empty phantom
    # subtask (0 commits, no PR) that could never close. _parent_ac_ref_sets now
    # normalizes text -> id so coverage counts regardless of how it was declared.
    parent = _build_task(
        acceptance_criteria=["crit a", "crit b"],
        acceptance_criteria_ids=["id-a", "id-b"],
    )
    svc = _svc_with_children(
        parent,
        [
            (TaskStatus.COMPLETED, ["crit a"]),  # declared by TEXT, not "id-a"
            (TaskStatus.COMPLETED, ["id-b"]),  # declared by id
        ],
    )
    assert await svc.uncovered_parent_acceptance_criteria(parent.id) == []


@pytest.mark.asyncio
async def test_parent_ac_coverage_normalizes_text_refs() -> None:
    # A text-declared coverage ref from a COMPLETED child surfaces as
    # claimed+verified, same as an id-declared one.
    parent = _build_task(
        acceptance_criteria=["crit a", "crit b"],
        acceptance_criteria_ids=["id-a", "id-b"],
    )
    svc = _svc_with_children(parent, [(TaskStatus.COMPLETED, ["crit a"])])
    cov = await svc.parent_ac_coverage(parent.id)
    assert cov[0] == {
        "id": "id-a",
        "text": "crit a",
        "claimed": True,
        "verified": True,
    }


@pytest.mark.asyncio
async def test_parent_ac_coverage_maps_claimed_and_verified() -> None:
    # Per-criterion visibility: a COMPLETED child both claims and verifies its
    # criterion; an in-flight child only claims; an untouched criterion is
    # neither. This is the digest a decomposing PM reads from the briefing.
    parent = _build_task(
        acceptance_criteria=["crit a", "crit b", "crit c"],
        acceptance_criteria_ids=["id-a", "id-b", "id-c"],
    )
    svc = _svc_with_children(
        parent,
        [
            (TaskStatus.COMPLETED, ["id-a"]),
            (TaskStatus.IN_PROGRESS, ["id-b"]),
        ],
    )
    assert await svc.parent_ac_coverage(parent.id) == [
        {"id": "id-a", "text": "crit a", "claimed": True, "verified": True},
        {"id": "id-b", "text": "crit b", "claimed": True, "verified": False},
        {"id": "id-c", "text": "crit c", "claimed": False, "verified": False},
    ]


@pytest.mark.asyncio
async def test_parent_ac_coverage_empty_without_ac_ids() -> None:
    # No stable ids on the parent (e.g. created before the linkage) -> nothing to
    # report; the digest stays absent rather than emitting bogus rows.
    parent = _build_task(acceptance_criteria=["a"], acceptance_criteria_ids=[])
    svc = _svc_with_children(parent, [(TaskStatus.IN_PROGRESS, ["id-a"])])
    assert await svc.parent_ac_coverage(parent.id) == []


@pytest.mark.asyncio
async def test_unclaimed_parent_acs_inert_without_declared_coverage() -> None:
    # The decomposition floor is opt-in: with no child declaring parent_ac_refs
    # it returns [] so a PM who never adopts coverage is never blocked at idle.
    parent = _build_task(
        acceptance_criteria=["a", "b"], acceptance_criteria_ids=["id-a", "id-b"]
    )
    svc = _svc_with_children(
        parent, [(TaskStatus.IN_PROGRESS, []), (TaskStatus.IN_PROGRESS, [])]
    )
    assert await svc.unclaimed_parent_acceptance_criteria(parent.id) == []


@pytest.mark.asyncio
async def test_unclaimed_parent_acs_counts_live_children_not_just_completed() -> None:
    # The distinction from the roll-up gate: an in-flight child *claims* its
    # criterion (so the decomposition floor is satisfied) even though it has not
    # yet *verified* it (so the roll-up gate still flags it). A cancelled child's
    # claim does not count -- its work died with it.
    parent = _build_task(
        acceptance_criteria=["crit a", "crit b", "crit c"],
        acceptance_criteria_ids=["id-a", "id-b", "id-c"],
    )
    rows = [
        (TaskStatus.IN_PROGRESS, ["id-a"]),  # live -> claims crit a
        (TaskStatus.CANCELLED, ["id-b"]),  # cancelled -> claim void
    ]
    # unclaimed: crit a is claimed by the live child; crit b (only the cancelled
    # child) and crit c (nobody) remain.
    assert await _svc_with_children(parent, rows).unclaimed_parent_acceptance_criteria(
        parent.id
    ) == ["crit b", "crit c"]
    # roll-up still flags crit a too: the live child has not COMPLETED it.
    assert await _svc_with_children(parent, rows).uncovered_parent_acceptance_criteria(
        parent.id
    ) == [
        "crit a",
        "crit b",
        "crit c",
    ]


def _svc_with_sibling_status_seq(rows: list[tuple]) -> TaskService:
    """TaskService whose execute() yields (status, sequence) sibling rows."""
    res = MagicMock()
    res.all.return_value = rows
    return TaskService(MagicMock(execute=AsyncMock(return_value=res)))


@pytest.mark.asyncio
async def test_earlier_incomplete_code_sibling_true_for_live_lower_seq() -> None:
    # A dev's queued code leaf (seq 2) is lane-held while its own seq-0 sibling
    # is still in flight — so it must not pin the dev to idle.
    task = _build_task(
        task_type=TaskType.CODE.value,
        parent_task_id=uuid4(),
        assigned_to=uuid4(),
        sequence=2,
    )
    svc = _svc_with_sibling_status_seq([(TaskStatus.IN_PROGRESS, 0)])
    assert await svc.has_earlier_incomplete_code_sibling(task) is True


@pytest.mark.asyncio
async def test_earlier_incomplete_code_sibling_false_when_earlier_terminal() -> None:
    task = _build_task(
        task_type=TaskType.CODE.value,
        parent_task_id=uuid4(),
        assigned_to=uuid4(),
        sequence=2,
    )
    svc = _svc_with_sibling_status_seq(
        [(TaskStatus.COMPLETED, 0), (TaskStatus.CANCELLED, 1)]
    )
    assert await svc.has_earlier_incomplete_code_sibling(task) is False


@pytest.mark.asyncio
async def test_earlier_incomplete_code_sibling_false_for_higher_seq_only() -> None:
    # A LATER sibling (seq 3) does not hold an earlier leaf (seq 2).
    task = _build_task(
        task_type=TaskType.CODE.value,
        parent_task_id=uuid4(),
        assigned_to=uuid4(),
        sequence=2,
    )
    svc = _svc_with_sibling_status_seq([(TaskStatus.IN_PROGRESS, 3)])
    assert await svc.has_earlier_incomplete_code_sibling(task) is False


@pytest.mark.asyncio
async def test_earlier_incomplete_code_sibling_non_code_short_circuits() -> None:
    # Only code queues sequence this way; a planning/doc leaf never queries.
    session = MagicMock(execute=AsyncMock())
    svc = TaskService(session)
    task = _build_task(
        task_type="planning",
        parent_task_id=uuid4(),
        assigned_to=uuid4(),
        sequence=2,
    )
    assert await svc.has_earlier_incomplete_code_sibling(task) is False
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_earlier_incomplete_code_sibling_false_when_fields_missing() -> None:
    task = _build_task(
        task_type=TaskType.CODE.value,
        parent_task_id=None,
        assigned_to=uuid4(),
        sequence=2,
    )
    svc = _svc_with_sibling_status_seq([(TaskStatus.IN_PROGRESS, 0)])
    assert await svc.has_earlier_incomplete_code_sibling(task) is False


@pytest.mark.asyncio
async def test_unblock_with_branch_resumes_in_progress() -> None:
    # A task claimed (has a branch) before it blocked resumes in_progress.
    task = _build_task(
        status=TaskStatus.BLOCKED,
        branch_name="feature/backend/abc12345",
        blocker_raised_by=uuid4(),
    )
    svc = TaskService(MagicMock(flush=AsyncMock()))
    _bind(svc, "get", AsyncMock(return_value=task))
    _bind(svc, "_index_lifecycle_event_background", AsyncMock())
    out = await svc.unblock(task.id)
    assert out is task
    assert task.status == TaskStatus.IN_PROGRESS


# ---------------------------------------------------------------------------
# cell_pm_complete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cell_pm_complete_appends_merge_commit() -> None:
    task = _build_task(commits=[{"hash": "old", "message": "earlier"}])
    svc = TaskService(MagicMock(flush=AsyncMock()))
    complete_mock = AsyncMock(return_value=task)
    _bind(svc, "get", AsyncMock(return_value=task))
    _bind(svc, "complete", complete_mock)
    pm_id = uuid4()
    await svc.cell_pm_complete(pm_id, task.id, "all good", merge_commit="deadbeef")
    assert task.commits[-1]["hash"] == "deadbeef"
    assert task.commits[-1]["kind"] == "merge"
    complete_mock.assert_awaited_once_with(task.id, agent_id=pm_id)


@pytest.mark.asyncio
async def test_cell_pm_complete_skips_merge_when_none() -> None:
    task = _build_task(commits=[])
    svc = TaskService(MagicMock(flush=AsyncMock()))
    complete_mock = AsyncMock(return_value=task)
    _bind(svc, "get", AsyncMock(return_value=task))
    _bind(svc, "complete", complete_mock)
    await svc.cell_pm_complete(uuid4(), task.id, "all good", merge_commit=None)
    assert task.commits == []


# ---------------------------------------------------------------------------
# escalate / escalate_up_to_role
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_escalate_returns_none_when_no_target_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _build_task()
    agent = MagicMock(id=uuid4(), slug="lone-agent")
    agent_result = MagicMock()
    agent_result.scalar_one_or_none.return_value = agent

    session = MagicMock()
    session.execute = AsyncMock(return_value=agent_result)
    session.flush = AsyncMock()
    svc = TaskService(session)
    _bind(svc, "get", AsyncMock(return_value=task))
    monkeypatch.setattr(
        "roboco.agents_config.get_escalation_target", lambda _slug: None
    )
    out = await svc.escalate(uuid4(), task.id, "stuck")
    assert out is None


@pytest.mark.asyncio
async def test_escalate_up_to_role_returns_none_for_unknown_role() -> None:
    task = _build_task()
    agent = MagicMock(id=uuid4(), slug="some-agent")
    agent_result = MagicMock()
    agent_result.scalar_one_or_none.return_value = agent

    session = MagicMock()
    session.execute = AsyncMock(return_value=agent_result)
    svc = TaskService(session)
    _bind(svc, "get", AsyncMock(return_value=task))
    out = await svc.escalate_up_to_role(uuid4(), task.id, "bogus_role", "reason")
    assert out is None


# ---------------------------------------------------------------------------
# _ensure_branch_for_task — coordination/fan-out tasks do no git
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_branch_returns_existing_branch() -> None:
    """An already-branched task short-circuits before any project check."""
    svc = TaskService(MagicMock())
    task = MagicMock(branch_name="feature/backend/abc12345", project_id=None)
    assert (
        await svc._ensure_branch_for_task(task, uuid4()) == "feature/backend/abc12345"
    )


@pytest.mark.asyncio
async def test_ensure_branch_coordination_root_cuts_integration_branch() -> None:
    """A product-backed root cuts feature/main_pm/{root} in each product repo."""
    svc = TaskService(MagicMock())
    task = MagicMock(branch_name=None, project_id=None, product_id=uuid4())
    create_in_project = AsyncMock(return_value="feature/main_pm/root1234")
    _bind(svc, "_create_branch_in_project", create_in_project)
    product_svc = MagicMock(distinct_project_ids=AsyncMock(return_value=[uuid4()]))
    project_svc = MagicMock(get=AsyncMock(return_value=MagicMock()))
    with (
        patch("roboco.services.product.get_product_service", return_value=product_svc),
        patch("roboco.services.project.get_project_service", return_value=project_svc),
    ):
        result = await svc._ensure_branch_for_task(task, uuid4())
    assert result == "feature/main_pm/root1234"
    create_in_project.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_branch_coordination_root_no_cell_map_stays_branchless() -> None:
    """A product with no cell->repo map yet stays branchless (graceful fallback)."""
    svc = TaskService(MagicMock())
    task = MagicMock(branch_name=None, project_id=None, product_id=uuid4())
    product_svc = MagicMock(distinct_project_ids=AsyncMock(return_value=[]))
    with patch("roboco.services.product.get_product_service", return_value=product_svc):
        result = await svc._ensure_branch_for_task(task, uuid4())
    assert result == ""


@pytest.mark.asyncio
async def test_ensure_branch_cell_map_root_cuts_integration_branch_per_project() -> (
    None
):
    """An ad-hoc cell_projects root cuts feature/main_pm/{root} in each distinct
    project the map spans — the product-root path with the map sourced from the
    task instead of a Product."""
    svc = TaskService(MagicMock())
    be_proj, fe_proj = uuid4(), uuid4()
    cell_map = [
        SimpleNamespace(team=Team.BACKEND, project_id=be_proj),
        SimpleNamespace(team=Team.FRONTEND, project_id=fe_proj),
    ]
    task = MagicMock(
        branch_name=None,
        project_id=None,
        product_id=None,
        batch_id=uuid4(),
        parent_task_id=uuid4(),
        cell_projects=cell_map,
    )
    create_in_project = AsyncMock(return_value="feature/main_pm/root1234")
    _bind(svc, "_create_branch_in_project", create_in_project)
    project_svc = MagicMock(get=AsyncMock(return_value=MagicMock()))
    with patch("roboco.services.project.get_project_service", return_value=project_svc):
        result = await svc._ensure_branch_for_task(task, uuid4())
    assert result == "feature/main_pm/root1234"
    # one integration branch per distinct project in the map (here 2 cells, 2 projects)
    assert create_in_project.await_count == len(cell_map)


@pytest.mark.asyncio
async def test_distinct_projects_for_task_dedupes_cell_map_by_project_id() -> None:
    """Two cells mapping at the same project (the monorepo case) yield ONE
    integration branch, not two — mirroring product distinct_project_ids."""
    svc = TaskService(MagicMock())
    shared = uuid4()
    task = MagicMock(
        project_id=None,
        product_id=None,
        cell_projects=[
            SimpleNamespace(team=Team.FRONTEND, project_id=shared),
            SimpleNamespace(team=Team.BACKEND, project_id=shared),
        ],
    )
    ids = await svc._distinct_projects_for_task(task)
    assert ids == [shared]


@pytest.mark.asyncio
async def test_ensure_branch_raises_when_neither_project_nor_product() -> None:
    """A task with neither a project, a product, nor a cell map is misconfigured."""
    svc = TaskService(MagicMock())
    task = MagicMock(
        branch_name=None,
        project_id=None,
        product_id=None,
        cell_projects=[],
        batch_id=None,
        parent_task_id=None,
    )
    with pytest.raises(ValueError, match="project_id"):
        await svc._ensure_branch_for_task(task, uuid4())


# ---------------------------------------------------------------------------
# _finalize_claim — branch-creation failure rollback (F060)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_claim_rollback_emits_reversal_audit() -> None:
    """When branch creation fails mid-claim, the rollback must emit a REVERSAL
    audit row (CLAIMED -> original) so the audit journey matches the real
    (rolled-back) task state. The audit service writes on its own connection, so
    the forward `task.claimed` row is NOT undone by the rollback's flush.
    """
    session = MagicMock()
    session.flush = AsyncMock()
    svc = TaskService(session)

    task = _build_task(
        status=TaskStatus.PENDING,
        branch_name=None,  # forces the branch-creation path
        project_id=uuid4(),
        product_id=None,
        batch_id=None,
        parent_task_id=None,
        cell_projects=[],  # a plain code task, not a branchless coordination root
        pr_created=False,
        pr_number=None,
    )
    agent = MagicMock(id=uuid4(), role=AgentRole.DEVELOPER)

    audit_calls: list[dict[str, Any]] = []

    def _capture(
        _task: object, *, from_status: str, to_status: str, **_kw: object
    ) -> None:
        audit_calls.append({"from": from_status, "to": to_status})

    _bind(svc, "_emit_status_transition_audit", _capture)
    _bind(
        svc,
        "_ensure_branch_for_task",
        AsyncMock(side_effect=RuntimeError("branch boom")),
    )

    with pytest.raises(RuntimeError, match="branch boom"):
        await svc._finalize_claim(task, agent, agent.id)

    # The task reverted to its pre-claim status (the existing behavior).
    assert task.status == TaskStatus.PENDING
    # The forward claim row was emitted...
    assert {"from": "pending", "to": "claimed"} in audit_calls
    # ...AND the reversal row is emitted so the journey's last event matches
    # the rolled-back state (the F060 fix). Before the fix this was missing.
    assert {"from": "claimed", "to": "pending"} in audit_calls


@pytest.mark.asyncio
async def test_emit_status_transition_audit_writes_in_session_atomically() -> None:
    """The status-transition audit row is written into the CALLER's session (same
    transaction as the transition), not fire-and-forget on a separate connection,
    so it commits/rolls back atomically with the transition and cannot diverge
    from real state. Asserted at the unit level: the row is ``session.add``-ed
    (same txn) and NO fire-and-forget background task is spawned.
    """
    session = MagicMock()
    added: list[object] = []
    session.add.side_effect = added.append
    svc = TaskService(session)
    prior_bg = set(svc._background_tasks)

    task = MagicMock(id=uuid4(), claimed_by=uuid4(), team=Team.BACKEND)

    svc._emit_status_transition_audit(
        task,
        from_status="pending",
        to_status="claimed",
        agent_role="developer",
        audit_agent_id=None,
    )

    # The audit row is added to the CALLER's session (same transaction) — not
    # dispatched to a separate fire-and-forget connection.
    rows = [r for r in added if isinstance(r, AuditLogTable)]
    assert len(rows) == 1
    row = rows[0]
    assert row.event_type == "task.claimed"
    assert row.target_type == "task"
    assert row.target_id == task.id
    assert row.details["from_status"] == "pending"
    assert row.details["to_status"] == "claimed"
    assert row.details["agent_role"] == "developer"
    assert row.details["team"] == "backend"
    # The claiming agent is attributed (resolved from claimed_by).
    assert row.agent_id == task.claimed_by
    # No fire-and-forget audit task was spawned (the old decoupled path).
    assert svc._background_tasks == prior_bg


# ---------------------------------------------------------------------------
# _resolve_doc_abspath — normalize documenter-supplied paths under /app/docs
# ---------------------------------------------------------------------------


def test_resolve_doc_abspath_strips_redundant_docs_prefix() -> None:
    """A `docs/`-rooted relative path must not double the base segment.

    DOCS_BASE_PATH is /app/docs; joining it with `docs/design/x.md` produced
    /app/docs/docs/design/x.md, so the file was never found and never indexed.
    """
    assert (
        TaskService._resolve_doc_abspath("docs/design/spec.md")
        == "/app/docs/design/spec.md"
    )


def test_resolve_doc_abspath_keeps_plain_relative_path() -> None:
    """A relative path with no `docs/` prefix joins under the base unchanged."""
    assert (
        TaskService._resolve_doc_abspath("design/spec.md") == "/app/docs/design/spec.md"
    )


def test_resolve_doc_abspath_passes_absolute_path_through() -> None:
    """An absolute path already correctly rooted under the base is unchanged."""
    assert (
        TaskService._resolve_doc_abspath("/app/docs/design/spec.md")
        == "/app/docs/design/spec.md"
    )


def test_resolve_doc_abspath_collapses_doubled_absolute_docs() -> None:
    """An absolute path that doubled the base segment is collapsed.

    The documenter sometimes records `/app/docs/docs/...`; previously it was
    returned verbatim and never resolved on disk (the recurring "Source not
    found" warning).
    """
    assert (
        TaskService._resolve_doc_abspath("/app/docs/docs/backend/api/prompter.md")
        == "/app/docs/backend/api/prompter.md"
    )


def test_resolve_doc_abspath_leaves_external_absolute_path() -> None:
    """An absolute path outside the docs root is left as-is for the indexer to skip."""
    external = "/data/workspaces/panel/frontend/fe-dev-1/src/page.tsx"
    assert TaskService._resolve_doc_abspath(external) == external


@pytest.mark.asyncio
async def test_update_skips_none_to_protect_partial_callers() -> None:
    """update() must skip None values, not write them.

    Callers pass field=dict.get('x'), which is None when the key is absent —
    e.g. the board-redraft update_live_draft path passes title/acceptance_criteria
    that way. Without the None-skip guard those None values would null-wipe
    existing data. Explicit clearing is the update ROUTE's job (a field
    whitelist), never this shared service method. Locks that contract so the
    guard can't be silently removed again.
    """
    task = SimpleNamespace(title="original", acceptance_criteria=["keep me"])
    svc = TaskService(MagicMock(flush=AsyncMock()))
    with patch.object(svc, "get", AsyncMock(return_value=task)):
        result: Any = await svc.update(
            uuid4(), title="updated", acceptance_criteria=None
        )

    assert result is task
    assert task.title == "updated"  # explicit, non-None value is applied
    assert task.acceptance_criteria == ["keep me"]  # None skipped, not wiped


# ---------------------------------------------------------------------------
# M20: admin_set_status terminal guard + skip revision bump under force
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_set_status_refuses_terminal_to_non_terminal_without_force(
    db_session,
) -> None:
    agent = AgentTable(
        id=uuid4(),
        name="A",
        slug=f"a-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(agent)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="P",
        slug=f"p-{uuid4().hex[:6]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=agent.id,
    )
    db_session.add(project)
    await db_session.flush()
    tid = uuid4()
    db_session.add(
        TaskTable(
            id=tid,
            title="t",
            description="d",
            acceptance_criteria=["done"],
            status=TaskStatus.COMPLETED,
            priority=2,
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.LOW,
            team=Team.BACKEND,
            confirmed_by_human=True,
            project_id=project.id,
            created_by=agent.id,
            branch_name="feature/x",
            revision_count=2,
        )
    )
    await db_session.flush()
    svc = get_task_service(db_session)
    with pytest.raises(TaskLifecycleError):
        await svc.admin_set_status(tid, TaskStatus.NEEDS_REVISION)


@pytest.mark.asyncio
async def test_admin_set_status_force_no_revision_bump(
    db_session,
) -> None:
    agent = AgentTable(
        id=uuid4(),
        name="A",
        slug=f"a-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(agent)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="P",
        slug=f"p-{uuid4().hex[:6]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=agent.id,
    )
    db_session.add(project)
    await db_session.flush()
    tid = uuid4()
    db_session.add(
        TaskTable(
            id=tid,
            title="t",
            description="d",
            acceptance_criteria=["done"],
            status=TaskStatus.COMPLETED,
            priority=2,
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.LOW,
            team=Team.BACKEND,
            confirmed_by_human=True,
            project_id=project.id,
            created_by=agent.id,
            branch_name="feature/x",
            revision_count=2,
        )
    )
    await db_session.flush()
    svc = get_task_service(db_session)
    await svc.admin_set_status(
        tid, TaskStatus.NEEDS_REVISION, force=True, actor_id=agent.id
    )
    row = (
        await db_session.execute(select(TaskTable).where(TaskTable.id == tid))
    ).scalar_one()
    assert row.status == TaskStatus.NEEDS_REVISION
    expected_revision_count = 2
    assert row.revision_count == expected_revision_count, (
        "admin force terminal->needs_revision bumped revision_count — "
        "admin recovery must not be counted as rework in metrics"
    )
