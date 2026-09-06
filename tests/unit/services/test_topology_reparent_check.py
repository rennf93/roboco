"""Postmortem follow-up: re-run the PR-base/parent-topology check whenever a
task is re-parented via ``PATCH /api/tasks/{id}`` (the ``parent_task_id``
write-through in ``TaskService.update``), not just at the terminal verbs.

Fixtures are modeled on incident 5612b225/PR #856 (a task restructured out
of a parentless root left its PR based on 'slave' instead of an
intermediate root branch) and the same-class incidents on record
(6866e888, 6b9e19aa, 7de89c6e).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.foundation.policy.content import markers
from roboco.services.task import TaskService


def _bind(svc: TaskService, name: str, value: object) -> None:
    object.__setattr__(svc, name, value)


def _service() -> TaskService:
    session = MagicMock()
    session.flush = AsyncMock()
    session.get = AsyncMock(return_value=None)
    return TaskService(session)


def _task(*, parent_task_id: object, branch_name: str) -> MagicMock:
    return MagicMock(
        id=uuid4(),
        parent_task_id=parent_task_id,
        branch_name=branch_name,
        batch_id=None,
        acceptance_criteria=None,
        acceptance_criteria_ids=None,
        orchestration_markers=None,
        work_session_id=uuid4(),
    )


@pytest.mark.asyncio
async def test_reparent_records_topology_issue_when_pr_stranded_on_head() -> None:
    """The 5612b225/PR #856 shape: re-parenting under a real coordination
    root while the task's branch/PR base is still the integration branch
    records the mismatch immediately instead of only at complete()."""
    svc = _service()
    new_parent_id = uuid4()
    parent = MagicMock(branch_name="feature/main_pm/root0001")
    task = _task(parent_task_id=None, branch_name="feature/backend/child0001")
    _bind(svc, "get", AsyncMock(side_effect=[task, parent]))
    _bind(svc, "recorded_pr_base", AsyncMock(return_value="slave"))
    _bind(svc, "project_default_branch_for_task", AsyncMock(return_value="slave"))

    result = await svc.update(task.id, parent_task_id=new_parent_id)

    assert result is task
    issue = markers.get_topology_issue(task)
    assert issue is not None
    assert issue["shape"] == "parented_base_is_head"
    assert issue["expected_base"] == "feature/main_pm/root0001"
    assert issue["actual_base"] == "slave"


@pytest.mark.asyncio
async def test_reparent_onto_matching_branch_clears_prior_issue() -> None:
    """A re-parent that RESOLVES a prior mismatch (the base now matches the
    new parent's own branch) clears the marker instead of leaving it
    stale."""
    svc = _service()
    new_parent_id = uuid4()
    parent = MagicMock(branch_name="feature/main_pm/root0001")
    task = _task(parent_task_id=None, branch_name="feature/backend/child0001")
    markers.set_topology_issue(task, {"shape": "stale", "expected_base": "x"})
    _bind(svc, "get", AsyncMock(side_effect=[task, parent]))
    _bind(svc, "recorded_pr_base", AsyncMock(return_value="feature/main_pm/root0001"))

    await svc.update(task.id, parent_task_id=new_parent_id)

    assert markers.get_topology_issue(task) is None


@pytest.mark.asyncio
async def test_update_without_parent_change_skips_topology_recheck() -> None:
    """A PATCH that never touches parent_task_id (or is a no-op reassert of
    the same value) does not re-run the check at all."""
    svc = _service()
    parent_id = uuid4()
    task = _task(parent_task_id=parent_id, branch_name="feature/backend/child0001")
    get_mock = AsyncMock(return_value=task)
    _bind(svc, "get", get_mock)
    recorded_mock = AsyncMock()
    _bind(svc, "recorded_pr_base", recorded_mock)

    await svc.update(task.id, title="a new title")

    # Only the initial fetch — no re-parent, so no topology recheck (no
    # second `get` call for a parent lookup, no `recorded_pr_base` call).
    assert get_mock.await_count == 1
    recorded_mock.assert_not_called()
    assert markers.get_topology_issue(task) is None
