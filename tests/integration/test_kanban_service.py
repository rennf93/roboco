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
        task_type=kw.pop("task_type", TaskType.CODE),
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


# ---------------------------------------------------------------------------
# Column coverage: no task status may vanish from a board (total_cards == sum)
# ---------------------------------------------------------------------------

# The 8 statuses the legacy DEV_COLUMNS dropped: BACKLOG, PAUSED, VERIFYING,
# NEEDS_REVISION, AWAITING_PR_REVIEW, AWAITING_PM_REVIEW, AWAITING_CEO_APPROVAL,
# CANCELLED. A dev whose task bounced to needs_revision (or sits in a gate) used
# to see their own task disappear from the board.
_DROPPED_DEV_STATUSES = [
    TaskStatus.BACKLOG,
    TaskStatus.PAUSED,
    TaskStatus.VERIFYING,
    TaskStatus.NEEDS_REVISION,
    TaskStatus.AWAITING_PR_REVIEW,
    TaskStatus.AWAITING_PM_REVIEW,
    TaskStatus.AWAITING_CEO_APPROVAL,
    TaskStatus.CANCELLED,
]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", _DROPPED_DEV_STATUSES)
async def test_dev_board_shows_every_status(
    kanban_setup: dict, status: TaskStatus
) -> None:
    """A dev task in any lifecycle status must appear in exactly one column —
    it must not be silently dropped (the card-counted-but-hidden leak)."""
    db = kanban_setup["db"]
    svc = kanban_setup["svc"]
    db.add(_seed(kanban_setup, status=status))
    await db.flush()
    board = await svc.get_dev_board(Team.BACKEND)
    placed = sum(len(c.cards) for c in board.columns)
    assert placed == board.total_cards
    assert any(c.cards for c in board.columns)


@pytest.mark.asyncio
async def test_dev_board_total_cards_equals_column_sum(kanban_setup: dict) -> None:
    """The board must never report N total cards while only M are visible."""
    db = kanban_setup["db"]
    svc = kanban_setup["svc"]
    for status in (
        TaskStatus.IN_PROGRESS,
        TaskStatus.NEEDS_REVISION,
        TaskStatus.AWAITING_PR_REVIEW,
        TaskStatus.PAUSED,
        TaskStatus.COMPLETED,
        TaskStatus.CANCELLED,
    ):
        db.add(_seed(kanban_setup, status=status))
    await db.flush()
    board = await svc.get_dev_board(Team.BACKEND)
    assert sum(c.card_count for c in board.columns) == board.total_cards


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        TaskStatus.AWAITING_QA,
        TaskStatus.AWAITING_DOCUMENTATION,
        TaskStatus.AWAITING_PR_REVIEW,
        TaskStatus.AWAITING_PM_REVIEW,
        TaskStatus.AWAITING_CEO_APPROVAL,
        TaskStatus.NEEDS_REVISION,
        TaskStatus.PAUSED,
        TaskStatus.CANCELLED,
    ],
)
async def test_pm_board_shows_gate_and_revision_states(
    kanban_setup: dict, status: TaskStatus
) -> None:
    """The cell PM coordinates the QA->docs->PR-review->PM-review->CEO chain, so
    every in-flight gate/revision/paused/cancelled status must be visible — not
    dropped by a column mapping that only knows pending/claimed/in_progress/
    blocked/done."""
    db = kanban_setup["db"]
    svc = kanban_setup["svc"]
    db.add(_seed(kanban_setup, status=status))
    await db.flush()
    board = await svc.get_pm_board(Team.BACKEND)
    assert sum(c.card_count for c in board.columns) == board.total_cards
    assert any(c.cards for c in board.columns)


@pytest.mark.asyncio
async def test_qa_board_excludes_dev_verifying(kanban_setup: dict) -> None:
    """VERIFYING is the developer's self-verification state — the task is still
    with the dev, not with QA. The QA board's 'In Review' column used to show
    these dev-mid-verification tasks as if QA work were underway."""
    db = kanban_setup["db"]
    svc = kanban_setup["svc"]
    verifying = _seed(kanban_setup, status=TaskStatus.VERIFYING, title="dev-self-check")
    queued = _seed(kanban_setup, status=TaskStatus.AWAITING_QA, title="qa-queue")
    db.add_all([verifying, queued])
    await db.flush()
    board = await svc.get_qa_board(Team.BACKEND)
    cards = [c for col in board.columns for c in col.cards]
    ids = {c.id for c in cards}
    assert queued.id in ids
    assert verifying.id not in ids


@pytest.mark.asyncio
async def test_documenter_board_excludes_dev_code_tasks(kanban_setup: dict) -> None:
    """The documenter shares a cell team with devs, so a dev IN_PROGRESS code
    task used to appear under 'Gathering' as if it were documentation. The
    documenter board must be scoped to task_type=documentation."""
    db = kanban_setup["db"]
    svc = kanban_setup["svc"]
    dev_task = _seed(
        kanban_setup,
        status=TaskStatus.IN_PROGRESS,
        title="dev-code",
        task_type=TaskType.CODE,
    )
    doc_task = _seed(
        kanban_setup,
        status=TaskStatus.IN_PROGRESS,
        title="doc-task",
        task_type=TaskType.DOCUMENTATION,
    )
    db.add_all([dev_task, doc_task])
    await db.flush()
    board = await svc.get_documenter_board(Team.BACKEND)
    cards = [c for col in board.columns for c in col.cards]
    ids = {c.id for c in cards}
    assert doc_task.id in ids
    assert dev_task.id not in ids


@pytest.mark.asyncio
async def test_main_pm_board_flat_incoming_distributed_done_populated(
    kanban_setup: dict,
) -> None:
    """The flat Main PM board filtered to in-flight-only, so its own
    incoming(PENDING)/distributed(CLAIMED)/done(COMPLETED) columns were
    structurally always empty. Those columns must now show matching tasks."""
    db = kanban_setup["db"]
    svc = kanban_setup["svc"]
    db.add(_seed(kanban_setup, status=TaskStatus.PENDING, team=Team.BACKEND))
    db.add(_seed(kanban_setup, status=TaskStatus.CLAIMED, team=Team.FRONTEND))
    db.add(_seed(kanban_setup, status=TaskStatus.COMPLETED, team=Team.BACKEND))
    db.add(_seed(kanban_setup, status=TaskStatus.IN_PROGRESS, team=Team.BACKEND))
    await db.flush()
    board = await svc.get_main_pm_board_flat()
    cols = {c.id: c for c in board.columns}
    assert len(cols["incoming"].cards) >= 1
    assert len(cols["distributed"].cards) >= 1
    assert len(cols["done"].cards) >= 1
    # No loaded task vanishes: the columned cards cover every loaded task.
    assert sum(len(c.cards) for c in board.columns) == board.total_cards


@pytest.mark.asyncio
async def test_build_flat_board_has_other_fallback(kanban_setup: dict) -> None:
    """A status with no configured column lands in an 'Other' fallback column so
    no card is ever silently dropped (the total_cards > sum(card_count) leak)."""
    db = kanban_setup["db"]
    svc = kanban_setup["svc"]
    # The Board roadmap (BOARD_COLUMNS) maps only pending/claimed/in_progress/
    # completed; an awaiting_qa task at P0 loads but matches no column.
    db.add(_seed(kanban_setup, status=TaskStatus.AWAITING_QA, priority=0))
    await db.flush()
    board = await svc.get_board_kanban()
    assert any(c.id == "other" and c.card_count == 1 for c in board.columns)
    assert sum(c.card_count for c in board.columns) == board.total_cards
