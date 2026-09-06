"""PATCH null-clear semantics — unassigning a task implies releasing its claim.

Live wedge (2026-07-01): a CEO PATCH set assigned_to=null on a wedged task but
claimed_by/active_claimant_id survived, so the task kept routing to the stale
claimant while the next agent's content writes bounced not_authorized.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.api.utils.tasks import _apply_null_clears, _recheck_topology_after_detach


def _task(**overrides: object) -> SimpleNamespace:
    owner = uuid4()
    base: dict[str, object] = {
        "assigned_to": owner,
        "claimed_by": owner,
        "claimed_at": datetime.now(UTC),
        "active_claimant_id": owner,
        "parent_task_id": uuid4(),
        "project_id": uuid4(),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_unassign_clears_claim_fields() -> None:
    """assigned_to=null releases the claim triplet with it."""
    task = _task()
    _apply_null_clears(task, {"assigned_to": None})
    assert task.assigned_to is None
    assert task.claimed_by is None
    assert task.claimed_at is None
    assert task.active_claimant_id is None


def test_other_null_clears_leave_claim_untouched() -> None:
    """Clearing parent_task_id/project_id is structural — not a claim release."""
    owner = uuid4()
    task = _task(assigned_to=owner, claimed_by=owner, active_claimant_id=owner)
    _apply_null_clears(task, {"parent_task_id": None})
    assert task.parent_task_id is None
    assert task.assigned_to == owner
    assert task.claimed_by == owner
    assert task.active_claimant_id == owner


@pytest.mark.asyncio
async def test_detach_reparent_reruns_topology_recheck() -> None:
    """PATCH {"parent_task_id": null} bypasses TaskService.update()'s own
    field-update loop (the null-clear is popped out and applied via direct
    setattr instead), so _maybe_recheck_topology never fires for a detach.
    The route must re-run the same recheck itself for this direction."""
    task = _task()
    service = MagicMock()
    service.recheck_topology_after_reparent = AsyncMock()

    await _recheck_topology_after_detach(task, service, {"parent_task_id": None})

    service.recheck_topology_after_reparent.assert_awaited_once_with(task)


@pytest.mark.asyncio
async def test_non_reparent_null_clears_skip_topology_recheck() -> None:
    """Clearing an unrelated nullable field (assigned_to, project_id,
    budget_usd) never touches parent topology — no recheck call."""
    task = _task()
    service = MagicMock()
    service.recheck_topology_after_reparent = AsyncMock()

    await _recheck_topology_after_detach(task, service, {"assigned_to": None})

    service.recheck_topology_after_reparent.assert_not_awaited()
