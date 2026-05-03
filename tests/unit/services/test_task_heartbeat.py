"""TaskService.heartbeat updates last_heartbeat_at on the live task row.

Foundation for stale-claim recovery (Tasks 2b reaper, 2c verb-wiring): the
column existed in the DB since migration 006 but no service writer touched
it. This test seeds a real task in the test Postgres DB, calls heartbeat(),
and asserts the column was actually updated.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    Complexity,
    TaskNature,
    TaskStatus,
    TaskType,
    Team,
)
from roboco.services.task import TaskService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_claimed_task(session: AsyncSession) -> UUID:
    """Seed a project + dev agent + claimed task; return the task id.

    Mirrors the minimum subset of `smoke_test_batch` needed to satisfy
    the FK chain (created_by → agent, project_id → project, assigned_to →
    agent). Status is CLAIMED because heartbeat is called from agents
    holding an active claim.
    """
    system_agent = AgentTable(
        id=uuid4(),
        name="System",
        slug=f"system-{uuid4().hex[:8]}",
        role=AgentRole.SYSTEM,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="system",
        capabilities=[],
        permissions={},
        metrics={},
    )
    session.add(system_agent)
    await session.flush()

    project = ProjectTable(
        id=uuid4(),
        name="Heartbeat Test Project",
        slug=f"heartbeat-{uuid4().hex[:8]}",
        git_url="https://github.com/example/heartbeat.git",
        default_branch="main",
        protected_branches=["main"],
        assigned_cell=Team.BACKEND,
        created_by=system_agent.id,
        is_active=True,
    )
    session.add(project)
    await session.flush()

    dev_agent = AgentTable(
        id=uuid4(),
        name="Backend Dev",
        slug=f"be-dev-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=["python"],
        permissions={},
        metrics={},
    )
    session.add(dev_agent)
    await session.flush()

    task = TaskTable(
        id=uuid4(),
        title="Heartbeat target task",
        description="Synthetic task for heartbeat writer test.",
        acceptance_criteria=["heartbeat column gets written"],
        status=TaskStatus.CLAIMED,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=project.id,
        branch_name="feature/backend/HEARTBT1",
        created_by=system_agent.id,
        assigned_to=dev_agent.id,
        team=Team.BACKEND,
        dependency_ids=[],
        blocker_ids=[],
        sequence=0,
        plan={"steps": ["heartbeat"]},
        estimated_complexity=Complexity.LOW,
        execution_log={},
        checkpoints=[],
        progress_updates=[],
        commits=[],
        documents=[],
        outputs=[],
        last_heartbeat_at=None,
    )
    session.add(task)
    await session.commit()

    # `TaskTable.id` is annotated `Mapped[UUID]` (SA dialect type, not stdlib).
    # Convert through `str()` so the returned value is a real `uuid.UUID`.
    return UUID(str(task.id))


@pytest.mark.asyncio
async def test_heartbeat_updates_last_heartbeat_at(db_session: AsyncSession) -> None:
    """Calling heartbeat() must set last_heartbeat_at to a fresh UTC datetime."""
    task_id = await _seed_claimed_task(db_session)
    before = datetime.now(UTC) - timedelta(seconds=2)

    svc = TaskService(db_session)
    await svc.heartbeat(task_id)
    await db_session.commit()

    row = await svc.get(task_id)
    assert row is not None
    assert row.last_heartbeat_at is not None
    assert row.last_heartbeat_at > before


@pytest.mark.asyncio
async def test_heartbeat_is_noop_for_missing_task(db_session: AsyncSession) -> None:
    """heartbeat() on a non-existent task id must silently no-op (no raise)."""
    svc = TaskService(db_session)
    # No task seeded with this id; UPDATE should affect zero rows and return.
    await svc.heartbeat(uuid4())
    await db_session.commit()
