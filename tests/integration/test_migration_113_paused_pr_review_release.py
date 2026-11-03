"""Migration 113: paused PR-review tasks are released or cancelled.

Runs the migration's own ``upgrade()`` through alembic's ``Operations`` on the
test session's connection; the ``db_session`` teardown rolls everything back.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
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

    from roboco.db.tables import TaskTable
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import Session

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "113_paused_pr_review_release.py"
)


async def _upgrade(db_session: AsyncSession) -> None:
    spec = importlib.util.spec_from_file_location("migration_113", _MIGRATION)
    assert spec is not None
    assert spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def _apply(session: Session) -> None:
        context = MigrationContext.configure(session.connection())
        with Operations.context(context):
            migration.upgrade()

    await db_session.run_sync(_apply)


@pytest_asyncio.fixture
async def fleet(db_session: AsyncSession) -> AsyncIterator[dict[str, Any]]:
    reviewer = AgentTable(
        id=uuid4(),
        name="rev",
        slug=f"rev-{uuid4().hex[:8]}",
        role=AgentRole.PR_REVIEWER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="rev",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(reviewer)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="Cell",
        slug=f"cell-{uuid4().hex[:8]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=reviewer.id,
    )
    db_session.add(project)
    await db_session.flush()
    yield {
        "svc": TaskService(db_session),
        "db": db_session,
        "reviewer": reviewer,
        "project_id": project.id,
    }


async def _review(
    fleet: dict[str, Any],
    *,
    status: TaskStatus,
    age_minutes: int,
    pr_number: int = 409,
    source: str = "internal_pr",
) -> TaskTable:
    svc: TaskService = fleet["svc"]
    task = await svc.create(
        TaskCreateRequest(
            title="Review",
            description="d",
            acceptance_criteria=["ac"],
            team=Team.BACKEND,
            created_by=fleet["reviewer"].id,
            project_id=fleet["project_id"],
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.MEDIUM,
            source=source,
        )
    )
    task.pr_number = pr_number
    task.status = status
    task.created_at = datetime.now(UTC) - timedelta(minutes=age_minutes)
    if status == TaskStatus.PAUSED:
        reviewer = fleet["reviewer"]
        task.assigned_to = reviewer.id
        task.claimed_by = reviewer.id
        task.active_claimant_id = reviewer.id
    await fleet["db"].flush()
    return task


@pytest.mark.asyncio
async def test_superseded_paused_review_is_cancelled(fleet: dict[str, Any]) -> None:
    old = await _review(fleet, status=TaskStatus.PAUSED, age_minutes=30)
    newer = await _review(fleet, status=TaskStatus.PENDING, age_minutes=1)

    await _upgrade(fleet["db"])
    await fleet["db"].refresh(old)
    await fleet["db"].refresh(newer)

    assert old.status == TaskStatus.CANCELLED
    assert old.active_claimant_id is None
    assert newer.status == TaskStatus.PENDING


@pytest.mark.asyncio
async def test_unsuperseded_paused_review_returns_to_pending_unclaimed(
    fleet: dict[str, Any],
) -> None:
    lone = await _review(fleet, status=TaskStatus.PAUSED, age_minutes=30)
    other_pr = await _review(
        fleet, status=TaskStatus.PENDING, age_minutes=1, pr_number=410
    )
    dev_paused = await _review(
        fleet, status=TaskStatus.PAUSED, age_minutes=5, pr_number=7, source="manual"
    )

    await _upgrade(fleet["db"])
    for row in (lone, other_pr, dev_paused):
        await fleet["db"].refresh(row)

    assert lone.status == TaskStatus.PENDING
    assert lone.assigned_to is None
    assert lone.claimed_by is None
    assert lone.active_claimant_id is None
    assert dev_paused.status == TaskStatus.PAUSED
