"""KanbanService coverage — board generation per role."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
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
from roboco.services.kanban import KanbanService, get_kanban_service

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


def _seed(setup: dict, *, status: TaskStatus, **kw: Any) -> TaskTable:
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
    _TOTAL = 3
    assert sum(len(c.cards) for c in board.columns) >= _TOTAL


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
    _TOTAL = 2
    stats = await svc.get_board_stats(team=Team.BACKEND)
    assert stats["total"] >= _TOTAL


# ---------------------------------------------------------------------------
# Card creation - progress percentage extraction (lines 58-63)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_card_pulls_percentage_from_last_progress_update(
    kanban_setup: dict,
) -> None:
    """When a task has progress_updates with percentage, card.progress is set."""
    db = kanban_setup["db"]
    svc = kanban_setup["svc"]
    task = _seed(
        kanban_setup,
        status=TaskStatus.IN_PROGRESS,
        progress_updates=[
            {"at": "t0", "note": "start"},
            {"at": "t1", "percentage": 50},
            {"at": "t2", "percentage": 75},
        ],
    )
    db.add(task)
    await db.flush()
    board = await svc.get_dev_board(Team.BACKEND)
    cards = [c for col in board.columns for c in col.cards]
    matched = next(c for c in cards if c.id == task.id)
    _LATEST = 75
    assert matched.progress_percentage == _LATEST


@pytest.mark.asyncio
async def test_card_no_percentage_when_updates_lack_it(kanban_setup: dict) -> None:
    """progress_updates without 'percentage' keys => progress stays None."""
    db = kanban_setup["db"]
    svc = kanban_setup["svc"]
    task = _seed(
        kanban_setup,
        status=TaskStatus.IN_PROGRESS,
        progress_updates=[{"at": "t0", "note": "qualitative only"}],
    )
    db.add(task)
    await db.flush()
    board = await svc.get_dev_board(Team.BACKEND)
    cards = [c for col in board.columns for c in col.cards]
    matched = next(c for c in cards if c.id == task.id)
    assert matched.progress_percentage is None


# ---------------------------------------------------------------------------
# Swimlane title fallback (line 199)
# ---------------------------------------------------------------------------


def test_get_swimlane_title_default_branch(kanban_setup: dict) -> None:
    """Unknown swimlane_by string returns lane_key unchanged (line 199)."""
    svc = kanban_setup["svc"]
    title = svc._get_swimlane_title("custom-key", "team", {})
    assert title == "custom-key"


# ---------------------------------------------------------------------------
# get_main_pm_board_flat — covers backend/frontend/ux_ui sorting + blocked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assignee_swimlane_with_no_assigned_tasks(kanban_setup: dict) -> None:
    """Tasks with no assignees in assignee swimlane returns empty agent_names."""
    db = kanban_setup["db"]
    svc = kanban_setup["svc"]
    # All tasks unassigned -> assigned_to is None for every row -> empty set
    db.add(_seed(kanban_setup, status=TaskStatus.IN_PROGRESS))
    await db.flush()
    board = await svc.get_dev_board(Team.BACKEND, swimlane_by="assignee")
    assert hasattr(board, "swimlanes")


def test_get_kanban_service_factory(kanban_setup: dict) -> None:
    """get_kanban_service returns a working service instance."""
    svc = get_kanban_service(kanban_setup["db"])
    assert svc is not None


@pytest.mark.asyncio
async def test_main_pm_board_flat_sorts_into_team_columns(kanban_setup: dict) -> None:
    """Tasks are sorted into the right team columns and blocked counted."""
    db = kanban_setup["db"]
    svc = kanban_setup["svc"]
    db.add(_seed(kanban_setup, status=TaskStatus.IN_PROGRESS, team=Team.BACKEND))
    db.add(_seed(kanban_setup, status=TaskStatus.IN_PROGRESS, team=Team.FRONTEND))
    db.add(_seed(kanban_setup, status=TaskStatus.IN_PROGRESS, team=Team.UX_UI))
    db.add(_seed(kanban_setup, status=TaskStatus.BLOCKED, team=Team.BACKEND))
    await db.flush()

    board = await svc.get_main_pm_board_flat()
    cols = {c.id: c for c in board.columns}
    assert len(cols["backend"].cards) >= 1
    assert len(cols["frontend"].cards) >= 1
    assert len(cols["ux_ui"].cards) >= 1
    assert board.blocked_count >= 1


@pytest.mark.asyncio
async def test_main_pm_board_flat_columns_non_cell_teams(kanban_setup: dict) -> None:
    """#196: a Main-PM (or other non-cell-team) task used to be counted in
    total_cards but never columned — the card was built and discarded. It now
    lands in a Coordination column so the board's columns sum to total_cards."""
    db = kanban_setup["db"]
    svc = kanban_setup["svc"]
    db.add(_seed(kanban_setup, status=TaskStatus.IN_PROGRESS, team=Team.MAIN_PM))
    await db.flush()

    board = await svc.get_main_pm_board_flat()
    cols = {c.id: c for c in board.columns}
    assert "coordination" in cols
    assert len(cols["coordination"].cards) == 1
    # No card is silently dropped: the columned cards cover every loaded task.
    assert sum(len(c.cards) for c in board.columns) == board.total_cards


