"""
Task Service

Provides CRUD operations and lifecycle management for tasks.
Handles status transitions, assignments, and queries.
"""

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import structlog
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.db.tables import AgentTable, TaskTable
from roboco.enforcement import (
    TaskOwnershipError,
    validate_task_ownership,
)
from roboco.models.base import TaskStatus, Team
from roboco.models.task import TaskCreateRequest

logger = structlog.get_logger()

# UUID format constants for validation
_UUID_LENGTH = 36  # Standard UUID string length
_UUID_HYPHEN_COUNT = 4  # Number of hyphens in a UUID


def extract_original_developer(quick_context: str | None) -> str | None:
    """
    Safely extract original developer ID from quick_context.

    The quick_context stores original developer as: "original_developer:{uuid}"
    This is used to prevent self-review (QA reviewing their own work).

    Args:
        quick_context: The task's quick_context field value

    Returns:
        UUID string of original developer, or None if not found/invalid
    """
    if not quick_context:
        return None

    prefix = "original_developer:"
    if not quick_context.startswith(prefix):
        return None

    try:
        dev_id = quick_context[len(prefix) :].strip()
        # Validate it looks like a UUID
        if len(dev_id) == _UUID_LENGTH and dev_id.count("-") == _UUID_HYPHEN_COUNT:
            return dev_id
        return None
    except Exception:
        return None


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
            team=req.team if isinstance(req.team, str) else req.team.value,
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

    async def claim(
        self, task_id: UUID, agent_id: UUID, allow_reassign: bool = False
    ) -> TaskTable | None:
        """
        Claim a task for an agent.

        Validates:
        - Task exists and is in a claimable status for the agent's role
        - Agent belongs to the same team as the task

        Role-based claiming:
        - Developers/PMs: can claim PENDING tasks
        - QA: can claim AWAITING_QA tasks
        - Documenters: can claim AWAITING_DOCUMENTATION tasks

        Args:
            task_id: Task to claim
            agent_id: Agent claiming the task
            allow_reassign: If True, allows reassigning CLAIMED tasks
        """
        task = await self.get(task_id)
        if not task:
            return None

        # Get agent to determine role-based valid statuses
        agent_result = await self.session.execute(
            select(AgentTable).where(AgentTable.id == agent_id)
        )
        agent = agent_result.scalar_one_or_none()

        # Role-based claiming: each role can only claim specific statuses
        # QA → awaiting_qa only, Documenter → awaiting_documentation only
        # Developers/PMs → pending (and claimed if allow_reassign)
        valid_statuses: set[TaskStatus] = set()

        if agent and agent.role:
            role = agent.role.value if hasattr(agent.role, "value") else str(agent.role)
            if role == "qa":
                # QA can ONLY claim awaiting_qa tasks
                valid_statuses.add(TaskStatus.AWAITING_QA)
            elif role == "documenter":
                # Documenter can ONLY claim awaiting_documentation tasks
                valid_statuses.add(TaskStatus.AWAITING_DOCUMENTATION)
            else:
                # Developer, PM, and other roles claim pending tasks
                valid_statuses.add(TaskStatus.PENDING)
                if allow_reassign:
                    valid_statuses.add(TaskStatus.CLAIMED)
        elif task.status in {TaskStatus.AWAITING_QA, TaskStatus.AWAITING_DOCUMENTATION}:
            # No role information - reject claims for role-specific statuses
            logger.warning(
                "Cannot claim task - role required for this status",
                task_id=str(task_id),
                task_status=task.status.value,
                agent_id=str(agent_id),
                has_agent=agent is not None,
                agent_role="none",
            )
            return None
        else:
            # No role info but task is pending - allow claim (fallback)
            valid_statuses.add(TaskStatus.PENDING)
            if allow_reassign:
                valid_statuses.add(TaskStatus.CLAIMED)

        if task.status not in valid_statuses:
            logger.warning(
                "Cannot claim task - invalid status for role",
                task_id=str(task_id),
                current_status=task.status.value,
                agent_role=agent.role.value if agent and agent.role else "unknown",
                valid_statuses=[s.value for s in valid_statuses],
            )
            return None

        # Validate agent belongs to the task's team (agent already fetched above)
        if agent and task.team and agent.team != task.team:
            logger.warning(
                "Cannot claim task - agent not in task's team",
                task_id=str(task_id),
                agent_team=agent.team.value if agent.team else None,
                task_team=task.team.value,
            )
            return None

        # For QA/Documenter claiming, ensure original_developer is set for
        # self-review checks. Primary storage is in submit_for_qa, but we set
        # here as fallback (e.g., if task was created directly in awaiting_qa)
        if agent and agent.role:
            role = agent.role.value if hasattr(agent.role, "value") else str(agent.role)
            if role in ("qa", "documenter"):
                # Only set if not already stored by submit_for_qa
                existing_context = task.quick_context or ""
                if "original_developer:" not in existing_context:
                    original_dev = str(task.assigned_to) if task.assigned_to else None
                    if original_dev:
                        task.quick_context = f"original_developer:{original_dev}"

        # All roles: update assigned_to and claimed_at
        task.assigned_to = cast("Any", agent_id)
        task.claimed_at = datetime.now(UTC)

        # Only change status to CLAIMED for developers/PMs (pending tasks)
        # QA/Documenter keep the awaiting_qa/awaiting_documentation status
        if task.status == TaskStatus.PENDING:
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

        # Valid statuses to start/resume work:
        # - CLAIMED: Developer just claimed a pending task
        # - PAUSED: Developer resuming paused work
        # - NEEDS_REVISION: Developer resuming after QA rejection
        valid_start_statuses = (
            TaskStatus.CLAIMED,
            TaskStatus.PAUSED,
            TaskStatus.NEEDS_REVISION,
        )
        if task.status not in valid_start_statuses:
            logger.warning(
                "Cannot start task - invalid status",
                task_id=str(task_id),
                current_status=task.status.value,
            )
            return None

        # Only update started_at if this is the first time starting
        if task.started_at is None:
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

    async def soft_block(
        self,
        task_id: UUID,
        reason: str,
        blocker_type: str,
        what_needed: str,
    ) -> TaskTable | None:
        """
        Block a task due to an external factor (not a task dependency).

        Unlike `block()` which requires another task as the blocker,
        this method handles soft blocks like:
        - External dependencies (waiting for API access, credentials)
        - Questions that need PM/stakeholder input
        - Technical blockers (infrastructure issues)

        Args:
            task_id: The task to block
            reason: Why the task is blocked
            blocker_type: Type of blocker (external/internal/question/dependency)
            what_needed: What is needed to unblock

        Returns:
            The blocked task, or None if blocking not allowed
        """
        task = await self.get(task_id)
        if not task:
            return None

        if task.status != TaskStatus.IN_PROGRESS:
            return None

        # Build blocker note for dev_notes
        blocker_note = (
            f"[BLOCKED - {blocker_type.upper()}]\n"
            f"Reason: {reason}\n"
            f"What's needed: {what_needed}"
        )
        existing_notes = task.dev_notes or ""
        if existing_notes:
            task.dev_notes = f"{existing_notes}\n\n{blocker_note}"
        else:
            task.dev_notes = blocker_note

        task.status = TaskStatus.BLOCKED
        await self.session.flush()

        logger.info(
            "Task soft-blocked",
            task_id=str(task_id),
            blocker_type=blocker_type,
            reason=reason,
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

        # Store original developer BEFORE QA claims - this is the authoritative record
        # for self-review prevention. Storing here ensures we capture the developer
        # even if the task is reassigned before QA claims it.
        original_dev = str(task.assigned_to) if task.assigned_to else None
        if original_dev:
            task.quick_context = f"original_developer:{original_dev}"

        task.self_verified = True
        task.status = TaskStatus.AWAITING_QA
        await self.session.flush()

        logger.info(
            "Task submitted for QA",
            task_id=str(task_id),
            original_developer=original_dev,
        )
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
        """
        Mark task as failed QA and reassign to original developer.

        When QA fails a task, it goes back to the original developer for revision.
        The original developer is extracted from quick_context which stores
        "original_developer:{uuid}" when the task was submitted to QA.
        """
        task = await self.get(task_id)
        if not task:
            return None

        if task.status != TaskStatus.AWAITING_QA:
            return None

        task.qa_notes = notes
        task.qa_verified = False
        task.status = TaskStatus.NEEDS_REVISION

        # Reassign to original developer so they can work on revisions
        original_dev = extract_original_developer(task.quick_context)
        if original_dev:
            task.assigned_to = cast("Any", UUID(original_dev))
            logger.info(
                "Task reassigned to original developer for revision",
                task_id=str(task_id),
                original_developer=original_dev,
            )
        else:
            # If no original developer found, unassign so it can be claimed
            task.assigned_to = None
            logger.warning(
                "No original developer found, task unassigned",
                task_id=str(task_id),
            )

        await self.session.flush()

        logger.info("Task failed QA", task_id=str(task_id))
        return task

    async def docs_complete(
        self,
        task_id: UUID,
        doc_notes: str | None = None,
    ) -> TaskTable | None:
        """
        Mark documentation as complete (documenter only).

        Transitions task from AWAITING_DOCUMENTATION to AWAITING_PM_REVIEW.
        The Cell PM will then review and call complete() to finish the task.

        Args:
            task_id: The task to mark docs complete
            doc_notes: Optional notes about the documentation

        Returns:
            The updated task or None if not allowed
        """
        task = await self.get(task_id)
        if not task:
            return None

        if task.status != TaskStatus.AWAITING_DOCUMENTATION:
            logger.warning(
                "Cannot mark docs complete - not awaiting documentation",
                task_id=str(task_id),
                current_status=task.status.value,
            )
            return None

        # Store doc notes in quick_context (no dedicated field for doc_notes)
        if doc_notes:
            existing_context = task.quick_context or ""
            doc_note_entry = f"doc_notes:{doc_notes}"
            task.quick_context = (
                f"{existing_context}\n{doc_note_entry}".strip()
                if existing_context
                else doc_note_entry
            )

        task.status = TaskStatus.AWAITING_PM_REVIEW

        # Reassign to the cell PM for final review
        # Store documenter in quick_context for reference
        if task.assigned_to:
            existing_context = task.quick_context or ""
            if "documenter:" not in existing_context:
                doc_context = f"documenter:{task.assigned_to}"
                task.quick_context = (
                    f"{existing_context}\n{doc_context}".strip()
                    if existing_context
                    else doc_context
                )

        # Note: We don't auto-assign to PM here - PM will pick it up via scan
        # The task remains assigned to documenter until PM claims it
        await self.session.flush()

        logger.info(
            "Documentation complete, awaiting PM review",
            task_id=str(task_id),
        )
        return task

    async def complete(
        self,
        task_id: UUID,
    ) -> TaskTable | None:
        """
        Mark task as completed (PM only).

        Only PMs can complete tasks, and only from AWAITING_PM_REVIEW status.
        This ensures the full workflow: Dev → QA → Documenter → PM.

        Args:
            task_id: The task to complete

        Returns:
            The completed task or None if completion not allowed
        """
        task = await self.get(task_id)
        if not task:
            return None

        # Only allow completion from AWAITING_PM_REVIEW
        # This enforces the workflow: documenter calls docs_complete, PM calls complete
        if task.status != TaskStatus.AWAITING_PM_REVIEW:
            logger.warning(
                "Cannot complete task - must be in awaiting_pm_review status",
                task_id=str(task_id),
                current_status=task.status.value,
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
