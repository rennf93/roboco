"""Never-started claims a work author holds beyond its one task go back to its queue.

A developer or devops agent holds one unfinished task at a time. Before that rule
covered every path that hands out work (c8879be9, 2026-10-28) the dispatcher
stacked on-behalf claims on developers that already held work, and nothing ever
reconciled the rows it left: the 2026-10-28 run ended with be-dev-1 holding
paused f5d66db6 and 11a83407 next to claimed 106f46e3 and ec0b4a38. On
2026-11-01 be-dev-1 was booted onto claimed 106f46e3 again and every verb it
tried was refused ("You have a in_progress task", "task is in 'claimed'").

``TaskService.release_never_started_extra_claims`` releases those extra claims
(claimed -> pending, still assigned to the author, branch untouched), records
why on each task, and leaves started work alone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable, AuditLogTable, ProjectTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType
from roboco.models.task import TaskCreateRequest
from roboco.services.task import TaskService
from sqlalchemy import select

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


async def _held(cell: dict[str, Any], status: TaskStatus) -> TaskTable:
    """A task ``cell['dev']`` holds in ``status``, on its own branch."""
    svc: TaskService = cell["svc"]
    task = await svc.create(
        TaskCreateRequest(
            title="t",
            description="d",
            acceptance_criteria=["ac"],
            team=Team.BACKEND,
            created_by=cell["pm"].id,
            project_id=cell["project_id"],
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.MEDIUM,
        )
    )
    task.status = status
    task.assigned_to = cell["dev"].id
    task.claimed_by = cell["dev"].id
    task.branch_name = f"feature/backend/{str(task.id)[:8]}"
    await cell["db"].flush()
    return task


async def _release_audit_targets(db: AsyncSession) -> list[Any]:
    rows = await db.execute(
        select(AuditLogTable.target_id).where(
            AuditLogTable.event_type == "task.extra_claim_released"
        )
    )
    return list(rows.scalars().all())


@pytest.mark.asyncio
async def test_extra_claims_beside_paused_work_return_to_the_authors_queue(
    cell: dict[str, Any],
) -> None:
    """The shape be-dev-1 was left in on 2026-10-28: two paused tasks and two
    never-started claims."""
    svc, db, dev = cell["svc"], cell["db"], cell["dev"]
    paused = [await _held(cell, TaskStatus.PAUSED) for _ in range(2)]
    claims = [await _held(cell, TaskStatus.CLAIMED) for _ in range(2)]
    branches = {task.id: task.branch_name for task in claims}

    released = await svc.release_never_started_extra_claims(dev.id)

    assert {task.id for task in released} == set(branches)
    for task in claims:
        await db.refresh(task)
        assert task.status == TaskStatus.PENDING
        assert task.assigned_to == dev.id
        assert task.claimed_by == dev.id
        assert task.branch_name == branches[task.id]
        assert "held-work reconciliation" in task.progress_updates[-1]["message"]
    for task in paused:
        await db.refresh(task)
        assert task.status == TaskStatus.PAUSED
    assert sorted(map(str, await _release_audit_targets(db))) == sorted(
        map(str, branches)
    )


@pytest.mark.asyncio
async def test_started_work_is_kept_and_only_the_claim_is_released(
    cell: dict[str, Any],
) -> None:
    svc, db, dev = cell["svc"], cell["db"], cell["dev"]
    working = await _held(cell, TaskStatus.IN_PROGRESS)
    claim = await _held(cell, TaskStatus.CLAIMED)

    released = await svc.release_never_started_extra_claims(dev.id)

    assert [task.id for task in released] == [claim.id]
    held = await svc.list_held_for_agent(dev.id)
    assert [task.id for task in held] == [working.id]
    progress = claim.progress_updates[-1]["message"]
    assert f"in_progress task {working.id}" in progress
    await db.refresh(working)
    assert working.status == TaskStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_a_claim_beside_work_escalated_away_is_released(
    cell: dict[str, Any],
) -> None:
    """The 2026-11-01 shape: f5d66db6 escalated to the PM (still the author's
    through pre_block_assignee) next to claimed 106f46e3."""
    svc, dev, pm = cell["svc"], cell["dev"], cell["pm"]
    escalated = await _held(cell, TaskStatus.IN_PROGRESS)
    await svc.apply_escalation(
        task=escalated,
        target_agent_id=pm.id,
        escalator_slug=dev.slug,
        target_slug=pm.slug,
        reason="quality gate keeps failing",
    )
    claim = await _held(cell, TaskStatus.CLAIMED)

    released = await svc.release_never_started_extra_claims(dev.id)

    assert [task.id for task in released] == [claim.id]
    assert [task.id for task in await svc.list_held_for_agent(dev.id)] == [escalated.id]


@pytest.mark.asyncio
async def test_an_author_holding_only_claims_keeps_one(cell: dict[str, Any]) -> None:
    svc, dev = cell["svc"], cell["dev"]
    claims = [await _held(cell, TaskStatus.CLAIMED) for _ in range(3)]

    released = await svc.release_never_started_extra_claims(dev.id)

    kept = await svc.list_held_for_agent(dev.id)
    assert len(kept) == 1
    assert {task.id for task in released} | {kept[0].id} == {t.id for t in claims}


@pytest.mark.asyncio
async def test_started_extras_are_never_touched(cell: dict[str, Any]) -> None:
    """A second started task is the author's work in progress; only a claim
    that never started is released."""
    svc, dev = cell["svc"], cell["dev"]
    working = [await _held(cell, TaskStatus.IN_PROGRESS) for _ in range(2)]
    paused = await _held(cell, TaskStatus.PAUSED)

    released = await svc.release_never_started_extra_claims(dev.id)

    assert released == []
    held = {task.id for task in await svc.list_held_for_agent(dev.id)}
    assert held == {working[0].id, working[1].id, paused.id}


@pytest.mark.asyncio
async def test_reconciliation_is_idempotent(cell: dict[str, Any]) -> None:
    svc, db, dev = cell["svc"], cell["db"], cell["dev"]
    await _held(cell, TaskStatus.PAUSED)
    await _held(cell, TaskStatus.CLAIMED)

    first = await svc.release_never_started_extra_claims(dev.id)
    second = await svc.release_never_started_extra_claims(dev.id)

    assert len(first) == 1
    assert second == []
    assert len(await _release_audit_targets(db)) == 1


@pytest.mark.asyncio
async def test_a_single_held_task_is_left_alone(cell: dict[str, Any]) -> None:
    svc, dev = cell["svc"], cell["dev"]
    claim = await _held(cell, TaskStatus.CLAIMED)

    assert await svc.release_never_started_extra_claims(dev.id) == []
    assert [task.id for task in await svc.list_held_for_agent(dev.id)] == [claim.id]


@pytest.mark.asyncio
async def test_a_released_claim_is_its_authors_next_task(cell: dict[str, Any]) -> None:
    """No task is dropped: once the kept task is done the author claims the
    released one again, on the branch it already had."""
    svc, dev = cell["svc"], cell["dev"]
    kept = await _held(cell, TaskStatus.IN_PROGRESS)
    claim = await _held(cell, TaskStatus.CLAIMED)
    branch = claim.branch_name
    await svc.release_never_started_extra_claims(dev.id)
    assert await svc.claim(claim.id, dev.id, provision=False) is None

    kept.status = TaskStatus.AWAITING_QA
    await cell["db"].flush()
    reclaimed = await svc.claim(claim.id, dev.id, provision=False)

    assert reclaimed is not None
    assert reclaimed.status == TaskStatus.CLAIMED
    assert reclaimed.branch_name == branch
