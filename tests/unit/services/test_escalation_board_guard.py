"""#14: a descendant code task is never assigned to a board/advisory role.

The main_pm -> product_owner escalation rung used to hand an in_progress child
code task to the Product Owner and mark it BLOCKED. The board has no verb to
claim/build/complete code work, so the dev's finished work deadlocked. The
shared write primitive ``TaskService.apply_escalation`` now diverts such an
escalation: the task is released to PENDING for a role-matched cell claim
instead of being stranded on a board role.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.models.base import AgentRole, TaskStatus, TaskType
from roboco.services.task import TaskService, _is_descendant_code_task


def _bind(svc: TaskService, name: str, value: object) -> None:
    object.__setattr__(svc, name, value)


def _service() -> TaskService:
    session = MagicMock()
    session.flush = AsyncMock()
    return TaskService(session)


# ---------------------------------------------------------------------------
# _is_descendant_code_task (pure)
# ---------------------------------------------------------------------------


def test_descendant_code_task_is_flagged() -> None:
    task = MagicMock(parent_task_id=uuid4(), task_type=TaskType.CODE)
    assert _is_descendant_code_task(task) is True


def test_root_code_task_is_not_descendant() -> None:
    # A root task can legitimately escalate up the chain (the CEO reviews it).
    task = MagicMock(parent_task_id=None, task_type=TaskType.CODE)
    assert _is_descendant_code_task(task) is False


def test_descendant_doc_task_is_not_code() -> None:
    task = MagicMock(parent_task_id=uuid4(), task_type=TaskType.DOCUMENTATION)
    assert _is_descendant_code_task(task) is False


def test_descendant_planning_task_is_not_code() -> None:
    task = MagicMock(parent_task_id=uuid4(), task_type=TaskType.PLANNING)
    assert _is_descendant_code_task(task) is False


def test_code_task_type_as_raw_string_is_flagged() -> None:
    # Detached/partially-hydrated rows may surface task_type as a raw string.
    task = MagicMock(parent_task_id=uuid4(), task_type="code")
    assert _is_descendant_code_task(task) is True


# ---------------------------------------------------------------------------
# apply_escalation board-role divert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_escalation_diverts_descendant_code_to_board() -> None:
    svc = _service()
    target_id = uuid4()
    task = MagicMock(
        id=uuid4(),
        parent_task_id=uuid4(),
        task_type=TaskType.CODE,
        assigned_to=uuid4(),
        blocker_raised_by=None,
        status=TaskStatus.IN_PROGRESS,
    )
    _bind(svc, "_is_board_advisory_agent", AsyncMock(return_value=True))
    release_mock = AsyncMock()
    _bind(svc, "_release_code_task_to_pool", release_mock)

    await svc.apply_escalation(
        task=task,
        target_agent_id=target_id,
        escalator_slug="main-pm",
        target_slug="product-owner",
        reason="please review",
    )

    # Diverted to the pool release — NOT blocked, NOT reassigned to the board.
    release_mock.assert_awaited_once()
    assert task.status == TaskStatus.IN_PROGRESS  # untouched by the guard branch
    assert task.assigned_to != target_id


@pytest.mark.asyncio
async def test_apply_escalation_proceeds_for_non_board_target() -> None:
    svc = _service()
    target_id = uuid4()
    task = MagicMock(
        id=uuid4(),
        parent_task_id=uuid4(),
        task_type=TaskType.CODE,
        assigned_to=uuid4(),
        blocker_raised_by=None,
        dev_notes=None,
        status=TaskStatus.IN_PROGRESS,
    )
    _bind(svc, "_is_board_advisory_agent", AsyncMock(return_value=False))
    release_mock = AsyncMock()
    _bind(svc, "_release_code_task_to_pool", release_mock)

    await svc.apply_escalation(
        task=task,
        target_agent_id=target_id,
        escalator_slug="be-pm",
        target_slug="main-pm",
        reason="cell blocked",
    )

    # Normal escalation: blocked + reassigned to the (non-board) target.
    release_mock.assert_not_called()
    assert task.status == TaskStatus.BLOCKED
    assert task.assigned_to == target_id


@pytest.mark.asyncio
async def test_apply_escalation_blocks_root_code_task_to_board_target() -> None:
    # A ROOT code task is not a descendant — the guard does not fire even when
    # the target is a board role (the CEO/board reviews roots legitimately).
    svc = _service()
    target_id = uuid4()
    task = MagicMock(
        id=uuid4(),
        parent_task_id=None,
        task_type=TaskType.CODE,
        assigned_to=uuid4(),
        blocker_raised_by=None,
        dev_notes=None,
        status=TaskStatus.IN_PROGRESS,
    )
    board_check = AsyncMock(return_value=True)
    _bind(svc, "_is_board_advisory_agent", board_check)
    release_mock = AsyncMock()
    _bind(svc, "_release_code_task_to_pool", release_mock)

    await svc.apply_escalation(
        task=task,
        target_agent_id=target_id,
        escalator_slug="main-pm",
        target_slug="product-owner",
        reason="root review",
    )

    # Guard short-circuits on _is_descendant_code_task BEFORE the board check,
    # so a root task escalates normally.
    board_check.assert_not_called()
    release_mock.assert_not_called()
    assert task.status == TaskStatus.BLOCKED
    assert task.assigned_to == target_id


@pytest.mark.asyncio
async def test_release_code_task_to_pool_sets_pending_and_clears_assignee() -> None:
    svc = _service()
    task = MagicMock(
        id=uuid4(),
        assigned_to=uuid4(),
        claimed_by=uuid4(),
        active_claimant_id=uuid4(),
        dev_notes="prior",
        status=TaskStatus.IN_PROGRESS,
    )
    await svc._release_code_task_to_pool(
        task=task,
        escalator_slug="main-pm",
        blocked_target_slug="product-owner",
        reason="cannot own code",
    )
    assert task.status == TaskStatus.PENDING
    assert task.assigned_to is None
    assert task.claimed_by is None
    assert task.active_claimant_id is None
    assert "ESCALATION REDIRECTED" in task.dev_notes


@pytest.mark.asyncio
async def test_is_board_advisory_agent_classifies_roles() -> None:
    for role, expected in [
        (AgentRole.PRODUCT_OWNER, True),
        (AgentRole.HEAD_MARKETING, True),
        (AgentRole.AUDITOR, True),
        (AgentRole.MAIN_PM, False),
        (AgentRole.CELL_PM, False),
        (AgentRole.DEVELOPER, False),
    ]:
        session = MagicMock()
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=role)
        session.execute = AsyncMock(return_value=result)
        svc = TaskService(session)
        assert await svc._is_board_advisory_agent(uuid4()) is expected
