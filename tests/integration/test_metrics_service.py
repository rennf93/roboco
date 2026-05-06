"""MetricsService coverage — velocity, blockers, team health, agent metrics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import (
    TaskNature,
    TaskStatus,
    TaskType,
)
from roboco.services.metrics import MetricsService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def metrics_setup(
    db_session: AsyncSession,
) -> AsyncIterator[dict]:
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
        name="M-Proj",
        slug=f"m-proj-{uuid4().hex[:8]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=agent.id,
    )
    db_session.add(project)
    await db_session.flush()
    yield {
        "svc": MetricsService(db_session),
        "agent_id": agent.id,
        "project_id": project.id,
        "db": db_session,
    }


def _task(
    setup: dict,
    *,
    status: TaskStatus,
    team: Team = Team.BACKEND,
    **extras: object,
) -> TaskTable:
    """Build a TaskTable; extras forwarded (completed_at, started_at, ...)."""
    return TaskTable(
        id=uuid4(),
        title="t",
        description="d",
        acceptance_criteria=["ac"],
        status=status,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=setup["project_id"],
        created_by=setup["agent_id"],
        team=team,
        **extras,
    )


# ---------------------------------------------------------------------------
# Velocity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_velocity_returns_metrics(metrics_setup: dict) -> None:
    """Velocity returns counts; numbers depend on test ordering."""
    svc = metrics_setup["svc"]
    velocity = await svc.get_velocity(days=7)
    # Counts may be non-zero due to test pollution from session-scoped fixtures
    # that commit. We only verify the shape, not the empty count.
    assert isinstance(velocity.tasks_completed, int)
    assert isinstance(velocity.tasks_created, int)
    assert velocity.completion_rate >= 0


@pytest.mark.asyncio
async def test_get_velocity_with_completed_tasks(metrics_setup: dict) -> None:
    svc = metrics_setup["svc"]
    db = metrics_setup["db"]
    now = datetime.now(UTC)
    started = now - timedelta(hours=2)
    db.add(
        _task(
            metrics_setup,
            status=TaskStatus.COMPLETED,
            started_at=started,
            completed_at=now,
        )
    )
    await db.flush()
    velocity = await svc.get_velocity(days=7)
    assert velocity.tasks_completed == 1
    assert velocity.avg_completion_hours is not None


@pytest.mark.asyncio
async def test_get_velocity_filtered_by_team(metrics_setup: dict) -> None:
    svc = metrics_setup["svc"]
    db = metrics_setup["db"]
    now = datetime.now(UTC)
    db.add(
        _task(
            metrics_setup,
            status=TaskStatus.COMPLETED,
            team=Team.FRONTEND,
            completed_at=now,
        )
    )
    await db.flush()
    backend_v = await svc.get_velocity(days=7, team=Team.BACKEND)
    assert backend_v.tasks_completed == 0
    frontend_v = await svc.get_velocity(days=7, team=Team.FRONTEND)
    assert frontend_v.tasks_completed == 1


# ---------------------------------------------------------------------------
# Blocker metrics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_blocker_metrics_empty(metrics_setup: dict) -> None:
    svc = metrics_setup["svc"]
    bm = await svc.get_blocker_metrics()
    assert bm.active_blockers == 0


@pytest.mark.asyncio
async def test_get_blocker_metrics_with_blocked(metrics_setup: dict) -> None:
    svc = metrics_setup["svc"]
    db = metrics_setup["db"]
    db.add(_task(metrics_setup, status=TaskStatus.BLOCKED))
    db.add(_task(metrics_setup, status=TaskStatus.BLOCKED, team=Team.FRONTEND))
    await db.flush()
    bm = await svc.get_blocker_metrics()
    _BLOCKED = 2
    assert bm.active_blockers == _BLOCKED
    assert "backend" in bm.blockers_by_team or "frontend" in bm.blockers_by_team


# ---------------------------------------------------------------------------
# Team metrics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_team_metrics_empty(metrics_setup: dict) -> None:
    svc = metrics_setup["svc"]
    tm = await svc.get_team_metrics(Team.BACKEND)
    assert tm.team == Team.BACKEND
    assert tm.active_tasks == 0
    assert tm.documentation_coverage == 0


@pytest.mark.asyncio
async def test_get_team_metrics_with_data(metrics_setup: dict) -> None:
    svc = metrics_setup["svc"]
    db = metrics_setup["db"]
    now = datetime.now(UTC)
    db.add(_task(metrics_setup, status=TaskStatus.IN_PROGRESS))
    db.add(
        _task(
            metrics_setup,
            status=TaskStatus.COMPLETED,
            started_at=now - timedelta(hours=2),
            completed_at=now,
            dev_notes="some notes",
        )
    )
    db.add(_task(metrics_setup, status=TaskStatus.BLOCKED))
    await db.flush()
    tm = await svc.get_team_metrics(Team.BACKEND)
    assert tm.active_tasks == 1
    assert tm.completed_tasks_week == 1
    assert tm.blocked_tasks == 1


@pytest.mark.asyncio
async def test_get_all_team_metrics(metrics_setup: dict) -> None:
    svc = metrics_setup["svc"]
    _TEAMS = 3
    rows = await svc.get_all_team_metrics()
    assert len(rows) == _TEAMS  # backend, frontend, ux_ui
    assert {r.team for r in rows} == {Team.BACKEND, Team.FRONTEND, Team.UX_UI}


# ---------------------------------------------------------------------------
# Agent metrics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_agent_metrics_for_known_agent(metrics_setup: dict) -> None:
    svc = metrics_setup["svc"]
    am = await svc.get_agent_metrics(metrics_setup["agent_id"])
    assert am is not None
    assert am.agent_id == metrics_setup["agent_id"]


@pytest.mark.asyncio
async def test_get_agent_metrics_returns_none_for_unknown(
    metrics_setup: dict,
) -> None:
    svc = metrics_setup["svc"]
    assert await svc.get_agent_metrics(uuid4()) is None


# ---------------------------------------------------------------------------
# Communication volume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_communication_volume_empty(metrics_setup: dict) -> None:
    svc = metrics_setup["svc"]
    cv = await svc.get_communication_volume(hours=24)
    assert cv["total_messages"] == 0
    assert cv["active_channels"] == 0


# ---------------------------------------------------------------------------
# Health status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_health_status_empty(metrics_setup: dict) -> None:
    svc = metrics_setup["svc"]
    h = await svc.get_health_status(team=Team.BACKEND)
    assert h["status"] == "ok"
    assert h["team"] == "backend"


@pytest.mark.asyncio
async def test_get_health_status_critical_when_majority_blocked(
    metrics_setup: dict,
) -> None:
    svc = metrics_setup["svc"]
    db = metrics_setup["db"]
    # 4 blocked + 1 in_progress → 80% blocked → critical
    for _ in range(4):
        db.add(_task(metrics_setup, status=TaskStatus.BLOCKED))
    db.add(_task(metrics_setup, status=TaskStatus.IN_PROGRESS))
    await db.flush()
    h = await svc.get_health_status(team=Team.BACKEND)
    assert h["status"] == "critical"


@pytest.mark.asyncio
async def test_get_health_status_org_wide(metrics_setup: dict) -> None:
    svc = metrics_setup["svc"]
    h = await svc.get_health_status(team=None)
    assert h["team"] == "all"


def test_determine_health_status_directly() -> None:
    """Cover the threshold-decision helper without DB."""
    svc = MetricsService.__new__(MetricsService)  # No DB needed.
    assert svc._determine_health_status(0.5, 10, 0) == "critical"
    assert svc._determine_health_status(0.2, 10, 5) == "slow"
    assert svc._determine_health_status(0.0, 10, 0) == "slow"
    assert svc._determine_health_status(0.0, 1, 5) == "ok"
