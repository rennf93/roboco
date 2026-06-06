"""The cell_pm `reassign` verb — hand a claimed/in_progress task to another
developer in the caller's OWN cell, preserving the branch.

Covers the intra-cell guard (`Choreographer._validate_reassign`, using real
agents_config data) and the reaper-safe service write
(`TaskService.reassign_active_claim`).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from roboco.models.base import TaskStatus
from roboco.seeds.initial_data import AGENT_UUIDS
from roboco.services.gateway.choreographer._impl import Choreographer
from roboco.services.task import TaskService

_BE_PM = UUID(AGENT_UUIDS["be-pm"])


def _task(team: str = "backend", status: str = "in_progress") -> MagicMock:
    return MagicMock(team=MagicMock(value=team), status=MagicMock(value=status))


# ---------------------------------------------------------------------------
# _validate_reassign — intra-cell guard
# ---------------------------------------------------------------------------


def test_allows_same_cell_developer() -> None:
    assert Choreographer._validate_reassign(_task(), _BE_PM, "be-dev-2") is None


def test_rejects_cross_cell_developer() -> None:
    # fe-dev-1 is a frontend dev; a backend PM may not reassign to it.
    env = Choreographer._validate_reassign(_task("backend"), _BE_PM, "fe-dev-1")
    assert env is not None
    assert env.error == "not_authorized"


def test_rejects_non_developer_target() -> None:
    # be-qa is in the cell but is not a developer.
    env = Choreographer._validate_reassign(_task("backend"), _BE_PM, "be-qa")
    assert env is not None
    assert env.error == "not_authorized"


def test_rejects_task_outside_callers_cell() -> None:
    env = Choreographer._validate_reassign(_task("frontend"), _BE_PM, "be-dev-2")
    assert env is not None
    assert env.error == "not_authorized"


def test_rejects_non_active_status() -> None:
    env = Choreographer._validate_reassign(
        _task("backend", "awaiting_qa"), _BE_PM, "be-dev-2"
    )
    assert env is not None
    assert env.error == "invalid_state"


def test_rejects_unknown_slug() -> None:
    env = Choreographer._validate_reassign(_task("backend"), _BE_PM, "be-dev-99")
    assert env is not None
    assert env.error == "invalid_state"


def test_allows_claimed_status() -> None:
    assert (
        Choreographer._validate_reassign(
            _task("backend", "claimed"), _BE_PM, "be-dev-1"
        )
        is None
    )


# ---------------------------------------------------------------------------
# reassign_active_claim — reaper-safe service write
# ---------------------------------------------------------------------------


def _build_task(**over: object) -> MagicMock:
    base: dict[str, object] = {
        "id": uuid4(),
        "status": TaskStatus.IN_PROGRESS,
        "assigned_to": None,
        "claimed_by": None,
        "claimed_at": None,
        "last_heartbeat_at": None,
        "active_claimant_id": None,
    }
    base.update(over)
    return MagicMock(**base)


def _service() -> TaskService:
    session = MagicMock()
    session.flush = AsyncMock()
    return TaskService(session)


@pytest.mark.asyncio
async def test_reassign_active_claim_seeds_a_fresh_claim() -> None:
    task = _build_task(status=TaskStatus.IN_PROGRESS)
    svc = _service()
    object.__setattr__(svc, "get", AsyncMock(return_value=task))
    new_id = uuid4()
    result = await svc.reassign_active_claim(task.id, new_id)
    assert result is task
    assert task.assigned_to == new_id
    assert task.claimed_by == new_id
    assert task.active_claimant_id == new_id
    # Fresh claim window so the reaper doesn't treat the new dev as stale.
    assert task.claimed_at is not None
    assert task.last_heartbeat_at is not None


@pytest.mark.asyncio
async def test_reassign_active_claim_refuses_non_active_status() -> None:
    task = _build_task(status=TaskStatus.AWAITING_QA)
    svc = _service()
    object.__setattr__(svc, "get", AsyncMock(return_value=task))
    assert await svc.reassign_active_claim(task.id, uuid4()) is None