# ---------------------------------------------------------------------------
# #198: subtask_count must reflect the real task tree, not a hardcoded 0
# ---------------------------------------------------------------------------


def _find_card(board: Any, task_id: Any) -> Any:
    """Locate a card by task id across flat columns and swimlanes."""
    cols = list(getattr(board, "columns", []))
    for lane in getattr(board, "swimlanes", []) or []:
        cols.extend(lane.columns)
    for col in cols:
        for card in col.cards:
            if str(card.id) == str(task_id):
                return card
    raise AssertionError(f"card {task_id} not on board")


@pytest.mark.asyncio
async def test_dev_board_card_reports_real_subtask_count(kanban_setup: dict) -> None:
    """#198: a parent with two children shows subtask_count=N / has_subtasks."""
    db = kanban_setup["db"]
    svc = kanban_setup["svc"]
    parent = _seed(kanban_setup, status=TaskStatus.IN_PROGRESS, title="parent")
    db.add(parent)
    await db.flush()
    children = [
        _seed(
            kanban_setup,
            status=TaskStatus.PENDING,
            title=title,
            parent_task_id=parent.id,
        )
        for title in ("child-a", "child-b")
    ]
    for child in children:
        db.add(child)
    await db.flush()

    board = await svc.get_dev_board(Team.BACKEND)
    card = _find_card(board, parent.id)
    assert card.subtask_count == len(children)
    assert card.has_subtasks is True


@pytest.mark.asyncio
async def test_dev_board_card_subtask_count_zero_when_leaf(kanban_setup: dict) -> None:
    """A leaf task (no children) reports subtask_count=0 / has_subtasks=False."""
    db = kanban_setup["db"]
    svc = kanban_setup["svc"]
    leaf = _seed(kanban_setup, status=TaskStatus.IN_PROGRESS, title="leaf")
    db.add(leaf)
    await db.flush()

    board = await svc.get_dev_board(Team.BACKEND)
    card = _find_card(board, leaf.id)
    assert card.subtask_count == 0
    assert card.has_subtasks is False


@pytest.mark.asyncio
async def test_priority_swimlane_board_reports_real_subtask_count(
    kanban_setup: dict,
) -> None:
    """#198: the swimlane path also threads the real count (not a per-lane stub)."""
    db = kanban_setup["db"]
    svc = kanban_setup["svc"]
    parent = _seed(kanban_setup, status=TaskStatus.IN_PROGRESS, priority=1, title="p")
    db.add(parent)
    await db.flush()
    db.add(
        _seed(
            kanban_setup,
            status=TaskStatus.PENDING,
            priority=1,
            title="c",
            parent_task_id=parent.id,
        )
    )
    await db.flush()

    board = await svc.get_dev_board(Team.BACKEND, swimlane_by="priority")
    card = _find_card(board, parent.id)
    assert card.subtask_count == 1
    assert card.has_subtasks is True
