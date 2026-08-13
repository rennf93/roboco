"""A status/ownership change that moves WHO should act must move the claim.

Live evidence (2026-08): a task in awaiting_ceo_approval had
assigned_to=ceo but active_claimant_id=main-pm, and two gate tasks the CEO
manually reassigned to pr-reviewer-1 still read active_claimant_id=main-pm
afterward. Both paths route through ``TaskService.reassign`` — used both by
the gateway choreographer's own lifecycle hand-offs (dev -> qa -> doc -> pm,
escalate_to_ceo -> None) and by the CEO's manual reassignment (via
``SecretaryService._reassign_task``'s fallback for any status outside
CLAIMED/IN_PROGRESS, which ``reassign_active_claim`` refuses). ``reassign``
set ``assigned_to`` / ``claimed_by`` but never touched
``active_claimant_id``, so the prior claimant kept reading as "actively
working" the task (and, via ``_active_claim_violation``, could keep writing
notes/commits) after losing ownership.

``reassign_active_claim`` (the CLAIMED/IN_PROGRESS-only sibling) already
gets this right — see ``test_task_assignment_invariants.py`` — this file
covers only the ``reassign`` gap.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.models.base import TaskStatus, TaskType, Team
from roboco.services.task import TaskService


def _bind(svc: TaskService, name: str, value: object) -> None:
    object.__setattr__(svc, name, value)


def _service() -> TaskService:
    session = MagicMock()
    session.flush = AsyncMock()
    session.get = AsyncMock(return_value=None)
    return TaskService(session)


def _task(*, assigned_to: object, active_claimant_id: object) -> MagicMock:
    # team=MAIN_PM + parent_task_id=None sidesteps the cell-PM-owned-child
    # redirect entirely (see test_task_assignment_invariants.py) so these
    # tests exercise only the active_claimant_id clearing.
    return MagicMock(
        id=uuid4(),
        parent_task_id=None,
        team=Team.MAIN_PM,
        task_type=TaskType.PLANNING,
        assigned_to=assigned_to,
        claimed_by=assigned_to,
        active_claimant_id=active_claimant_id,
        dev_notes="",
        status=TaskStatus.AWAITING_CEO_APPROVAL,
    )


@pytest.mark.asyncio
async def test_reassign_clears_stale_active_claimant_id() -> None:
    """The CEO manually reassigns a gate task to a new reviewer: the prior
    claimant's grip must not survive the hand-off."""
    svc = _service()
    old_claimant = uuid4()
    new_owner = uuid4()
    task = _task(assigned_to=old_claimant, active_claimant_id=old_claimant)
    _bind(svc, "get", AsyncMock(return_value=task))
    _bind(svc, "_is_board_advisory_agent", AsyncMock(return_value=False))

    result = await svc.reassign(task.id, new_owner)

    assert result is task
    assert task.assigned_to == new_owner
    assert task.active_claimant_id is None


@pytest.mark.asyncio
async def test_reassign_to_none_clears_stale_active_claimant_id() -> None:
    """escalate_to_ceo's own ``reassign(task_id, None)`` call must also
    clear the stale claim — the exact live-evidence shape: assigned_to
    becomes unassigned (the CEO acts via the UI) but active_claimant_id was
    left pointing at the PM that escalated."""
    svc = _service()
    pm_id = uuid4()
    task = _task(assigned_to=pm_id, active_claimant_id=pm_id)
    _bind(svc, "get", AsyncMock(return_value=task))
    _bind(svc, "_is_board_advisory_agent", AsyncMock(return_value=False))

    result = await svc.reassign(task.id, None)

    assert result is task
    assert task.assigned_to is None
    assert task.active_claimant_id is None


@pytest.mark.asyncio
async def test_reassign_noop_when_active_claimant_already_matches() -> None:
    """No needless write when the new owner is already the active claimant
    — a legitimately-set claim must not be wiped by an idempotent re-hand."""
    svc = _service()
    owner = uuid4()
    task = _task(assigned_to=owner, active_claimant_id=owner)
    _bind(svc, "get", AsyncMock(return_value=task))
    _bind(svc, "_is_board_advisory_agent", AsyncMock(return_value=False))

    result = await svc.reassign(task.id, owner)

    assert result is task
    assert task.active_claimant_id == owner


@pytest.mark.asyncio
async def test_reassign_leaves_already_unclaimed_task_alone() -> None:
    """No accidental write on the common case: a task with no live claim
    stays that way through a reassign."""
    svc = _service()
    new_owner = uuid4()
    task = _task(assigned_to=None, active_claimant_id=None)
    _bind(svc, "get", AsyncMock(return_value=task))
    _bind(svc, "_is_board_advisory_agent", AsyncMock(return_value=False))

    result = await svc.reassign(task.id, new_owner)

    assert result is task
    assert task.assigned_to == new_owner
    assert task.active_claimant_id is None
