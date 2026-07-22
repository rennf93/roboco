"""TaskService.project_month_spend_usd against a real Postgres DB.

The join (agent_spawn_sessions.task_id, a plain String(36), to tasks.id via a
cast) and the month-boundary filter are exactly the kind of thing a mocked
unit test can't prove actually executes as real SQL. This also pins the
open-session live-token pricing fix: a still-open session's
estimated_cost_usd is null until close, so it must be priced from its token
columns via calculate_cost, not silently summed as $0.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from roboco.billing.pricing import calculate_cost
from roboco.db.tables import AgentSpawnSessionTable, AgentTable, ProjectTable, TaskTable
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

_MODEL = "claude-sonnet-5"


async def _seed_project(session: AsyncSession) -> tuple[UUID, UUID]:
    """Seed a system agent + project. Returns ``(project_id, system_agent_id)``
    — the latter doubles as the task-row FK target below."""
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
        name="Budget Spend Test Project",
        slug=f"budget-spend-{uuid4().hex[:8]}",
        git_url="https://github.com/example/budget-spend.git",
        default_branch="main",
        protected_branches=["main"],
        assigned_cell=Team.BACKEND,
        created_by=system_agent.id,
        is_active=True,
    )
    session.add(project)
    await session.flush()
    return UUID(str(project.id)), UUID(str(system_agent.id))


async def _seed_task(session: AsyncSession, project_id: UUID, created_by: UUID) -> UUID:
    task = TaskTable(
        id=uuid4(),
        title="Budget spend fixture task",
        description="A description long enough to satisfy any length floor.",
        acceptance_criteria=["it exists"],
        status=TaskStatus.IN_PROGRESS,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        estimated_complexity=Complexity.MEDIUM,
        created_by=created_by,
        team=Team.BACKEND,
        project_id=project_id,
    )
    session.add(task)
    await session.flush()
    return UUID(str(task.id))


def _spawn_session(
    task_id: UUID,
    *,
    started_at: datetime,
    estimated_cost_usd: float | None,
    ended_at: datetime | None,
    tokens: tuple[int, int] = (0, 0),
) -> AgentSpawnSessionTable:
    tokens_input, tokens_output = tokens
    return AgentSpawnSessionTable(
        id=uuid4(),
        agent_slug="be-dev-1",
        team="backend",
        role="developer",
        model=_MODEL,
        task_id=str(task_id),
        started_at=started_at,
        ended_at=ended_at,
        estimated_cost_usd=estimated_cost_usd,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
    )


@pytest.mark.asyncio
async def test_sums_closed_and_prices_open_session(db_session: AsyncSession) -> None:
    """A closed session's estimated_cost_usd + an open session's live-token
    price (calculate_cost) — not the open session silently counted as $0."""
    project_id, agent_id = await _seed_project(db_session)
    task_id = await _seed_task(db_session, project_id, agent_id)
    now = datetime.now(UTC)

    db_session.add(
        _spawn_session(
            task_id,
            started_at=now - timedelta(hours=2),
            estimated_cost_usd=2.5,
            ended_at=now - timedelta(hours=1),
        )
    )
    db_session.add(
        _spawn_session(
            task_id,
            started_at=now - timedelta(minutes=30),
            estimated_cost_usd=None,
            ended_at=None,
            tokens=(100_000, 50_000),
        )
    )
    await db_session.flush()

    expected_open_cost = calculate_cost(
        model=_MODEL, tokens_input=100_000, tokens_output=50_000
    )
    assert expected_open_cost > 0, (
        "fixture model must be priced for this to be a real test"
    )

    svc = TaskService(db_session)
    total = await svc.project_month_spend_usd(project_id)
    assert total == pytest.approx(2.5 + expected_open_cost)


@pytest.mark.asyncio
async def test_excludes_last_months_session(db_session: AsyncSession) -> None:
    """A session that started before this calendar month's boundary must not
    count, even though its cost is closed and non-zero."""
    project_id, agent_id = await _seed_project(db_session)
    task_id = await _seed_task(db_session, project_id, agent_id)
    month_start = datetime.now(UTC).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    last_month = month_start - timedelta(days=1)

    db_session.add(
        _spawn_session(
            task_id,
            started_at=last_month,
            estimated_cost_usd=50.0,
            ended_at=last_month + timedelta(hours=1),
        )
    )
    await db_session.flush()

    svc = TaskService(db_session)
    total = await svc.project_month_spend_usd(project_id)
    assert total == 0.0


@pytest.mark.asyncio
async def test_join_excludes_other_projects_tasks(db_session: AsyncSession) -> None:
    """A session on ANOTHER project's task must never bleed into this
    project's sum — proves the join filters by project_id, not just presence
    in agent_spawn_sessions."""
    project_id, agent_id = await _seed_project(db_session)
    other_project_id, other_agent_id = await _seed_project(db_session)
    my_task_id = await _seed_task(db_session, project_id, agent_id)
    other_task_id = await _seed_task(db_session, other_project_id, other_agent_id)
    now = datetime.now(UTC)

    db_session.add(
        _spawn_session(
            my_task_id,
            started_at=now - timedelta(hours=1),
            estimated_cost_usd=1.0,
            ended_at=now,
        )
    )
    db_session.add(
        _spawn_session(
            other_task_id,
            started_at=now - timedelta(hours=1),
            estimated_cost_usd=999.0,
            ended_at=now,
        )
    )
    await db_session.flush()

    svc = TaskService(db_session)
    total = await svc.project_month_spend_usd(project_id)
    assert total == pytest.approx(1.0)
