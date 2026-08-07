"""Decomposition-coverage gate: delegate rejects a child that doesn't map to
the parent's acceptance criteria, and a successful delegate reports the
parent's remaining coverage gaps in evidence.

Live failure this closes (see CLAUDE.md "delegate" section): unmapped
children were only ever caught late, at submit_up's roll-up gate — after a
whole wave of subtasks had already run. Moving the mapping check to delegate
time surfaces "child covers nothing" / "ref doesn't match a real criterion"
before any subtask is created.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    Complexity,
    TaskNature,
    TaskStatus,
    TaskType,
    Team,
)
from roboco.services.gateway.choreographer import (
    Choreographer,
    ChoreographerDeps,
    DelegateInputs,
)
from roboco.services.task import get_task_service
from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


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
    _ldef = base["journal"].latest_decision_at.return_value
    if type(_ldef).__name__ in ("MagicMock", "AsyncMock"):
        base["journal"].latest_decision_at.return_value = datetime.now(UTC)
    # _ensure_pm_decision's journal write is savepoint-guarded — an
    # unconfigured AsyncMock's begin_nested() call returns a raw unawaited
    # coroutine, which `async with` cannot use. Only stub it for a mocked
    # task service: one test below passes a REAL TaskService (get_task_service)
    # over a live db_session, whose genuine begin_nested must stay intact.
    if isinstance(base["task"], AsyncMock | MagicMock):
        base["task"].session.begin_nested = MagicMock(
            return_value=MagicMock(
                __aenter__=AsyncMock(return_value=None),
                __aexit__=AsyncMock(return_value=False),
            )
        )
    return ChoreographerDeps(**base)


def _parent_with_criteria(pm_id: Any) -> MagicMock:
    return MagicMock(
        id=uuid4(),
        project_id=uuid4(),
        status="in_progress",
        assigned_to=pm_id,
        team="backend",
        quick_context="Decomposition planned; cells implement their slice next.",
        acceptance_criteria=["Criterion A", "Criterion B"],
        acceptance_criteria_ids=["id-a", "id-b"],
    )


def _inputs(**kw: Any) -> DelegateInputs:
    base: dict[str, Any] = {
        "title": "Implement endpoint",
        "description": "Add /v1/foo endpoint with tests",
        "assigned_to": "be-dev-1",
        "team": "backend",
        "task_type": "code",
        "nature": "technical",
        "acceptance_criteria": ["GET /v1/foo returns 200 with body"],
        "intends_to_touch": ["backend/api/routers/foo.py"],
    }
    base.update(kw)
    return DelegateInputs(**base)


@pytest.mark.asyncio
async def test_delegate_rejects_child_with_no_mapping() -> None:
    """A parent with real ACs rejects a child that maps to none of them."""
    pm_id = uuid4()
    parent = _parent_with_criteria(pm_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = parent
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.get_subtasks.return_value = []
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.delegate(pm_id, parent.id, _inputs(title="Orphan slice"))
    body = env.as_dict()
    assert body["error"] == "invalid_state", body
    assert "Orphan slice" in body["message"]
    assert "no covers_parent_criteria" in body["message"]
    assert "Criterion A" in body["remediate"] and "Criterion B" in body["remediate"]
    task_svc.create_subtask.assert_not_awaited()
    task_svc.unknown_ac_refs.assert_not_called()


@pytest.mark.asyncio
async def test_delegate_rejects_unresolvable_ref_lists_valid_criteria() -> None:
    """A ref matching neither a criterion id nor its exact text is rejected,
    with the parent's real criteria named so the PM can pick a valid one."""
    pm_id = uuid4()
    parent = _parent_with_criteria(pm_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = parent
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.get_subtasks.return_value = []
    task_svc.unknown_ac_refs = MagicMock(return_value=["bogus-ref"])
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.delegate(
        pm_id,
        parent.id,
        _inputs(title="Endpoint slice", covers_parent_criteria=["bogus-ref"]),
    )
    body = env.as_dict()
    assert body["error"] == "invalid_state", body
    assert "Endpoint slice" in body["message"]
    assert "bogus-ref" in body["message"]
    assert "Criterion A" in body["remediate"] and "Criterion B" in body["remediate"]
    task_svc.create_subtask.assert_not_awaited()
    task_svc.unknown_ac_refs.assert_called_once_with(parent, ["bogus-ref"])


@pytest.mark.asyncio
async def test_delegate_rejects_multiple_unresolvable_refs_in_one_envelope() -> None:
    """Every unresolvable ref is named in the one rejection, not just the first."""
    pm_id = uuid4()
    parent = _parent_with_criteria(pm_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = parent
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.get_subtasks.return_value = []
    task_svc.unknown_ac_refs = MagicMock(return_value=["bogus-one", "bogus-two"])
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.delegate(
        pm_id,
        parent.id,
        _inputs(covers_parent_criteria=["bogus-one", "bogus-two"]),
    )
    body = env.as_dict()
    assert body["error"] == "invalid_state", body
    assert "bogus-one" in body["message"]
    assert "bogus-two" in body["message"]
    task_svc.create_subtask.assert_not_awaited()


@pytest.mark.asyncio
async def test_delegate_success_evidence_carries_covered_and_uncovered() -> None:
    """A resolvable mapping creates the subtask and reports the parent's
    coverage split in evidence, using the same primitive submit_up checks."""
    pm_id = uuid4()
    parent = _parent_with_criteria(pm_id)
    new_task = MagicMock(id=uuid4())
    task_svc = AsyncMock()
    task_svc.get.return_value = parent
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.get_subtasks.return_value = []
    task_svc.unknown_ac_refs = MagicMock(return_value=[])
    task_svc.create_subtask.return_value = new_task
    task_svc.uncovered_parent_acceptance_criteria.return_value = ["Criterion B"]
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.delegate(pm_id, parent.id, _inputs(covers_parent_criteria=["id-a"]))
    body = env.as_dict()
    assert body["error"] is None, body
    assert body["status"] == "created"
    assert body["evidence"]["parent_ac_coverage"] == {
        "covered": ["Criterion A"],
        "uncovered": ["Criterion B"],
    }


@pytest.mark.asyncio
async def test_delegate_wave_leaving_acs_uncovered_still_succeeds() -> None:
    """No full-coverage hard gate at delegate: a wave may leave criteria for a
    later delegate call — the child is still created, gaps just get listed."""
    pm_id = uuid4()
    parent = _parent_with_criteria(pm_id)
    parent.acceptance_criteria = ["Criterion A", "Criterion B", "Criterion C"]
    parent.acceptance_criteria_ids = ["id-a", "id-b", "id-c"]
    new_task = MagicMock(id=uuid4())
    task_svc = AsyncMock()
    task_svc.get.return_value = parent
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.get_subtasks.return_value = []
    task_svc.unknown_ac_refs = MagicMock(return_value=[])
    task_svc.create_subtask.return_value = new_task
    task_svc.uncovered_parent_acceptance_criteria.return_value = [
        "Criterion B",
        "Criterion C",
    ]
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.delegate(pm_id, parent.id, _inputs(covers_parent_criteria=["id-a"]))
    body = env.as_dict()
    assert body["error"] is None, body
    coverage = body["evidence"]["parent_ac_coverage"]
    assert coverage["covered"] == ["Criterion A"]
    assert coverage["uncovered"] == ["Criterion B", "Criterion C"]
    task_svc.create_subtask.assert_awaited_once()


@pytest.mark.asyncio
async def test_ac_coverage_guard_heals_empty_ids_parent_in_place(
    db_session: AsyncSession,
) -> None:
    """A criteria-bearing parent whose ``acceptance_criteria_ids`` is empty
    (a legacy row from before every AC rewrite reconciled ids) is
    self-healed to 1:1 by the reject path itself, against a real DB row —
    not just papered over in the rendered hint. Regression coverage for the
    adversarial finding on commit d259476b: the pre-fix guard rendered a
    literal ``'<id>'`` placeholder and an empty criteria listing on exactly
    this row shape, re-rejecting a PM who copy-pasted it verbatim."""
    agent = AgentTable(
        id=uuid4(),
        name="PM",
        slug=f"pm-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
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
            title="parent",
            description="d",
            acceptance_criteria=["Criterion A", "Criterion B"],
            acceptance_criteria_ids=[],
            status=TaskStatus.IN_PROGRESS,
            priority=2,
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.LOW,
            team=Team.BACKEND,
            confirmed_by_human=True,
            project_id=project.id,
            created_by=agent.id,
            branch_name="feature/x",
            assigned_to=agent.id,
        )
    )
    await db_session.flush()
    task_svc = get_task_service(db_session)
    parent = await task_svc.get(tid)
    assert parent is not None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c._delegate_ac_coverage_guard(parent, _inputs(title="Orphan slice"))

    assert env is not None
    body = env.as_dict()
    assert "'<id>'" not in body["remediate"]
    assert "Criterion A" in body["remediate"]
    assert "Criterion B" in body["remediate"]

    row = (
        await db_session.execute(select(TaskTable).where(TaskTable.id == tid))
    ).scalar_one()
    assert len(row.acceptance_criteria_ids) == len(row.acceptance_criteria)
    assert len(set(row.acceptance_criteria_ids)) == len(row.acceptance_criteria_ids)
