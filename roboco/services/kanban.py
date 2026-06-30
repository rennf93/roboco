"""
Kanban Service

Generates role-specific kanban board views from task data.
Supports swimlanes, cross-cell views, and real-time updates.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, ClassVar
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.db.tables import AgentTable, TaskTable
from roboco.models.base import TaskStatus, TaskType, Team
from roboco.models.kanban import (
    KanbanBoard,
    KanbanBoardType,
    KanbanCard,
    KanbanColumn,
    KanbanSwimlane,
    get_column_config,
)
from roboco.services.base import BaseService
from roboco.utils.converters import require_uuid, to_python_uuid


class KanbanService(BaseService):
    """
    Service for generating kanban board views.

    Provides:
    - Role-specific board layouts
    - Swimlane grouping (by priority, assignee)
    - Cross-cell aggregation for Main PM
    - Board-level roadmap view
    """

    service_name: ClassVar[str] = "kanban"

    # =========================================================================
    # CARD CREATION
    # =========================================================================

    async def _load_subtask_counts(self, tasks: Sequence[TaskTable]) -> dict[UUID, int]:
        """#198: batch-count direct children per parent in ONE grouped query, so a
        board of N cards doesn't fire N child-count queries (and so the count is
        real, not a hardcoded 0)."""
        parent_ids = [t.id for t in tasks if t.id is not None]
        if not parent_ids:
            return {}
        result = await self.session.execute(
            select(TaskTable.parent_task_id, func.count(TaskTable.id))
            .where(TaskTable.parent_task_id.in_(parent_ids))
            .group_by(TaskTable.parent_task_id)
        )
        return {row[0]: int(row[1]) for row in result.all() if row[0] is not None}

    async def _task_to_card(
        self,
        task: TaskTable,
        swimlane_key: str | None = None,
        subtask_counts: dict[UUID, int] | None = None,
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

        # #198: real subtask count from the batch-loaded map (0 when no map / leaf).
        subtask_count = (
            subtask_counts.get(require_uuid(task.id), 0) if subtask_counts else 0
        )

        return KanbanCard(
            id=require_uuid(task.id),
            title=task.title,
            description=task.quick_context or task.description[:200]
            if task.description
            else None,
            priority=task.priority,
            status=task.status,
            team=task.team,
            assigned_to=to_python_uuid(task.assigned_to),
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

        Columns:
            Backlog → Assigned → In Progress → Blocked → QA Review → Documenting → Done
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
        tasks: Sequence[TaskTable],
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

        # Add cards to columns; any task whose status matches no configured
        # column lands in an 'Other' fallback so total_cards == sum(card_count)
        # and no card is built-then-silently-dropped (the vanished-card leak).
        other_cards: list[KanbanCard] = []
        blocked_count = 0
        subtask_counts = await self._load_subtask_counts(tasks)
        for task in tasks:
            card = await self._task_to_card(task, subtask_counts=subtask_counts)

            # Find the right column for this task's status
            placed = False
            for col_id, _, col_status in column_config:
                if task.status == col_status:
                    columns[col_id].cards.append(card)
                    columns[col_id].card_count += 1
                    placed = True
                    break
            if not placed:
                other_cards.append(card)

            if task.status == TaskStatus.BLOCKED:
                blocked_count += 1

        column_list = list(columns.values())
        if other_cards:
            column_list.append(
                KanbanColumn(
                    id="other",
                    title="Other",
                    status=None,
                    cards=other_cards,
                    card_count=len(other_cards),
                )
            )

        return KanbanBoard(
            id=f"{board_type.value}-{team.value if team else 'all'}",
            title=f"{team.value.title() if team else 'All'} {board_type.value.title()} Board",  # noqa: E501
            board_type=board_type,
            team=team,
            columns=column_list,
            total_cards=len(tasks),
            blocked_count=blocked_count,
            last_updated=datetime.now(UTC),
        )

    def _get_swimlane_key(self, task: TaskTable, swimlane_by: str) -> str:
        """Get the swimlane key for a task."""
        if swimlane_by == "priority":
            return f"P{task.priority}"
        if swimlane_by == "assignee":
            return str(task.assigned_to) if task.assigned_to else "Unassigned"
        return "default"

    def _get_swimlane_title(
        self,
        lane_key: str,
        swimlane_by: str,
        agent_names: dict[str, str],
    ) -> str:
        """Get the display title for a swimlane."""
        if swimlane_by == "priority":
            return f"Priority {lane_key}"
        if swimlane_by == "assignee":
            return (
                "Unassigned"
                if lane_key == "Unassigned"
                else agent_names.get(lane_key, lane_key)
            )
        return lane_key

    async def _fetch_agent_names(self, tasks: Sequence[TaskTable]) -> dict[str, str]:
        """Fetch agent names for all assignees in tasks."""
        assignee_ids = {t.assigned_to for t in tasks if t.assigned_to}
        if not assignee_ids:
            return {}

        agent_result = await self.session.execute(
            select(AgentTable).where(AgentTable.id.in_(assignee_ids))
        )
        return {str(agent.id): agent.name for agent in agent_result.scalars().all()}

    async def _build_swimlane_columns(
        self,
        lane_key: str,
        lane_tasks: list[TaskTable],
        column_config: list,
        subtask_counts: dict[UUID, int] | None = None,
    ) -> tuple[list[KanbanColumn], int]:
        """Build columns for a swimlane. Returns (columns, blocked_count)."""
        columns: list[KanbanColumn] = []
        blocked_count = 0

        for col_id, col_title, col_status in column_config:
            cards = [
                await self._task_to_card(t, lane_key, subtask_counts=subtask_counts)
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

        return columns, blocked_count

    async def _build_swimlane_board(
        self,
        tasks: Sequence[TaskTable],
        team: Team | None,
        board_type: KanbanBoardType,
        swimlane_by: str,
    ) -> KanbanBoard:
        """Build a board with swimlanes."""
        column_config = get_column_config(board_type)

        # Pre-fetch agent names for assignee swimlanes
        agent_names = (
            await self._fetch_agent_names(tasks) if swimlane_by == "assignee" else {}
        )
        # #198: load subtask counts once for the whole board (not per lane).
        subtask_counts = await self._load_subtask_counts(tasks)

        # Group tasks by swimlane key
        swimlane_groups: dict[str, list[TaskTable]] = {}
        for task in tasks:
            key = self._get_swimlane_key(task, swimlane_by)
            swimlane_groups.setdefault(key, []).append(task)

        # Build swimlanes
        swimlanes: list[KanbanSwimlane] = []
        total_blocked = 0

        for lane_key in sorted(swimlane_groups.keys()):
            columns, blocked = await self._build_swimlane_columns(
                lane_key,
                swimlane_groups[lane_key],
                column_config,
                subtask_counts=subtask_counts,
            )
            total_blocked += blocked

            swimlanes.append(
                KanbanSwimlane(
                    id=lane_key,
                    title=self._get_swimlane_title(lane_key, swimlane_by, agent_names),
                    columns=columns,
                )
            )

        return KanbanBoard(
            id=f"{board_type.value}-{team.value if team else 'all'}-swimlane",
            title=f"{team.value.title() if team else 'All'} {board_type.value.title()} Board",  # noqa: E501
            board_type=board_type,
            team=team,
            swimlanes=swimlanes,
            total_cards=len(tasks),
            blocked_count=total_blocked,
            last_updated=datetime.now(UTC),
        )

    # =========================================================================
    # QA BOARD
    # =========================================================================

    async def get_qa_board(self, team: Team) -> KanbanBoard:
        """
        Get the QA kanban board for a cell.

        Columns: Awaiting Review → In Review → Passed → Failed
        """
        # QA only sees tasks in QA-relevant statuses. VERIFYING is the dev's
        # self-verification (task still with the dev, not with QA) — excluded so
        # the QA board does not show dev-mid-verification as active QA work.
        qa_statuses = [
            TaskStatus.AWAITING_QA,
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
        # Documenter sees documentation-typed tasks in doc-relevant statuses.
        # The broad IN_PROGRESS/VERIFYING inclusion used to load any dev code
        # task happening to share the cell team; scope to task_type=documentation
        # so the board shows only the documenter's own pipeline.
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
                TaskTable.task_type == TaskType.DOCUMENTATION,
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
        # Load every status the board columns represent. The legacy filter
        # (in_progress/blocked/awaiting_qa only) excluded exactly the statuses
        # the incoming/distributed/done columns anchor, so those three columns
        # were structurally always empty.
        result = await self.session.execute(
            select(TaskTable)
            .where(
                TaskTable.status.in_(
                    [
                        TaskStatus.PENDING,
                        TaskStatus.CLAIMED,
                        TaskStatus.IN_PROGRESS,
                        TaskStatus.BLOCKED,
                        TaskStatus.AWAITING_QA,
                        TaskStatus.COMPLETED,
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
                id="coordination",
                title="Coordination",
                status=TaskStatus.IN_PROGRESS,
                cards=[],
            ),
            KanbanColumn(
                id="done", title="Done", status=TaskStatus.COMPLETED, cards=[]
            ),
        ]

        # Sort tasks into columns: status-keyed columns (incoming/distributed/
        # done) first, then the in-flight statuses route by team. Dict-dispatch
        # the routing instead of an if/elif ladder so the column rule is one
        # lookup (status wins over team; an in-flight status with no cell team
        # — Main PM, Board, fullstack, system — falls through to Coordination,
        # the cards the legacy chain discarded after counting them, #196).
        col_map = {col.id: col for col in columns}
        status_col = {
            TaskStatus.PENDING: "incoming",
            TaskStatus.CLAIMED: "distributed",
            TaskStatus.COMPLETED: "done",
        }
        team_col = {
            Team.BACKEND: "backend",
            Team.FRONTEND: "frontend",
            Team.UX_UI: "ux_ui",
        }
        blocked_count = 0
        subtask_counts = await self._load_subtask_counts(tasks)

        for task in tasks:
            card = await self._task_to_card(task, subtask_counts=subtask_counts)
            col_id = status_col.get(task.status)
            if col_id is None:
                col_id = team_col.get(task.team, "coordination")
            col_map[col_id].cards.append(card)
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
            last_updated=datetime.now(UTC),
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
