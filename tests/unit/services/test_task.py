"""Unit tests for TaskService gateway-backfill methods.

These cover the methods the Choreographer calls into; full end-to-end
behavior is exercised by the gateway tests. Each test mocks the DB
session boundary and checks the method's contract.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    BlockerResolverType,
    TaskStatus,
    Team,
)
from roboco.services.task import GatewayAgentView, TaskService


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
    task = _build_task(status=TaskStatus.AWAITING_QA)
    svc = TaskService(MagicMock(flush=AsyncMock()))
    _bind(svc, "get", AsyncMock(return_value=task))
    qa_id = uuid4()
    out = await svc.qa_claim(qa_id, task.id)
    assert out is task
    assert task.assigned_to == qa_id
    assert task.claimed_by == qa_id
    assert isinstance(task.claimed_at, datetime)


@pytest.mark.asyncio
async def test_qa_claim_rejects_wrong_status() -> None:
    task = _build_task(status=TaskStatus.IN_PROGRESS)
    svc = TaskService(MagicMock(flush=AsyncMock()))
    _bind(svc, "get", AsyncMock(return_value=task))
    out = await svc.qa_claim(uuid4(), task.id)
    assert out is None


@pytest.mark.asyncio
async def test_doc_claim_sets_assignment_on_awaiting_documentation() -> None:
    task = _build_task(status=TaskStatus.AWAITING_DOCUMENTATION)
    svc = TaskService(MagicMock(flush=AsyncMock()))
    _bind(svc, "get", AsyncMock(return_value=task))
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
async def test_ensure_branch_raises_when_neither_project_nor_product() -> None:
    """A task with neither a project nor a product is genuinely misconfigured."""
    svc = TaskService(MagicMock())
    task = MagicMock(branch_name=None, project_id=None, product_id=None)
    with pytest.raises(ValueError, match="project_id"):
        await svc._ensure_branch_for_task(task, uuid4())


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
    svc.get = AsyncMock(return_value=task)

    result = await svc.update(uuid4(), title="updated", acceptance_criteria=None)

    assert result is task
    assert task.title == "updated"  # explicit, non-None value is applied
    assert task.acceptance_criteria == ["keep me"]  # None skipped, not wiped
