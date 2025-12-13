"""
Task Service

Provides CRUD operations and lifecycle management for tasks.
Handles status transitions, assignments, and queries.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import structlog
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.db.tables import AgentTable, HandoffTable, TaskTable
from roboco.enforcement import (
    TaskLifecycleError,
    TaskOwnershipError,
    validate_task_ownership,
    validate_task_transition,
)
from roboco.models.base import Complexity, HandoffStatus, TaskStatus, Team

logger = structlog.get_logger()


@dataclass
class TaskCreateRequest:
    """Request data for creating a task."""

    title: str
    description: str
    acceptance_criteria: list[str]
    team: Team
    created_by: UUID
    priority: int = 2
    parent_task_id: UUID | None = None
    target_date: datetime | None = None
    estimated_complexity: Complexity = field(default=Complexity.MEDIUM)


class TaskService:
    """
    Service for managing tasks.

    Provides:
    - CRUD operations
    - Status transitions with validation
    - Assignment and claiming
    - Queries by team, status, assignee
    - Dependency management
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    # =========================================================================
    # CRUD OPERATIONS
    # =========================================================================

    async def create(self, req: TaskCreateRequest) -> TaskTable:
        """Create a new task."""
        task = TaskTable(
            title=req.title,
            description=req.description,
            acceptance_criteria=req.acceptance_criteria,
            team=req.team,
            created_by=req.created_by,
            priority=req.priority,
            parent_task_id=req.parent_task_id,
            target_date=req.target_date,
            estimated_complexity=req.estimated_complexity,
            status=TaskStatus.PENDING,
        )
        self.session.add(task)
        await self.session.flush()

        logger.info(
            "Task created",
            task_id=str(task.id),
            title=req.title,
            team=req.team.value,
        )
        return task

    async def get(self, task_id: UUID) -> TaskTable | None:
        """Get a task by ID."""
        result = await self.session.execute(
            select(TaskTable).where(TaskTable.id == task_id)
        )
        return result.scalar_one_or_none()

    async def update(
        self,
        task_id: UUID,
        **updates: Any,
    ) -> TaskTable | None:
        """Update a task."""
        task = await self.get(task_id)
        if not task:
            return None

        for key, value in updates.items():
            if hasattr(task, key) and value is not None:
                setattr(task, key, value)

        await self.session.flush()

        logger.info(
            "Task updated",
            task_id=str(task_id),
            updates=list(updates.keys()),
        )
        return task

    async def delete(self, task_id: UUID) -> bool:
        """Delete a task."""
        task = await self.get(task_id)
        if not task:
            return False

        await self.session.delete(task)
        await self.session.flush()

        logger.info("Task deleted", task_id=str(task_id))
        return True

    # =========================================================================
    # STATUS TRANSITIONS
    # =========================================================================

    async def claim(self, task_id: UUID, agent_id: UUID) -> TaskTable | None:
        """
        Claim a task for an agent.

        Validates:
        - Task exists and is in PENDING status
        - Agent belongs to the same team as the task
        """
        task = await self.get(task_id)
        if not task:
            return None

        if task.status != TaskStatus.PENDING:
            logger.warning(
                "Cannot claim task - not pending",
                task_id=str(task_id),
                current_status=task.status.value,
            )
            return None

        # Validate agent belongs to the task's team
        agent_result = await self.session.execute(
            select(AgentTable).where(AgentTable.id == agent_id)
        )
        agent = agent_result.scalar_one_or_none()

        if agent and task.team and agent.team != task.team:
            logger.warning(
                "Cannot claim task - agent not in task's team",
                task_id=str(task_id),
                agent_team=agent.team.value if agent.team else None,
                task_team=task.team.value,
            )
            return None

        task.assigned_to = cast("Any", agent_id)
        task.claimed_at = datetime.now(UTC)
        task.status = TaskStatus.CLAIMED
        await self.session.flush()

        logger.info(
            "Task claimed",
            task_id=str(task_id),
            agent_id=str(agent_id),
        )
        return task

    async def start(
        self, task_id: UUID, agent_id: UUID | None = None
    ) -> TaskTable | None:
        """
        Start working on a task.

        Args:
            task_id: The task to start
            agent_id: Optional agent ID to validate ownership

        Returns:
            The started task, or None if not allowed
        """
        task = await self.get(task_id)
        if not task:
            return None

        # Validate ownership if agent_id provided
        if agent_id is not None:
            try:
                assigned = task.assigned_to
                validate_task_ownership(
                    agent_id=str(agent_id),
                    task_id=str(task_id),
                    task_assigned_to=str(assigned) if assigned else None,
                    task_team=task.team.value if task.team else "backend",
                    action="start",
                )
            except TaskOwnershipError as e:
                logger.warning(
                    "Cannot start task - ownership validation failed",
                    task_id=str(task_id),
                    agent_id=str(agent_id),
                    error=str(e),
                )
                return None

        if task.status not in (TaskStatus.CLAIMED, TaskStatus.PAUSED):
            logger.warning(
                "Cannot start task - invalid status",
                task_id=str(task_id),
                current_status=task.status.value,
            )
            return None

        task.started_at = datetime.now(UTC)
        task.status = TaskStatus.IN_PROGRESS
        await self.session.flush()

        logger.info("Task started", task_id=str(task_id))
        return task

    async def block(self, task_id: UUID, blocker_task_id: UUID) -> TaskTable | None:
        """Block a task due to a dependency."""
        task = await self.get(task_id)
        if not task:
            return None

        if blocker_task_id not in task.dependency_ids:
            new_deps = [*task.dependency_ids, blocker_task_id]
            task.dependency_ids = new_deps
        task.status = TaskStatus.BLOCKED
        await self.session.flush()

        # Update the blocker task to reference this as blocked
        blocker = await self.get(blocker_task_id)
        if blocker and task_id not in blocker.blocker_ids:
            blocker.blocker_ids = [*blocker.blocker_ids, task_id]
            await self.session.flush()

        logger.info(
            "Task blocked",
            task_id=str(task_id),
            blocker_id=str(blocker_task_id),
        )
        return task

    async def unblock(self, task_id: UUID) -> TaskTable | None:
        """Unblock a task and resume to in_progress."""
        task = await self.get(task_id)
        if not task:
            return None

        if task.status != TaskStatus.BLOCKED:
            return None

        task.status = TaskStatus.IN_PROGRESS
        await self.session.flush()

        logger.info("Task unblocked", task_id=str(task_id))
        return task

    async def pause(self, task_id: UUID) -> TaskTable | None:
        """Pause a task."""
        task = await self.get(task_id)
        if not task:
            return None

        if task.status != TaskStatus.IN_PROGRESS:
            return None

        task.status = TaskStatus.PAUSED
        await self.session.flush()

        logger.info("Task paused", task_id=str(task_id))
        return task

    async def resume(self, task_id: UUID) -> TaskTable | None:
        """Resume a paused task."""
        task = await self.get(task_id)
        if not task:
            return None

        if task.status != TaskStatus.PAUSED:
            return None

        task.status = TaskStatus.IN_PROGRESS
        await self.session.flush()

        logger.info("Task resumed", task_id=str(task_id))
        return task

    async def submit_for_verification(self, task_id: UUID) -> TaskTable | None:
        """Submit task for self-verification."""
        task = await self.get(task_id)
        if not task:
            return None

        if task.status != TaskStatus.IN_PROGRESS:
            return None

        task.status = TaskStatus.VERIFYING
        await self.session.flush()

        logger.info("Task submitted for verification", task_id=str(task_id))
        return task

    async def submit_for_qa(self, task_id: UUID) -> TaskTable | None:
        """Submit task for QA review."""
        task = await self.get(task_id)
        if not task:
            return None

        if task.status != TaskStatus.VERIFYING:
            return None

        task.self_verified = True
        task.status = TaskStatus.AWAITING_QA
        await self.session.flush()

        logger.info("Task submitted for QA", task_id=str(task_id))
        return task

    async def pass_qa(
        self, task_id: UUID, notes: str | None = None
    ) -> TaskTable | None:
        """Mark task as passed QA."""
        task = await self.get(task_id)
        if not task:
            return None

        if task.status != TaskStatus.AWAITING_QA:
            return None

        if notes:
            task.qa_notes = notes
        task.qa_verified = True
        task.status = TaskStatus.AWAITING_DOCUMENTATION
        await self.session.flush()

        logger.info("Task passed QA", task_id=str(task_id))
        return task

    async def fail_qa(self, task_id: UUID, notes: str) -> TaskTable | None:
        """Mark task as failed QA."""
        task = await self.get(task_id)
        if not task:
            return None

        if task.status != TaskStatus.AWAITING_QA:
            return None

        task.qa_notes = notes
        task.qa_verified = False
        task.status = TaskStatus.NEEDS_REVISION
        await self.session.flush()

        logger.info("Task failed QA", task_id=str(task_id))
        return task

    async def complete(
        self,
        task_id: UUID,
        skip_handoff_check: bool = False,
    ) -> TaskTable | None:
        """
        Mark task as completed.

        Enforces handoff requirement: tasks in AWAITING_DOCUMENTATION
        must have an accepted handoff before completion.

        Args:
            task_id: The task to complete
            skip_handoff_check: Skip handoff requirement (for small tasks)

        Returns:
            The completed task or None if completion not allowed
        """
        task = await self.get(task_id)
        if not task:
            return None

        # Validate transition using enforcement layer
        try:
            validate_task_transition(task.status.value, TaskStatus.COMPLETED.value)
        except TaskLifecycleError:
            # Allow from specific states
            if task.status not in (
                TaskStatus.AWAITING_DOCUMENTATION,
                TaskStatus.AWAITING_QA,  # Small tasks may skip docs
                TaskStatus.VERIFYING,  # Solo dev may skip QA
            ):
                return None

        # Enforce handoff requirement for tasks that went through full lifecycle
        if task.status == TaskStatus.AWAITING_DOCUMENTATION and not skip_handoff_check:
            handoff_result = await self.session.execute(
                select(HandoffTable).where(
                    HandoffTable.task_id == task_id,
                    HandoffTable.status == HandoffStatus.ACCEPTED,
                )
            )
            handoff = handoff_result.scalar_one_or_none()

            if not handoff:
                logger.warning(
                    "Cannot complete task - handoff required",
                    task_id=str(task_id),
                    status=task.status.value,
                )
                return None

        task.completed_at = datetime.now(UTC)
        task.status = TaskStatus.COMPLETED
        await self.session.flush()

        # Unblock any tasks waiting on this one
        await self._unblock_dependents(task_id)

        logger.info("Task completed", task_id=str(task_id))
        return task

    async def cancel(self, task_id: UUID) -> TaskTable | None:
        """Cancel a task."""
        task = await self.get(task_id)
        if not task:
            return None

        task.status = TaskStatus.CANCELLED
        await self.session.flush()

        logger.info("Task cancelled", task_id=str(task_id))
        return task

    async def _unblock_dependents(self, completed_task_id: UUID) -> None:
        """Unblock tasks that were waiting on the completed task."""
        result = await self.session.execute(
            select(TaskTable).where(
                TaskTable.dependency_ids.contains([completed_task_id])
            )
        )
        blocked_tasks = result.scalars().all()

        for task in blocked_tasks:
            # Remove the completed task from dependencies
            task.dependency_ids = [
                dep_id for dep_id in task.dependency_ids if dep_id != completed_task_id
            ]
            # If no more dependencies, unblock
            if not task.dependency_ids and task.status == TaskStatus.BLOCKED:
                task.status = TaskStatus.IN_PROGRESS
                logger.info(
                    "Task auto-unblocked",
                    task_id=str(task.id),
                    completed_dependency=str(completed_task_id),
                )

        await self.session.flush()

    # =========================================================================
    # PROGRESS AND CHECKPOINTS
    # =========================================================================

    async def add_progress(
        self,
        task_id: UUID,
        agent_id: UUID,
        message: str,
        percentage: int | None = None,
    ) -> TaskTable | None:
        """Add a progress update to a task."""
        task = await self.get(task_id)
        if not task:
            return None

        update = {
            "timestamp": datetime.now(UTC).isoformat(),
            "agent_id": str(agent_id),
            "message": message,
            "percentage": percentage,
        }
        task.progress_updates = [*task.progress_updates, update]
        await self.session.flush()

        return task

    async def add_checkpoint(
        self,
        task_id: UUID,
        agent_id: UUID,
        state_summary: str,
        remaining_work: list[str],
        notes: str | None = None,
    ) -> TaskTable | None:
        """Add a checkpoint for state recovery."""
        task = await self.get(task_id)
        if not task:
            return None

        checkpoint = {
            "id": str(UUID(int=len(task.checkpoints))),
            "timestamp": datetime.now(UTC).isoformat(),
            "agent_id": str(agent_id),
            "state_summary": state_summary,
            "remaining_work": remaining_work,
            "notes": notes,
        }
        task.checkpoints = [*task.checkpoints, checkpoint]
        await self.session.flush()

        return task

    async def add_commit(
        self,
        task_id: UUID,
        hash: str,
        message: str,
        agent_id: UUID | None = None,
    ) -> TaskTable | None:
        """Link a commit to a task."""
        task = await self.get(task_id)
        if not task:
            return None

        commit = {
            "hash": hash,
            "message": message,
            "timestamp": datetime.now(UTC).isoformat(),
            "author_agent_id": str(agent_id) if agent_id else None,
        }
        task.commits = [*task.commits, commit]
        await self.session.flush()

        return task

    # =========================================================================
    # QUERIES
    # =========================================================================

    async def list_all(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TaskTable]:
        """List all tasks with pagination."""
        result = await self.session.execute(
            select(TaskTable)
            .order_by(TaskTable.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def list_by_team(
        self,
        team: Team,
        status: TaskStatus | None = None,
        limit: int = 100,
    ) -> list[TaskTable]:
        """List tasks for a specific team."""
        query = select(TaskTable).where(TaskTable.team == team)

        if status:
            query = query.where(TaskTable.status == status)

        query = query.order_by(TaskTable.priority, TaskTable.created_at.desc())
        query = query.limit(limit)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def list_by_assignee(
        self,
        agent_id: UUID,
        status: TaskStatus | None = None,
    ) -> list[TaskTable]:
        """List tasks assigned to an agent."""
        query = select(TaskTable).where(TaskTable.assigned_to == agent_id)

        if status:
            query = query.where(TaskTable.status == status)

        query = query.order_by(TaskTable.priority, TaskTable.created_at.desc())

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def list_by_team_or_assignee(
        self,
        team: Team | None = None,
        agent_id: UUID | None = None,
        status: TaskStatus | None = None,
    ) -> list[TaskTable]:
        """
        List tasks by team OR assignee.

        Useful for finding tasks an agent could work on (their assigned tasks
        or unassigned tasks in their team).
        """
        conditions = []

        if team:
            conditions.append(
                and_(TaskTable.team == team, TaskTable.assigned_to.is_(None))
            )
        if agent_id:
            conditions.append(TaskTable.assigned_to == agent_id)

        if not conditions:
            return []

        query = select(TaskTable).where(or_(*conditions))

        if status:
            query = query.where(TaskTable.status == status)

        query = query.order_by(TaskTable.priority, TaskTable.created_at.desc())

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def list_by_status(
        self,
        status: TaskStatus,
        team: Team | None = None,
    ) -> list[TaskTable]:
        """List tasks with a specific status."""
        query = select(TaskTable).where(TaskTable.status == status)

        if team:
            query = query.where(TaskTable.team == team)

        query = query.order_by(TaskTable.priority, TaskTable.created_at.desc())

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def list_pending(self, team: Team | None = None) -> list[TaskTable]:
        """List pending tasks (available to claim)."""
        return await self.list_by_status(TaskStatus.PENDING, team)

    async def list_blocked(self, team: Team | None = None) -> list[TaskTable]:
        """List blocked tasks."""
        return await self.list_by_status(TaskStatus.BLOCKED, team)

    async def list_awaiting_qa(self, team: Team | None = None) -> list[TaskTable]:
        """List tasks awaiting QA review."""
        return await self.list_by_status(TaskStatus.AWAITING_QA, team)

    async def list_awaiting_docs(self, team: Team | None = None) -> list[TaskTable]:
        """List tasks awaiting documentation."""
        return await self.list_by_status(TaskStatus.AWAITING_DOCUMENTATION, team)

    async def get_subtasks(self, parent_task_id: UUID) -> list[TaskTable]:
        """Get all subtasks of a parent task."""
        result = await self.session.execute(
            select(TaskTable)
            .where(TaskTable.parent_task_id == parent_task_id)
            .order_by(TaskTable.created_at)
        )
        return list(result.scalars().all())

    # =========================================================================
    # STATISTICS
    # =========================================================================

    async def count_by_status(self, team: Team | None = None) -> dict[str, int]:
        """Count tasks by status."""
        query = select(
            TaskTable.status,
            func.count(TaskTable.id),
        ).group_by(TaskTable.status)

        if team:
            query = query.where(TaskTable.team == team)

        result = await self.session.execute(query)
        return {row[0].value: row[1] for row in result.all()}

    async def count_by_team(self) -> dict[str, int]:
        """Count tasks by team."""
        result = await self.session.execute(
            select(
                TaskTable.team,
                func.count(TaskTable.id),
            ).group_by(TaskTable.team)
        )
        return {row[0].value: row[1] for row in result.all()}

    async def get_active_count(self, agent_id: UUID) -> int:
        """Get count of active tasks for an agent."""
        result = await self.session.execute(
            select(func.count(TaskTable.id)).where(
                and_(
                    TaskTable.assigned_to == agent_id,
                    TaskTable.status.in_(
                        [
                            TaskStatus.CLAIMED,
                            TaskStatus.IN_PROGRESS,
                            TaskStatus.VERIFYING,
                        ]
                    ),
                )
            )
        )
        return result.scalar() or 0


# =============================================================================
# SERVICE FACTORY
# =============================================================================


def get_task_service(session: AsyncSession) -> TaskService:
    """Get a TaskService instance."""
    return TaskService(session)
