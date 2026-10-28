"""A work author (developer / devops) holds one unfinished task at a time.

The gateway's ``already_active`` / ``paused`` guards only ran on the agent's
own verbs, and their lookup missed claimed tasks and tasks escalated away from
the agent. Live 2026-10-28: be-dev-1 escalated 11a83407, claimed f5d66db6 a
minute later and was handed 11a83407 back by the PM's restore (two in_progress
tasks); the dispatcher's on-behalf claims stacked tasks on developers holding
paused work, which their own ``i_will_work_on`` then refused. These tests pin
``TaskService.held_task_blocking_claim`` and the claim chokepoint against a
real database.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable, ProjectTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType
from roboco.models.task import TaskCreateRequest
from roboco.services.task import TaskService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from roboco.db.tables import TaskTable
    from sqlalchemy.ext.asyncio import AsyncSession


def _agent(role: AgentRole, slug: str) -> AgentTable:
    return AgentTable(
        id=uuid4(),
        name=slug,
        slug=f"{slug}-{uuid4().hex[:8]}",
        role=role,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt=slug,
        capabilities=[],
        permissions={},
        metrics={},
    )


@pytest_asyncio.fixture
async def cell(db_session: AsyncSession) -> AsyncIterator[dict[str, Any]]:
    dev = _agent(AgentRole.DEVELOPER, "be-dev")
    pm = _agent(AgentRole.CELL_PM, "be-pm")
    db_session.add_all([dev, pm])
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="Cell",
        slug=f"cell-{uuid4().hex[:8]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=dev.id,
    )
    db_session.add(project)
    await db_session.flush()
    yield {
        "svc": TaskService(db_session),
        "db": db_session,
        "dev": dev,
        "pm": pm,
        "project_id": project.id,
    }


async def _task(
    cell: dict[str, Any],
    *,
    status: TaskStatus = TaskStatus.PENDING,
    owner: AgentTable | None = None,
    task_type: TaskType = TaskType.CODE,
) -> TaskTable:
    svc: TaskService = cell["svc"]
    task = await svc.create(
        TaskCreateRequest(
            title="t",
            description="d",
            acceptance_criteria=["ac"],
            team=Team.BACKEND,
            created_by=cell["pm"].id,
            project_id=cell["project_id"],
            task_type=task_type,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.MEDIUM,
        )
    )
    task.status = status
    if owner is not None:
        task.assigned_to = owner.id
        task.claimed_by = owner.id
    await cell["db"].flush()
    return task


@pytest.mark.asyncio
async def test_escalated_away_task_still_blocks_its_author(
    cell: dict[str, Any],
) -> None:
    svc, dev, pm = cell["svc"], cell["dev"], cell["pm"]
    escalated = await _task(cell, status=TaskStatus.IN_PROGRESS, owner=dev)
    await svc.apply_escalation(
        task=escalated,
        target_agent_id=pm.id,
        escalator_slug=dev.slug,
        target_slug=pm.slug,
        reason="needs a PM decision",
    )
    assert escalated.assigned_to == pm.id  # ownership moved to the PM...
    new = await _task(cell)

    held = await svc.held_task_blocking_claim(dev.id, new.id)

    # ...but unblock_with_restore hands it straight back to the dev.
    assert held is not None
    assert held.id == escalated.id
    assert await svc.claim(new.id, dev.id, provision=False) is None


@pytest.mark.asyncio
async def test_claimed_unstarted_task_blocks_a_second_claim(
    cell: dict[str, Any],
) -> None:
    svc, dev = cell["svc"], cell["dev"]
    claimed = await _task(cell, status=TaskStatus.CLAIMED, owner=dev)
    new = await _task(cell)

    held = await svc.held_task_blocking_claim(dev.id, new.id)

    assert held is not None
    assert held.id == claimed.id
    assert await svc.claim(new.id, dev.id, provision=False) is None


@pytest.mark.asyncio
async def test_paused_task_blocks_new_work_until_resumed(
    cell: dict[str, Any],
) -> None:
    svc, dev = cell["svc"], cell["dev"]
    paused = await _task(cell, status=TaskStatus.PAUSED, owner=dev)
    new = await _task(cell)

    held = await svc.held_task_blocking_claim(dev.id, new.id)

    assert held is not None
    assert held.id == paused.id
    assert await svc.claim(new.id, dev.id, provision=False) is None


@pytest.mark.asyncio
async def test_active_work_is_reported_before_paused_work(
    cell: dict[str, Any],
) -> None:
    svc, dev = cell["svc"], cell["dev"]
    await _task(cell, status=TaskStatus.PAUSED, owner=dev)
    active = await _task(cell, status=TaskStatus.IN_PROGRESS, owner=dev)
    new = await _task(cell)

    held = await svc.held_task_blocking_claim(dev.id, new.id)

    assert held is not None
    assert held.id == active.id


@pytest.mark.asyncio
async def test_the_target_task_itself_never_blocks(cell: dict[str, Any]) -> None:
    svc, dev = cell["svc"], cell["dev"]
    paused = await _task(cell, status=TaskStatus.PAUSED, owner=dev)

    assert await svc.held_task_blocking_claim(dev.id, paused.id) is None


@pytest.mark.asyncio
async def test_a_free_developer_still_claims(cell: dict[str, Any]) -> None:
    svc, dev = cell["svc"], cell["dev"]
    new = await _task(cell)

    claimed = await svc.claim(new.id, dev.id, provision=False)

    assert claimed is not None
    assert claimed.status == TaskStatus.CLAIMED
    assert claimed.assigned_to == dev.id


@pytest.mark.asyncio
async def test_coordinators_keep_holding_many_roots(cell: dict[str, Any]) -> None:
    """A PM plans and delegates many roots in parallel by design."""
    svc, pm = cell["svc"], cell["pm"]
    await _task(cell, status=TaskStatus.PAUSED, owner=pm, task_type=TaskType.PLANNING)
    new = await _task(cell, task_type=TaskType.PLANNING)

    claimed = await svc.claim(new.id, pm.id, provision=False)

    assert claimed is not None
    assert claimed.assigned_to == pm.id
