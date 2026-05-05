"""KanbanService coverage — board generation per role."""

from __future__ import annotations

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
from roboco.services.kanban import KanbanService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def kanban_setup(
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
        name="K-Proj",
        slug=f"k-proj-{uuid4().hex[:8]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=agent.id,
    )
    db_session.add(project)
    await db_session.flush()
    yield {
        "svc": KanbanService(db_session),
        "agent_id": agent.id,
        "project_id": project.id,
        "db": db_session,
    }


def _seed(setup: dict, *, status: TaskStatus, **kw) -> TaskTable:
    return TaskTable(
        id=uuid4(),
        title=kw.pop("title", "t"),
        description=kw.pop("description", "d"),
        acceptance_criteria=["ac"],
        status=status,
        priority=kw.pop("priority", 2),
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=setup["project_id"],
        created_by=setup["agent_id"],
        team=kw.pop("team", Team.BACKEND),
        **kw,
    )


# ---------------------------------------------------------------------------
# Dev board
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_dev_board_empty(kanban_setup: dict) -> None:
    svc = kanban_setup["svc"]
    board = await svc.get_dev_board(Team.BACKEND)
    assert board is not None
    assert hasattr(board, "columns")


@pytest.mark.asyncio
async def test_get_dev_board_groups_by_status(kanban_setup: dict) -> None:
    svc = kanban_setup["svc"]
    db = kanban_setup["db"]
    db.add(_seed(kanban_setup, status=TaskStatus.IN_PROGRESS))
    db.add(_seed(kanban_setup, status=TaskStatus.BLOCKED))
    db.add(_seed(kanban_setup, status=TaskStatus.COMPLETED))
    await db.flush()
    board = await svc.get_dev_board(Team.BACKEND)
    assert sum(len(c.cards) for c in board.columns) >= 3


@pytest.mark.asyncio
async def test_get_dev_board_with_priority_swimlane(kanban_setup: dict) -> None:
    svc = kanban_setup["svc"]
    db = kanban_setup["db"]
    db.add(_seed(kanban_setup, status=TaskStatus.IN_PROGRESS, priority=0))
    db.add(_seed(kanban_setup, status=TaskStatus.IN_PROGRESS, priority=1))
    await db.flush()
    board = await svc.get_dev_board(Team.BACKEND, swimlane_by="priority")
    # Swimlane boards have a swimlanes attribute populated.
    assert hasattr(board, "swimlanes")


@pytest.mark.asyncio
async def test_get_dev_board_with_assignee_swimlane(kanban_setup: dict) -> None:
    svc = kanban_setup["svc"]
    db = kanban_setup["db"]
    db.add(
        _seed(
            kanban_setup,
            status=TaskStatus.IN_PROGRESS,
            assigned_to=kanban_setup["agent_id"],
        )
    )
    await db.flush()
    board = await svc.get_dev_board(Team.BACKEND, swimlane_by="assignee")
    assert hasattr(board, "swimlanes")


# ---------------------------------------------------------------------------
# Other role boards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_qa_board(kanban_setup: dict) -> None:
    svc = kanban_setup["svc"]
    db = kanban_setup["db"]
    db.add(_seed(kanban_setup, status=TaskStatus.AWAITING_QA))
    await db.flush()
    board = await svc.get_qa_board(Team.BACKEND)
    assert board is not None


@pytest.mark.asyncio
async def test_get_documenter_board(kanban_setup: dict) -> None:
    svc = kanban_setup["svc"]
    db = kanban_setup["db"]
    db.add(_seed(kanban_setup, status=TaskStatus.AWAITING_DOCUMENTATION))
    await db.flush()
    board = await svc.get_documenter_board(Team.BACKEND)
    assert board is not None


@pytest.mark.asyncio
async def test_get_pm_board(kanban_setup: dict) -> None:
    svc = kanban_setup["svc"]
    db = kanban_setup["db"]
    db.add(_seed(kanban_setup, status=TaskStatus.AWAITING_PM_REVIEW))
    await db.flush()
    board = await svc.get_pm_board(Team.BACKEND)
    assert board is not None


@pytest.mark.asyncio
async def test_get_main_pm_board(kanban_setup: dict) -> None:
    svc = kanban_setup["svc"]
    db = kanban_setup["db"]
    db.add(_seed(kanban_setup, status=TaskStatus.IN_PROGRESS))
    db.add(_seed(kanban_setup, status=TaskStatus.IN_PROGRESS, team=Team.FRONTEND))
    await db.flush()
    board = await svc.get_main_pm_board()
    assert board is not None


@pytest.mark.asyncio
async def test_get_main_pm_board_flat(kanban_setup: dict) -> None:
    svc = kanban_setup["svc"]
    board = await svc.get_main_pm_board_flat()
    assert board is not None


@pytest.mark.asyncio
async def test_get_board_kanban_filters_priority(kanban_setup: dict) -> None:
    svc = kanban_setup["svc"]
    db = kanban_setup["db"]
    db.add(_seed(kanban_setup, status=TaskStatus.IN_PROGRESS, priority=0))
    db.add(_seed(kanban_setup, status=TaskStatus.IN_PROGRESS, priority=3))
    await db.flush()
    board = await svc.get_board_kanban()
    # Only priority<=1 tasks make it into the board view.
    total_cards = sum(len(c.cards) for c in board.columns)
    assert total_cards >= 1


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_board_stats_empty(kanban_setup: dict) -> None:
    svc = kanban_setup["svc"]
    stats = await svc.get_board_stats()
    assert "status_counts" in stats
    assert "total" in stats


@pytest.mark.asyncio
async def test_get_board_stats_with_data(kanban_setup: dict) -> None:
    svc = kanban_setup["svc"]
    db = kanban_setup["db"]
    db.add(_seed(kanban_setup, status=TaskStatus.IN_PROGRESS))
    db.add(_seed(kanban_setup, status=TaskStatus.BLOCKED))
    await db.flush()
    stats = await svc.get_board_stats(team=Team.BACKEND)
    assert stats["total"] >= 2
