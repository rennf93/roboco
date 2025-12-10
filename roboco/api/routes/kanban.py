"""
Kanban API Routes

Role-specific kanban board views for task visualization.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.api.deps import get_db
from roboco.models.base import Team
from roboco.models.kanban import KanbanBoard
from roboco.services.kanban import get_kanban_service

router = APIRouter(prefix="/kanban", tags=["kanban"])


# =============================================================================
# CELL-LEVEL BOARDS
# =============================================================================


@router.get("/dev/{team}", response_model=KanbanBoard)
async def get_dev_board(
    team: Team,
    db: Annotated[AsyncSession, Depends(get_db)],
    swimlane_by: str | None = Query(
        default=None,
        description="Group by 'priority' or 'assignee'",
    ),
):
    """
    Get the developer kanban board for a cell.

    Columns: Backlog → Assigned → In Progress → Blocked → QA Review → Documenting → Done

    Optionally group by priority or assignee using swimlanes.
    """
    service = get_kanban_service(db)
    return await service.get_dev_board(team, swimlane_by)


@router.get("/qa/{team}", response_model=KanbanBoard)
async def get_qa_board(
    team: Team,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Get the QA kanban board for a cell.

    Columns: Awaiting Review → In Review → Passed → Failed
    """
    service = get_kanban_service(db)
    return await service.get_qa_board(team)


@router.get("/documenter/{team}", response_model=KanbanBoard)
async def get_documenter_board(
    team: Team,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Get the documenter kanban board for a cell.

    Columns: Awaiting Handoff → Gathering → Writing → Published
    """
    service = get_kanban_service(db)
    return await service.get_documenter_board(team)


@router.get("/pm/{team}", response_model=KanbanBoard)
async def get_pm_board(
    team: Team,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Get the cell PM kanban board.

    Columns: Incoming → Triaged → Assigned → In Progress → Blocked → Done
    """
    service = get_kanban_service(db)
    return await service.get_pm_board(team)


# =============================================================================
# MANAGEMENT BOARDS
# =============================================================================


@router.get("/main-pm", response_model=KanbanBoard)
async def get_main_pm_board(
    db: Annotated[AsyncSession, Depends(get_db)],
    flat: bool = Query(
        default=False,
        description="Use flat team columns instead of swimlanes",
    ),
):
    """
    Get the Main PM kanban board with cross-cell view.

    Shows tasks across all cells with team-based organization.

    - Default: Swimlanes by team
    - flat=true: Team columns (Backend | Frontend | UX/UI)
    """
    service = get_kanban_service(db)
    if flat:
        return await service.get_main_pm_board_flat()
    return await service.get_main_pm_board()


@router.get("/board", response_model=KanbanBoard)
async def get_board_kanban(
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Get the Board-level roadmap view.

    Columns: Ideas → Roadmap → In Development → Released

    Shows only high-priority (P0, P1) tasks.
    """
    service = get_kanban_service(db)
    return await service.get_board_kanban()


# =============================================================================
# STATISTICS
# =============================================================================


@router.get("/stats")
async def get_board_stats(
    db: Annotated[AsyncSession, Depends(get_db)],
    team: Team | None = None,
):
    """Get kanban board statistics."""
    service = get_kanban_service(db)
    return await service.get_board_stats(team)
