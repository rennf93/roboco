"""Migration 112: a blocked task's claim lock moves to the agent owning it.

Rows escalated before ``apply_escalation`` moved the lock still carry the
escalator's ``active_claimant_id``. Runs the migration's own ``upgrade()``
through alembic's ``Operations`` on the test session's connection; the
``db_session`` teardown rolls everything back.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from roboco.db.tables import AgentTable, ProjectTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType
from roboco.models.task import TaskCreateRequest
from roboco.services.task import TaskService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from types import ModuleType

    from roboco.db.tables import TaskTable
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import Session

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "112_escalated_claim_lock.py"
)


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_112", _MIGRATION)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _upgrade(db_session: AsyncSession) -> None:
    migration = _load_migration()

    def _apply(session: Session) -> None:
        context = MigrationContext.configure(session.connection())
        with Operations.context(context):
            migration.upgrade()

    await db_session.run_sync(_apply)


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
async def fleet(db_session: AsyncSession) -> AsyncIterator[dict[str, Any]]:
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
    fleet: dict[str, Any],
    *,
    status: TaskStatus,
    owner: AgentTable,
    claimed_by: AgentTable | None = None,
    lock: AgentTable | None,
) -> TaskTable:
    svc: TaskService = fleet["svc"]
    task = await svc.create(
        TaskCreateRequest(
            title="t",
            description="d",
            acceptance_criteria=["ac"],
            team=Team.BACKEND,
            created_by=fleet["pm"].id,
            project_id=fleet["project_id"],
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.MEDIUM,
        )
    )
    task.status = status
    task.assigned_to = owner.id
    task.claimed_by = (claimed_by or owner).id
    task.active_claimant_id = lock.id if lock is not None else None
    await fleet["db"].flush()
    return task


async def _lock_after_upgrade(fleet: dict[str, Any], task: TaskTable) -> object:
    db: AsyncSession = fleet["db"]
    await db.refresh(task)
    return task.active_claimant_id


@pytest.mark.asyncio
async def test_escalated_task_lock_moves_from_the_escalator_to_the_owner(
    fleet: dict[str, Any],
) -> None:
    dev, pm = fleet["dev"], fleet["pm"]
    escalated = await _task(fleet, status=TaskStatus.BLOCKED, owner=pm, lock=dev)
    unclaimed = await _task(fleet, status=TaskStatus.BLOCKED, owner=pm, lock=None)

    await _upgrade(fleet["db"])

    assert await _lock_after_upgrade(fleet, escalated) == pm.id
    assert await _lock_after_upgrade(fleet, unclaimed) == pm.id


@pytest.mark.asyncio
async def test_rows_that_are_not_stale_escalations_keep_their_lock(
    fleet: dict[str, Any],
) -> None:
    dev, pm = fleet["dev"], fleet["pm"]
    dependency_block = await _task(
        fleet, status=TaskStatus.BLOCKED, owner=dev, lock=dev
    )
    split_owner = await _task(
        fleet, status=TaskStatus.BLOCKED, owner=pm, claimed_by=dev, lock=dev
    )
    working = await _task(fleet, status=TaskStatus.IN_PROGRESS, owner=pm, lock=dev)

    await _upgrade(fleet["db"])

    assert await _lock_after_upgrade(fleet, dependency_block) == dev.id
    assert await _lock_after_upgrade(fleet, split_owner) == dev.id
    assert await _lock_after_upgrade(fleet, working) == dev.id
