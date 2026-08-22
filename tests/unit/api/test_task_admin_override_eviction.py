"""`_apply_forced_status_override` (PATCH /api/tasks/{id}) delegates the
status change to `TaskService.admin_set_status` and returns its result
verbatim.

The stranded-agent eviction used to live at this route layer (a
`_evict_stranded_agent` + `_schedule_stranded_eviction` pair, scheduled only
when the request carried `force=true`). It has moved to
`TaskService.admin_set_status` itself (`roboco/services/task.py`), the
chokepoint every caller of a status override routes through, not just this
route, so it now fires whenever a live claim actually gets cleared,
independent of `force`. See
`tests/unit/services/test_task_admin_override_eviction.py` for the eviction
mechanics and the unconditional-on-force regression tests, and
`tests/integration/test_tasks_routes.py` for the force/hatch/resurrection
gating this route still owns end to end against a real DB.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.api.utils.tasks import _apply_forced_status_override, _StatusOverride
from roboco.models.base import TaskStatus


@pytest.mark.asyncio
async def test_apply_forced_status_override_delegates_to_admin_set_status() -> None:
    """The route helper is a thin pass-through past its own force/hatch/
    resurrect gates: it hands the actual status change to
    `admin_set_status` and returns exactly what it returns, with no
    eviction logic of its own left at this layer."""
    task_id = uuid4()
    pre_task = MagicMock()
    pre_task.status = TaskStatus.IN_PROGRESS

    post_task = MagicMock()
    fake_service = MagicMock()
    fake_service.admin_set_status = AsyncMock(return_value=post_task)

    agent = MagicMock(agent_id=uuid4())
    req = _StatusOverride(
        service=fake_service,
        task_id=task_id,
        task=pre_task,
        new_status=TaskStatus.NEEDS_REVISION,
        force=False,
        has_higher_perms=True,
        agent=agent,
    )

    result = await _apply_forced_status_override(req)

    assert result is post_task
    fake_service.admin_set_status.assert_awaited_once_with(
        task_id,
        TaskStatus.NEEDS_REVISION,
        actor_id=agent.agent_id,
        actor_role=getattr(agent, "role", None),
        force=False,
    )


@pytest.mark.asyncio
async def test_apply_forced_status_override_no_op_on_same_status() -> None:
    """No-op fast path: a PATCH that names the task's own current status
    never reaches `admin_set_status` at all."""
    task_id = uuid4()
    pre_task = MagicMock()
    pre_task.status = TaskStatus.IN_PROGRESS

    fake_service = MagicMock()
    fake_service.admin_set_status = AsyncMock()

    req = _StatusOverride(
        service=fake_service,
        task_id=task_id,
        task=pre_task,
        new_status=TaskStatus.IN_PROGRESS,
        force=False,
        has_higher_perms=True,
        agent=MagicMock(agent_id=uuid4()),
    )

    result = await _apply_forced_status_override(req)

    assert result is pre_task
    fake_service.admin_set_status.assert_not_awaited()
