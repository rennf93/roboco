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
    _ldef = base["journal"].latest_decision_at.return_value
    if type(_ldef).__name__ in ("MagicMock", "AsyncMock"):
        base["journal"].latest_decision_at.return_value = datetime.now(UTC)
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
