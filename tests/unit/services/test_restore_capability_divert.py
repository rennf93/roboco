"""unblock restore diverts to the pool when the snapshotted owner cannot act.

The 4be7d6d3 wedge: unblock restored awaiting_pm_review to an owner whose role
has no ``complete`` verb. The restore now consults the same lifecycle capability
predicate the assignment paths use and releases the task to the pool instead.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.foundation.identity import AGENTS
from roboco.models.base import TaskStatus, Team
from roboco.services.task import TaskService


def _svc() -> TaskService:
    session = MagicMock()
    session.flush = AsyncMock()
    session.get = AsyncMock(return_value=None)
    return TaskService(session)


def _blocked_task(pre_state: str, owner_slug: str) -> MagicMock:
    return MagicMock(
        id=uuid4(),
        status=TaskStatus.BLOCKED,
        pre_block_state=pre_state,
        pre_block_assignee=AGENTS[owner_slug].uuid,
        assigned_to=AGENTS[owner_slug].uuid,
        claimed_by=AGENTS[owner_slug].uuid,
        branch_name="b",
        dev_notes="",
        team=Team.BACKEND,
    )


@pytest.mark.asyncio
async def test_incapable_owner_restore_diverts_to_pool() -> None:
    svc = _svc()
    task = _blocked_task("awaiting_pm_review", "be-dev-2")
    object.__setattr__(svc, "get", AsyncMock(return_value=task))

    await svc.unblock_with_restore(uuid4(), task.id, restore=True)

    assert task.status == TaskStatus.PENDING
    assert task.assigned_to is None
    assert task.pre_block_state is None
    assert "[RESTORE REDIRECTED]" in task.dev_notes


@pytest.mark.asyncio
async def test_capable_owner_restore_is_unchanged() -> None:
    svc = _svc()
    task = _blocked_task("awaiting_pm_review", "main-pm")
    object.__setattr__(svc, "get", AsyncMock(return_value=task))

    await svc.unblock_with_restore(uuid4(), task.id, restore=True)

    assert task.status == TaskStatus.AWAITING_PM_REVIEW
    assert task.assigned_to == AGENTS["main-pm"].uuid
