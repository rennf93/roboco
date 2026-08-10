"""TaskService.get_delivery_stats_30d against a real Postgres DB.

The exclusion filters (parent_task_id IS NULL, task_type != administrative,
source NOT IN LEAD_TIME_EXCLUDED_SOURCES) are exactly the kind of thing a
mocked ``session.execute`` can't prove actually executes as real SQL — the
pre-existing mocked tests in ``test_cockpit.py`` return canned rows
regardless of the WHERE clause. This seeds real rows and proves the
population the query actually selects.
"""

from __future__ import annotations

from dataclasses import dataclass
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
from roboco.services.task import (
    PEST_CONTROL_SOURCE,
    VIDEO_POST_SOURCE,
    X_POST_SOURCE,
    TaskService,
)
from sqlalchemy import delete

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class _TaskSpec:
    task_type: TaskType
    source: str
    created_at: datetime
    completed_at: datetime
    parent_task_id: UUID | None = None


async def _isolate_delivery_stats_population(session: AsyncSession) -> None:
    """Delete every existing row from ``tasks`` within this test's own
    (uncommitted, rolled-back-at-teardown) transaction.

    ``get_delivery_stats_30d`` is a deliberately unscoped, whole-table query
    (no ``project_id`` filter — it's a company-wide metric), so it sees every
    completed root task any OTHER test in a full-suite run has already
    committed, not just this test's own seeds. Since ``db_session`` rolls
    back on teardown (conftest.py), a delete here only ever hides those rows
    from THIS test's read; nothing is permanently lost.
    """
    await session.execute(delete(TaskTable))
    await session.flush()


async def _seed_project(session: AsyncSession) -> tuple[UUID, UUID]:
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
        name="Delivery Stats Scope Test Project",
        slug=f"delivery-stats-{uuid4().hex[:8]}",
        git_url="https://github.com/example/delivery-stats.git",
        default_branch="main",
        protected_branches=["main"],
        assigned_cell=Team.BACKEND,
        created_by=system_agent.id,
        is_active=True,
    )
    session.add(project)
    await session.flush()
    return UUID(str(project.id)), UUID(str(system_agent.id))


async def _seed_task(
    session: AsyncSession, project_id: UUID, created_by: UUID, spec: _TaskSpec
) -> UUID:
    task = TaskTable(
        id=uuid4(),
        title="Delivery stats fixture task",
        description="A description long enough to satisfy any length floor.",
        acceptance_criteria=["it exists"],
        status=TaskStatus.COMPLETED,
        priority=2,
        task_type=spec.task_type,
        nature=TaskNature.TECHNICAL,
        estimated_complexity=Complexity.MEDIUM,
        created_by=created_by,
        team=Team.BACKEND,
        project_id=project_id,
        source=spec.source,
        parent_task_id=spec.parent_task_id,
        created_at=spec.created_at,
        completed_at=spec.completed_at,
    )
    session.add(task)
    await session.flush()
    return UUID(str(task.id))


@pytest.mark.asyncio
async def test_excludes_held_draft_and_admin_includes_delivery_root(
    db_session: AsyncSession,
) -> None:
    """An approved X post and a board-exploration task never enter the
    median; a real delivery root does, and its own lead time is the value
    reported (not dragged down by the near-instant held/report completions).
    """
    project_id, agent_id = await _seed_project(db_session)
    await _isolate_delivery_stats_population(db_session)
    now = datetime.now(UTC)

    # Held draft: completes the instant the CEO approves it. task_type ==
    # ADMINISTRATIVE here means the task_type filter alone would exclude it,
    # so this seed does NOT isolate the source predicate — see the
    # video_post seed below for that.
    await _seed_task(
        db_session,
        project_id,
        agent_id,
        _TaskSpec(
            task_type=TaskType.ADMINISTRATIVE,
            source=X_POST_SOURCE,
            created_at=now - timedelta(seconds=5),
            completed_at=now,
        ),
    )
    # Board-program exploration: completes the moment the report is filed.
    await _seed_task(
        db_session,
        project_id,
        agent_id,
        _TaskSpec(
            task_type=TaskType.ADMINISTRATIVE,
            source=PEST_CONTROL_SOURCE,
            created_at=now - timedelta(seconds=5),
            completed_at=now,
        ),
    )
    # Held video-post draft: task_type=CODE (NOT administrative), so only
    # the source predicate (source NOT IN LEAD_TIME_EXCLUDED_SOURCES)
    # excludes this row — pins the source predicate in isolation, since
    # deleting it would leak this row into completed_30d/median even though
    # every other excluded seed above is already caught by task_type alone.
    await _seed_task(
        db_session,
        project_id,
        agent_id,
        _TaskSpec(
            task_type=TaskType.CODE,
            source=VIDEO_POST_SOURCE,
            created_at=now - timedelta(seconds=5),
            completed_at=now,
        ),
    )
    # Real delivery root: 20h lead time.
    await _seed_task(
        db_session,
        project_id,
        agent_id,
        _TaskSpec(
            task_type=TaskType.CODE,
            source="manual",
            created_at=now - timedelta(hours=20),
            completed_at=now,
        ),
    )

    stats = await TaskService(db_session).get_delivery_stats_30d()
    assert stats["completed_30d"] == 1
    assert stats["median_lead_time_hours"] == pytest.approx(20.0, abs=0.01)


@pytest.mark.asyncio
async def test_excludes_child_task_double_counted_under_its_root(
    db_session: AsyncSession,
) -> None:
    """A child (cell/dev subtask) that completes alongside its root must not
    be counted a second time — only the parentless root enters the median.
    """
    project_id, agent_id = await _seed_project(db_session)
    await _isolate_delivery_stats_population(db_session)
    now = datetime.now(UTC)

    root_id = await _seed_task(
        db_session,
        project_id,
        agent_id,
        _TaskSpec(
            task_type=TaskType.CODE,
            source="manual",
            created_at=now - timedelta(hours=10),
            completed_at=now,
        ),
    )
    await _seed_task(
        db_session,
        project_id,
        agent_id,
        _TaskSpec(
            task_type=TaskType.CODE,
            source="manual",
            created_at=now - timedelta(hours=1),
            completed_at=now,
            parent_task_id=root_id,
        ),
    )

    stats = await TaskService(db_session).get_delivery_stats_30d()
    assert stats["completed_30d"] == 1
    assert stats["median_lead_time_hours"] == pytest.approx(10.0, abs=0.01)
