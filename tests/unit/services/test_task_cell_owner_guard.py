"""A cell implementation task may never be owned by a board / Main-PM role.

Live failure: through the escalation chain a cell task's ``assigned_to``
climbed to the Product Owner (a board role), which cannot drive cell work
(dev -> QA -> docs). When the upstream cleared, the task revived under that
owner and deadlocked paused. These guard the two service-side write paths:
``reassign`` (the handoff chokepoint) and ``_unblock_dependents`` (the
dependency-clear revival).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.models.base import TaskStatus
from roboco.services.task import TaskService


def _task(**overrides: object) -> MagicMock:
    base: dict[str, object] = {
        "id": uuid4(),
        "team": "backend",
        "status": TaskStatus.PENDING,
        "assigned_to": None,
        "claimed_by": None,
        "dependency_ids": [],
    }
    base.update(overrides)
    return MagicMock(**base)


def _service(execute_returns: object = None) -> TaskService:
    session = MagicMock()
    session.execute = AsyncMock(return_value=execute_returns)
    session.flush = AsyncMock()
    return TaskService(session)


def _bind(svc: TaskService, name: str, value: object) -> None:
    object.__setattr__(svc, name, value)


# ---------------------------------------------------------------------------
# _is_noncell_owner predicate
# ---------------------------------------------------------------------------


def test_predicate_blocks_board_on_cell_task() -> None:
    task = _task(team="frontend")
    with patch("roboco.agents_config.get_agent_role", return_value="product_owner"):
        assert TaskService._is_noncell_owner(task, uuid4()) is True


def test_predicate_blocks_main_pm_on_cell_task() -> None:
    task = _task(team="backend")
    with patch("roboco.agents_config.get_agent_role", return_value="main_pm"):
        assert TaskService._is_noncell_owner(task, uuid4()) is True


def test_predicate_allows_cell_agent() -> None:
    task = _task(team="backend")
    with patch("roboco.agents_config.get_agent_role", return_value="developer"):
        assert TaskService._is_noncell_owner(task, uuid4()) is False


def test_predicate_ignores_coordination_task() -> None:
    task = _task(team="main_pm")
    with patch("roboco.agents_config.get_agent_role", return_value="main_pm"):
        assert TaskService._is_noncell_owner(task, uuid4()) is False


def test_predicate_none_assignee() -> None:
    assert TaskService._is_noncell_owner(_task(), None) is False


# ---------------------------------------------------------------------------
# reassign guard (#2b)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reassign_clears_board_owner_on_cell_task() -> None:
    task = _task(team="backend")
    svc = _service()
    _bind(svc, "get", AsyncMock(return_value=task))
    with patch("roboco.agents_config.get_agent_role", return_value="product_owner"):
        result = await svc.reassign(task.id, uuid4())
    assert result is task
    assert task.assigned_to is None
    assert task.claimed_by is None


@pytest.mark.asyncio
async def test_reassign_allows_same_cell_handoff() -> None:
    task = _task(team="backend")
    svc = _service()
    _bind(svc, "get", AsyncMock(return_value=task))
    qa_id = uuid4()
    with patch("roboco.agents_config.get_agent_role", return_value="qa"):
        await svc.reassign(task.id, qa_id)
    assert task.assigned_to == qa_id
    assert task.claimed_by == qa_id


# ---------------------------------------------------------------------------
# _unblock_dependents revival re-homing (#4)
# ---------------------------------------------------------------------------


def _execute_returning(tasks: list[MagicMock]) -> MagicMock:
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=tasks)
    result = MagicMock()
    result.scalars = MagicMock(return_value=scalars)
    return result


@pytest.mark.asyncio
async def test_unblock_rehomes_misowned_cell_task_to_pending() -> None:
    completed = uuid4()
    task = _task(
        team="backend",
        status=TaskStatus.BLOCKED,
        dependency_ids=[completed],
        assigned_to=uuid4(),
    )
    svc = _service(_execute_returning([task]))
    set_status = MagicMock()
    _bind(svc, "_validate_and_set_status", set_status)
    with patch("roboco.agents_config.get_agent_role", return_value="product_owner"):
        await svc._unblock_dependents(completed)
    assert task.assigned_to is None
    assert task.claimed_by is None
    set_status.assert_called_once()
    assert set_status.call_args.args[1] == TaskStatus.PENDING


@pytest.mark.asyncio
async def test_unblock_revives_correctly_owned_task_in_progress() -> None:
    completed = uuid4()
    owner = uuid4()
    task = _task(
        team="backend",
        status=TaskStatus.BLOCKED,
        dependency_ids=[completed],
        assigned_to=owner,
    )
    svc = _service(_execute_returning([task]))
    set_status = MagicMock()
    _bind(svc, "_validate_and_set_status", set_status)
    with patch("roboco.agents_config.get_agent_role", return_value="developer"):
        await svc._unblock_dependents(completed)
    # Correct owner preserved, revived in_progress (unchanged behaviour).
    assert task.assigned_to == owner
    set_status.assert_called_once()
    assert set_status.call_args.args[1] == TaskStatus.IN_PROGRESS
