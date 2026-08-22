"""``_dependency_claim_guard`` must not crash on a NULL ``dependency_ids``.

A restored/reconstructed task row can carry NULL instead of `[]` on a
nullable-in-practice ARRAY/JSON column (live incident: `TaskService.add_progress`
crashed with `TypeError: Value after * must be an iterable, not NoneType` on
exactly this shape). This guard is the claim-time chokepoint every
`i_will_work_on` / `i_will_plan` call routes through, so a crash here blocks
every claim on the affected task.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_deps(task_svc: AsyncMock) -> ChoreographerDeps:
    return ChoreographerDeps(
        task=task_svc,
        work_session=AsyncMock(),
        git=AsyncMock(),
        a2a=AsyncMock(),
        journal=AsyncMock(),
        audit=AsyncMock(),
        evidence_repo=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_dependency_claim_guard_treats_null_dependency_ids_as_none() -> None:
    task_svc = AsyncMock()
    task_svc.unmet_dependency_ids = AsyncMock(return_value=[])
    c = Choreographer(_make_deps(task_svc))
    cc: Any = c
    task = MagicMock(id=uuid4(), dependency_ids=None)

    guard = await cc._dependency_claim_guard(task)  # must not raise

    assert guard is None
    task_svc.unmet_dependency_ids.assert_not_awaited()
