"""0.11.0 sequenced batch intake: tasks.batch_id + collision descriptor columns.

Migration 046 adds ``tasks.batch_id`` (indexed ``ix_tasks_batch_id``) plus the
per-task collision surface the SequencingService reads: ``intends_to_touch``
(text[]), and ``adds_migration`` / ``touches_shared`` (bool, NOT NULL default
false — a non-batch task declares no surface). The real upgrade/downgrade chain
is verified separately against a throwaway Postgres; these assertions guard the
resulting schema shape and a value round-trip.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import TaskNature, TaskStatus, TaskType
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_backend_project(
    db_session: AsyncSession,
) -> tuple[AgentTable, ProjectTable]:
    agent = AgentTable(
        id=uuid4(),
        name="Dev",
        slug=f"be-dev-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(agent)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="B-Proj",
        slug=f"b-proj-{uuid4().hex[:8]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=agent.id,
    )
    db_session.add(project)
    await db_session.flush()
    return agent, project


@pytest.mark.asyncio
async def test_batch_columns_and_index_exist(db_session: AsyncSession) -> None:
    rows = (
        await db_session.execute(
            text(
                "SELECT column_name, is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_name = 'tasks' AND column_name IN "
                "('batch_id', 'intends_to_touch', 'adds_migration', 'touches_shared')"
            )
        )
    ).all()
    by_name = {r[0]: (r[1], r[2]) for r in rows}
    assert set(by_name) == {
        "batch_id",
        "intends_to_touch",
        "adds_migration",
        "touches_shared",
    }
    assert by_name["batch_id"][0] == "YES"  # nullable
    assert by_name["intends_to_touch"][0] == "YES"  # nullable
    assert by_name["adds_migration"][0] == "NO"  # NOT NULL
    assert "false" in (by_name["adds_migration"][1] or "")  # default false
    assert by_name["touches_shared"][0] == "NO"
    assert "false" in (by_name["touches_shared"][1] or "")
    idx = (
        await db_session.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'tasks' AND indexname = 'ix_tasks_batch_id'"
            )
        )
    ).first()
    assert idx is not None, "ix_tasks_batch_id must exist on tasks"


@pytest.mark.asyncio
async def test_batch_fields_round_trip(db_session: AsyncSession) -> None:
    _, project = await _seed_backend_project(db_session)
    batch = uuid4()
    task = TaskTable(
        id=uuid4(),
        title="t",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=project.id,
        created_by=project.created_by,
        team=Team.BACKEND,
        batch_id=batch,
        intends_to_touch=["svc/x.py", "page/y.tsx"],
        adds_migration=True,
        touches_shared=True,
    )
    db_session.add(task)
    await db_session.flush()
    fetched = (
        await db_session.execute(
            text(
                "SELECT batch_id, intends_to_touch, adds_migration, touches_shared "
                "FROM tasks WHERE id = :id"
            ),
            {"id": task.id},
        )
    ).first()
    assert fetched is not None
    assert fetched[0] == batch
    assert fetched[1] == ["svc/x.py", "page/y.tsx"]
    assert fetched[2] is True
    assert fetched[3] is True


@pytest.mark.asyncio
async def test_non_batch_task_gets_defaults(db_session: AsyncSession) -> None:
    _, project = await _seed_backend_project(db_session)
    task = TaskTable(
        id=uuid4(),
        title="t",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=project.id,
        created_by=project.created_by,
        team=Team.BACKEND,
    )
    db_session.add(task)
    await db_session.flush()
    await db_session.refresh(task)
    assert task.batch_id is None
    assert task.intends_to_touch is None
    assert task.adds_migration is False
    assert task.touches_shared is False
