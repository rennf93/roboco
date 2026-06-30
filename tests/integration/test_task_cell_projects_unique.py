"""The task_cell_projects UNIQUE(task_id, team) constraint must hold on the real
Postgres schema — the 'one project per cell per task' invariant (#67).

The cell-map resolution tests stub the map against SimpleNamespace objects and
never touch a DB session, so the real constraint is unexercised. If it were
mis-declared or dropped, two same-team rows could coexist and
``_resolve_subtask_project`` would non-deterministically return one — cutting a
subtask's branch/PR against the WRONG repo. This integration test inserts two
rows with the same (task_id, team) and asserts the second raises IntegrityError
on ``uq_task_cell_projects_task_team``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import (
    AgentTable,
    ProjectTable,
    TaskCellProjectTable,
    TaskTable,
)
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType
from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def setup_task_and_project(
    db_session: AsyncSession,
) -> AsyncIterator[dict]:
    system = AgentTable(
        id=uuid4(),
        name="System",
        slug=f"system-{uuid4().hex[:8]}",
        role=AgentRole.SYSTEM,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="s",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(system)
    await db_session.flush()

    project = ProjectTable(
        id=uuid4(),
        name="P",
        slug=f"p-{uuid4().hex[:6]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=system.id,
    )
    db_session.add(project)
    await db_session.flush()

    task = TaskTable(
        id=uuid4(),
        title="coordination root",
        description="a real description over twenty chars long",
        acceptance_criteria=["criterion one"],
        team=Team.BACKEND,
        status=TaskStatus.PENDING,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        estimated_complexity=Complexity.MEDIUM,
        created_by=system.id,
        project_id=project.id,
    )
    db_session.add(task)
    await db_session.flush()

    yield {"task_id": task.id, "project_id": project.id}


@pytest.mark.asyncio
async def test_duplicate_task_team_raises_integrity_error(
    db_session: AsyncSession, setup_task_and_project: dict
) -> None:
    """A second row with the same (task_id, team) violates the unique constraint."""
    task_id = setup_task_and_project["task_id"]
    project_id = setup_task_and_project["project_id"]

    first = TaskCellProjectTable(
        id=uuid4(), task_id=task_id, team=Team.BACKEND, project_id=project_id
    )
    db_session.add(first)
    await db_session.flush()

    second = TaskCellProjectTable(
        id=uuid4(), task_id=task_id, team=Team.BACKEND, project_id=project_id
    )
    db_session.add(second)

    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_same_task_different_teams_allowed(
    db_session: AsyncSession, setup_task_and_project: dict
) -> None:
    """One project per cell per task — different cells coexist for one task."""
    task_id = setup_task_and_project["task_id"]
    project_id = setup_task_and_project["project_id"]

    db_session.add_all(
        [
            TaskCellProjectTable(
                id=uuid4(), task_id=task_id, team=Team.BACKEND, project_id=project_id
            ),
            TaskCellProjectTable(
                id=uuid4(), task_id=task_id, team=Team.FRONTEND, project_id=project_id
            ),
        ]
    )
    await db_session.flush()  # no error — different teams
