"""
Kanban Service

Generates role-specific kanban board views from task data.
Supports swimlanes, cross-cell views, and real-time updates.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.db.tables import AgentTable, TaskTable
from roboco.models.base import TaskStatus, Team
from roboco.models.kanban import (
    KanbanBoard,
    KanbanBoardType,
    KanbanCard,
    KanbanColumn,
    KanbanSwimlane,
    get_column_config,
)

logger = structlog.get_logger()


class KanbanService:
    """
    Service for generating kanban board views.

    Provides:
    - Role-specific board layouts
    - Swimlane grouping (by priority, assignee)
    - Cross-cell aggregation for Main PM
    - Board-level roadmap view
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    # =========================================================================
    # CARD CREATION
    # =========================================================================

    async def _task_to_card(
        self, task: TaskTable, swimlane_key: str | None = None
    ) -> KanbanCard:
        """Convert a task to a kanban card."""
        # Get assignee name if assigned
        assignee_name = None
        if task.assigned_to and task.assignee:
            assignee_name = task.assignee.name

        # Calculate progress from updates
        progress = None
        if task.progress_updates:
            last_with_percentage = next(
                (u for u in reversed(task.progress_updates) if u.get("percentage")),
                None,
            )
            if last_with_percentage:
                progress = last_with_percentage["percentage"]

        # Count subtasks (would need a query in real implementation)
        subtask_count = 0

        return KanbanCard(
            id=task.id,
            title=task.title,
            description=task.quick_context or task.description[:200]
            if task.description
            else None,
            priority=task.priority,
            status=task.status,
            team=task.team,
            assigned_to=task.assigned_to,
            assignee_name=assignee_name,
            created_at=task.created_at,
            updated_at=task.updated_at,
            target_date=task.target_date,
            complexity=task.estimated_complexity,
            is_blocked=task.status == TaskStatus.BLOCKED,
            blocker_count=len(task.dependency_ids),
            progress_percentage=progress,
            commit_count=len(task.commits),
            has_subtasks=subtask_count > 0,
            subtask_count=subtask_count,
            quick_context=task.quick_context,
            swimlane_key=swimlane_key,
        )

    # =========================================================================
    # DEV BOARD
    # =========================================================================

    async def get_dev_board(
        self,
        team: Team,
        swimlane_by: str | None = None,  # "priority" or "assignee"
    ) -> KanbanBoard:
        """
        Get the developer kanban board for a cell.

        Columns: Backlog → Assigned → In Progress → Blocked → QA Review → Documenting → Done
        """
        # Get all tasks for the team
        result = await self.session.execute(
            select(TaskTable)
            .where(TaskTable.team == team)
            .order_by(TaskTable.priority, TaskTable.created_at.desc())
        )
        tasks = result.scalars().all()

        # Build the board
        if swimlane_by:
            return await self._build_swimlane_board(
                tasks, team, KanbanBoardType.DEV, swimlane_by
            )
        else:
            return await self._build_flat_board(tasks, team, KanbanBoardType.DEV)

    async def _build_flat_board(
        self,
        tasks: list[TaskTable],
        team: Team | None,
        board_type: KanbanBoardType,
    ) -> KanbanBoard:
        """Build a flat board without swimlanes."""
        column_config = get_column_config(board_type)

        # Create columns
        columns: dict[str, KanbanColumn] = {}
        for col_id, col_title, col_status in column_config:
            columns[col_id] = KanbanColumn(
                id=col_id,
                title=col_title,
                status=col_status,
                cards=[],
                card_count=0,
            )

        # Add cards to columns
        blocked_count = 0
        for task in tasks:
            card = await self._task_to_card(task)

            # Find the right column for this task's status
            for col_id, _, col_status in column_config:
                if task.status == col_status:
                    columns[col_id].cards.append(card)
                    columns[col_id].card_count += 1
                    break

            if task.status == TaskStatus.BLOCKED:
                blocked_count += 1

        return KanbanBoard(
            id=f"{board_type.value}-{team.value if team else 'all'}",
            title=f"{team.value.title() if team else 'All'} {board_type.value.title()} Board",
            board_type=board_type,
            team=team,
            columns=list(columns.values()),
            total_cards=len(tasks),
            blocked_count=blocked_count,
            last_updated=datetime.utcnow(),
        )

    async def _build_swimlane_board(
        self,
        tasks: list[TaskTable],
        team: Team | None,
        board_type: KanbanBoardType,
        swimlane_by: str,
    ) -> KanbanBoard:
        """Build a board with swimlanes."""
        column_config = get_column_config(board_type)

        # Group tasks by swimlane key
        swimlane_groups: dict[str, list[TaskTable]] = {}
        for task in tasks:
            if swimlane_by == "priority":
                key = f"P{task.priority}"
            elif swimlane_by == "assignee":
                key = str(task.assigned_to) if task.assigned_to else "Unassigned"
            else:
                key = "default"

            if key not in swimlane_groups:
                swimlane_groups[key] = []
            swimlane_groups[key].append(task)

        # Build swimlanes
        swimlanes: list[KanbanSwimlane] = []
        blocked_count = 0

        for lane_key in sorted(swimlane_groups.keys()):
            lane_tasks = swimlane_groups[lane_key]

            # Create columns for this swimlane
            columns: list[KanbanColumn] = []
            for col_id, col_title, col_status in column_config:
                cards = [
                    await self._task_to_card(t, lane_key)
                    for t in lane_tasks
                    if t.status == col_status
                ]
                columns.append(
                    KanbanColumn(
                        id=f"{lane_key}-{col_id}",
                        title=col_title,
                        status=col_status,
                        cards=cards,
                        card_count=len(cards),
                    )
                )
                blocked_count += sum(1 for c in cards if c.is_blocked)

            # Get lane title
            if swimlane_by == "priority":
                lane_title = f"Priority {lane_key}"
            elif swimlane_by == "assignee":
                if lane_key == "Unassigned":
                    lane_title = "Unassigned"
                else:
                    # Would need to look up agent name
                    lane_title = lane_key
            else:
                lane_title = lane_key

            swimlanes.append(
                KanbanSwimlane(
                    id=lane_key,
                    title=lane_title,
                    columns=columns,
                )
            )

        return KanbanBoard(
            id=f"{board_type.value}-{team.value if team else 'all'}-swimlane",
            title=f"{team.value.title() if team else 'All'} {board_type.value.title()} Board",
            board_type=board_type,
            team=team,
            swimlanes=swimlanes,
            total_cards=len(tasks),
            blocked_count=blocked_count,
            last_updated=datetime.utcnow(),
        )

    # =========================================================================
    # QA BOARD
    # =========================================================================

    async def get_qa_board(self, team: Team) -> KanbanBoard:
        """
        Get the QA kanban board for a cell.

        Columns: Awaiting Review → In Review → Passed → Failed
        """
        # QA only sees tasks in QA-relevant statuses
        qa_statuses = [
            TaskStatus.AWAITING_QA,
            TaskStatus.VERIFYING,
            TaskStatus.AWAITING_DOCUMENTATION,
            TaskStatus.NEEDS_REVISION,
        ]

        result = await self.session.execute(
            select(TaskTable)
            .where(
                TaskTable.team == team,
                TaskTable.status.in_(qa_statuses),
            )
            .order_by(TaskTable.priority, TaskTable.created_at.desc())
        )
        tasks = result.scalars().all()

        return await self._build_flat_board(tasks, team, KanbanBoardType.QA)

    # =========================================================================
    # DOCUMENTER BOARD
    # =========================================================================

    async def get_documenter_board(self, team: Team) -> KanbanBoard:
        """
        Get the documenter kanban board for a cell.

        Columns: Awaiting Handoff → Gathering → Writing → Published
        """
        # Documenter sees tasks awaiting documentation or completed
        doc_statuses = [
            TaskStatus.AWAITING_DOCUMENTATION,
            TaskStatus.IN_PROGRESS,  # Gathering
            TaskStatus.VERIFYING,  # Writing
            TaskStatus.COMPLETED,
        ]

        result = await self.session.execute(
            select(TaskTable)
            .where(
                TaskTable.team == team,
                TaskTable.status.in_(doc_statuses),
            )
            .order_by(TaskTable.priority, TaskTable.created_at.desc())
        )
        tasks = result.scalars().all()

        return await self._build_flat_board(tasks, team, KanbanBoardType.DOCUMENTER)

    # =========================================================================
    # PM BOARD
    # =========================================================================

    async def get_pm_board(self, team: Team) -> KanbanBoard:
        """
        Get the cell PM kanban board.

        Columns: Incoming → Triaged → Assigned → In Progress → Blocked → Done
        """
        result = await self.session.execute(
            select(TaskTable)
            .where(TaskTable.team == team)
            .order_by(TaskTable.priority, TaskTable.created_at.desc())
        )
        tasks = result.scalars().all()

        return await self._build_flat_board(tasks, team, KanbanBoardType.PM)

    # =========================================================================
    # MAIN PM BOARD (CROSS-CELL)
    # =========================================================================

    async def get_main_pm_board(self) -> KanbanBoard:
        """
        Get the Main PM kanban board with cross-cell view.

        Shows tasks across all cells with team-based swimlanes.
        """
        result = await self.session.execute(
            select(TaskTable).order_by(TaskTable.priority, TaskTable.created_at.desc())
        )
        tasks = result.scalars().all()

        # Use team as swimlane
        return await self._build_swimlane_board(
            tasks, None, KanbanBoardType.MAIN_PM, "team"
        )

    async def get_main_pm_board_flat(self) -> KanbanBoard:
        """Get Main PM board with team columns instead of swimlanes."""
        # Get tasks grouped by team
        result = await self.session.execute(
            select(TaskTable)
            .where(
                TaskTable.status.in_(
                    [
                        TaskStatus.IN_PROGRESS,
                        TaskStatus.BLOCKED,
                        TaskStatus.AWAITING_QA,
                    ]
                )
            )
            .order_by(TaskTable.priority, TaskTable.created_at.desc())
        )
        tasks = result.scalars().all()

        # Create columns for each team + incoming/done
        columns = [
            KanbanColumn(
                id="incoming", title="Incoming", status=TaskStatus.PENDING, cards=[]
            ),
            KanbanColumn(
                id="distributed",
                title="Distributed",
                status=TaskStatus.CLAIMED,
                cards=[],
            ),
            KanbanColumn(
                id="backend", title="Backend", status=TaskStatus.IN_PROGRESS, cards=[]
            ),
            KanbanColumn(
                id="frontend", title="Frontend", status=TaskStatus.IN_PROGRESS, cards=[]
            ),
            KanbanColumn(
                id="ux_ui", title="UX/UI", status=TaskStatus.IN_PROGRESS, cards=[]
            ),
            KanbanColumn(
                id="done", title="Done", status=TaskStatus.COMPLETED, cards=[]
            ),
        ]

        # Sort tasks into columns
        col_map = {col.id: col for col in columns}
        blocked_count = 0

        for task in tasks:
            card = await self._task_to_card(task)
            if task.team == Team.BACKEND:
                col_map["backend"].cards.append(card)
            elif task.team == Team.FRONTEND:
                col_map["frontend"].cards.append(card)
            elif task.team == Team.UX_UI:
                col_map["ux_ui"].cards.append(card)

            if task.status == TaskStatus.BLOCKED:
                blocked_count += 1

        # Update card counts
        for col in columns:
            col.card_count = len(col.cards)

        return KanbanBoard(
            id="main-pm-board",
            title="Main PM Overview",
            board_type=KanbanBoardType.MAIN_PM,
            team=None,
            columns=columns,
            total_cards=len(tasks),
            blocked_count=blocked_count,
            last_updated=datetime.utcnow(),
        )

    # =========================================================================
    # BOARD-LEVEL - ROADMAP
    # =========================================================================

    async def get_board_kanban(self) -> KanbanBoard:
        """
        Get the Board-level roadmap view.

        Columns: Ideas → Roadmap → In Development → Released
        """
        # Board sees high-priority tasks and epics
        result = await self.session.execute(
            select(TaskTable)
            .where(TaskTable.priority <= 1)  # P0 and P1 only
            .order_by(TaskTable.priority, TaskTable.created_at.desc())
        )
        tasks = result.scalars().all()

        return await self._build_flat_board(tasks, None, KanbanBoardType.BOARD)

    # =========================================================================
    # STATISTICS
    # =========================================================================

    async def get_board_stats(self, team: Team | None = None) -> dict[str, Any]:
        """Get kanban board statistics."""
        query = select(
            TaskTable.status,
            func.count(TaskTable.id),
        ).group_by(TaskTable.status)

        if team:
            query = query.where(TaskTable.team == team)

        result = await self.session.execute(query)
        status_counts = {row[0].value: row[1] for row in result.all()}

        return {
            "status_counts": status_counts,
            "total": sum(status_counts.values()),
            "blocked": status_counts.get("blocked", 0),
            "in_progress": status_counts.get("in_progress", 0),
            "completed": status_counts.get("completed", 0),
        }


# =============================================================================
# SERVICE FACTORY
# =============================================================================


def get_kanban_service(session: AsyncSession) -> KanbanService:
    """Get a KanbanService instance."""
    return KanbanService(session)
