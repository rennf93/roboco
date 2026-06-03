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
        checkpoints=[],
        progress_updates=[],
        commits=[],
        documents=[],
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


async def _seed_pending_task_for_claim(
    session: AsyncSession,
) -> tuple[UUID, UUID]:
    """Seed a pending task pre-assigned to a dev. Returns (task_id, agent_id).

    Pre-assigned-and-pending mirrors the production case where the CEO/PM
    creates a task already assigned to the executor; claim() is then the
    transition that moves it to CLAIMED + seeds the heartbeat.
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
        name="Claim Test Project",
        slug=f"claim-{uuid4().hex[:8]}",
        git_url="https://github.com/example/claim.git",
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
        title="Claim target task",
        description="Pending pre-assigned task; claim should seed heartbeat.",
        acceptance_criteria=["heartbeat seeded on claim"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=project.id,
        # Pre-set branch_name so _finalize_claim's _ensure_branch_for_task
        # short-circuits — the test's assertion is on heartbeat seeding,
        # not branch creation, and unit Postgres has no git auth.
        branch_name="feature/backend/CLAIMED1",
        created_by=system_agent.id,
        assigned_to=dev_agent.id,
        team=Team.BACKEND,
        dependency_ids=[],
        blocker_ids=[],
        sequence=0,
        plan=None,
        estimated_complexity=Complexity.LOW,
        checkpoints=[],
        progress_updates=[],
        commits=[],
        documents=[],
        last_heartbeat_at=None,
    )
    session.add(task)
    await session.commit()

    return UUID(str(task.id)), UUID(str(dev_agent.id))


@pytest.mark.asyncio
async def test_claim_seeds_last_heartbeat_at(db_session: AsyncSession) -> None:
    """Regression: claim() must seed last_heartbeat_at so the reaper does
    not immediately reap the freshly-claimed task on the next dispatch tick.

    Smoke 2026-05-03 surfaced the reaper firing ~5x/sec against newly-
    claimed tasks because last_heartbeat_at remained NULL until the agent
    called a hot verb (heartbeat-on-_touch). The fix in c0eb90e seeds the
    heartbeat at claim time. This test pins the contract.
    """
    task_id, agent_id = await _seed_pending_task_for_claim(db_session)
    before = datetime.now(UTC) - timedelta(seconds=2)

    svc = TaskService(db_session)
    result = await svc.claim(task_id, agent_id)
    await db_session.commit()

    assert result is not None, "claim should succeed for pre-assigned same-agent path"

    # Re-fetch via fresh query to confirm the column actually persisted.
    row = await svc.get(task_id)
    assert row is not None
    assert row.status == TaskStatus.CLAIMED
    assert row.last_heartbeat_at is not None, (
        "claim must seed last_heartbeat_at — without it the reaper "
        "interprets NULL as stale and reaps the claim immediately"
    )
    assert row.last_heartbeat_at > before
