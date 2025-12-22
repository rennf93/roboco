"""
Task Service

Provides CRUD operations and lifecycle management for tasks.
Handles status transitions, assignments, and queries.
"""

from datetime import UTC, datetime
from typing import Any, ClassVar, cast
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.db.tables import AgentTable, SessionTaskTable, TaskTable
from roboco.enforcement import (
    TaskOwnershipError,
    validate_task_ownership,
)
from roboco.models.base import TaskStatus, Team
from roboco.models.task import TaskCreateRequest
from roboco.services.base import BaseService

# UUID format constants for validation
_UUID_LENGTH = 36  # Standard UUID string length
_UUID_HYPHEN_COUNT = 4  # Number of hyphens in a UUID


def _get_valid_claim_statuses(
    agent: AgentTable | None,
    allow_reassign: bool,
) -> set[TaskStatus]:
    """
    Get valid task statuses an agent can claim based on their role.

    Role-based claiming:
    - QA: can only claim AWAITING_QA tasks
    - Documenter: can only claim AWAITING_DOCUMENTATION tasks
    - Developers/PMs: can claim PENDING (and CLAIMED if allow_reassign)

    Args:
        agent: The agent attempting to claim
        allow_reassign: Whether to allow claiming already-claimed tasks

    Returns:
        Set of valid TaskStatus values the agent can claim
    """
    if not agent or not agent.role:
        # No role info - default to pending tasks only
        statuses = {TaskStatus.PENDING}
        if allow_reassign:
            statuses.add(TaskStatus.CLAIMED)
        return statuses

    role = agent.role.value if hasattr(agent.role, "value") else str(agent.role)

    if role == "qa":
        return {TaskStatus.AWAITING_QA}
    elif role == "documenter":
        return {TaskStatus.AWAITING_DOCUMENTATION}
    else:
        # Developer, PM, and other roles
        statuses = {TaskStatus.PENDING}
        if allow_reassign:
            statuses.add(TaskStatus.CLAIMED)
        return statuses


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


class TaskService(BaseService):
    """
    Service for managing tasks.

    Provides:
    - CRUD operations
    - Status transitions with validation
    - Assignment and claiming
    - Queries by team, status, assignee
    - Dependency management
    """

    service_name: ClassVar[str] = "task"

    # =========================================================================
    # CRUD OPERATIONS
    # =========================================================================

    async def create(self, req: TaskCreateRequest) -> TaskTable:
        """
        Create a new task.

        Tasks are created with BACKLOG status by default. PM must:
        1. Create a session for the task
        2. Call activate() to transition to PENDING

        This ensures every task has a session before work begins.
        """
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
            status=TaskStatus.BACKLOG,
        )
        self.session.add(task)
        await self.session.flush()

        # Inherit parent task's primary session for subtasks
        if req.parent_task_id:
            await self._inherit_parent_session(
                task_id=cast("UUID", task.id),
                parent_task_id=req.parent_task_id,
                created_by=req.created_by,
            )

        self.log.info(
            "Task created",
            task_id=str(task.id),
            title=req.title,
            team=req.team if isinstance(req.team, str) else req.team.value,
        )
        return task

    async def _inherit_parent_session(
        self,
        task_id: UUID,
        parent_task_id: UUID,
        created_by: UUID,
    ) -> SessionTaskTable | None:
        """
        Inherit the parent task's primary session for a subtask.

        When a subtask is created, it automatically joins the parent task's
        primary discussion session (if one exists). This enables context
        continuity across the task hierarchy.

        Args:
            task_id: The new subtask ID
            parent_task_id: The parent task ID
            created_by: PM who created the subtask

        Returns:
            Created link if parent had a primary session, None otherwise
        """
        # Find parent's primary session
        result = await self.session.execute(
            select(SessionTaskTable).where(
                SessionTaskTable.task_id == parent_task_id,
                SessionTaskTable.is_primary.is_(True),
            )
        )
        parent_link = result.scalar_one_or_none()

        if not parent_link:
            return None

        # Create link for subtask (not primary - parent owns the primary)
        link = SessionTaskTable(
            session_id=parent_link.session_id,
            task_id=task_id,
            is_primary=False,  # Subtasks don't become primary
            relationship_type=parent_link.relationship_type,
            added_by=created_by,
        )

        self.session.add(link)
        await self.session.flush()

        self.log.info(
            "Subtask inherited parent session",
            task_id=str(task_id),
            parent_task_id=str(parent_task_id),
            session_id=str(parent_link.session_id),
        )
        return link

    async def activate(self, task_id: UUID) -> TaskTable:
        """
        Activate a task from BACKLOG to PENDING status.

        This is a PM-only operation that transitions a task from setup
        phase to ready-for-work phase. The orchestrator will then spawn
        agents to work on it.

        REQUIRES: Task must have at least one linked session.

        Args:
            task_id: The task to activate

        Returns:
            The activated task

        Raises:
            ValueError: If task not found, not in BACKLOG, or has no session
        """
        task = await self.get(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        if task.status != TaskStatus.BACKLOG:
            raise ValueError(
                f"Task {task_id} is not in BACKLOG status (current: {task.status})"
            )

        # Check if task has at least one linked session
        result = await self.session.execute(
            select(SessionTaskTable).where(SessionTaskTable.task_id == task_id).limit(1)
        )
        session_link = result.scalar_one_or_none()

        if not session_link:
            raise ValueError(
                f"Task {task_id} has no linked session. "
                "Create a session with roboco_session_create_for_tasks "
                "before activating."
            )

        # Transition to PENDING
        task.status = TaskStatus.PENDING
        await self.session.flush()

        self.log.info(
            "Task activated",
            task_id=str(task_id),
            session_id=str(session_link.session_id),
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

        self.log.info(
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

        self.log.info("Task deleted", task_id=str(task_id))
        return True

    # =========================================================================
    # STATUS TRANSITIONS
    # =========================================================================

    def _validate_claim_status(
        self,
        task: TaskTable,
        agent: AgentTable | None,
        valid_statuses: set[TaskStatus],
    ) -> str | None:
        """
        Validate task status for claiming.

        Returns:
            Error message if invalid, None if valid
        """
        role_specific = {TaskStatus.AWAITING_QA, TaskStatus.AWAITING_DOCUMENTATION}
        if not agent and task.status in role_specific:
            return "role required for this status"
        if task.status not in valid_statuses:
            return "invalid status for role"
        return None

    def _validate_claim_team(
        self, task: TaskTable, agent: AgentTable | None
    ) -> str | None:
        """
        Validate agent belongs to task's team.

        Returns:
            Error message if invalid, None if valid
        """
        if agent and task.team and agent.team != task.team:
            return "agent not in task's team"
        return None

    def _set_original_developer_context(
        self, task: TaskTable, agent: AgentTable | None
    ) -> None:
        """Set original developer context for QA/Documenter claims."""
        if not agent or not agent.role:
            return
        role = agent.role.value if hasattr(agent.role, "value") else str(agent.role)
        if role not in ("qa", "documenter"):
            return
        existing_context = task.quick_context or ""
        if "original_developer:" in existing_context:
            return
        if task.assigned_to:
            task.quick_context = f"original_developer:{task.assigned_to}"

    async def claim(
        self, task_id: UUID, agent_id: UUID, allow_reassign: bool = False
    ) -> TaskTable | None:
        """
        Claim a task for an agent.

        Role-based claiming:
        - Developers/PMs: can claim PENDING tasks
        - QA: can claim AWAITING_QA tasks
        - Documenters: can claim AWAITING_DOCUMENTATION tasks
        """
        task = await self.get(task_id)
        if not task:
            return None

        # Get agent for role-based validation
        agent_result = await self.session.execute(
            select(AgentTable).where(AgentTable.id == agent_id)
        )
        agent = agent_result.scalar_one_or_none()
        valid_statuses = _get_valid_claim_statuses(agent, allow_reassign)

        # Validate status
        if error := self._validate_claim_status(task, agent, valid_statuses):
            self.log.warning(f"Cannot claim task - {error}", task_id=str(task_id))
            return None

        # Validate team
        if error := self._validate_claim_team(task, agent):
            self.log.warning(f"Cannot claim task - {error}", task_id=str(task_id))
            return None

        # Set context for QA/Documenter claims
        self._set_original_developer_context(task, agent)

        # Update assignment
        task.assigned_to = cast("Any", agent_id)
        task.claimed_at = datetime.now(UTC)
        if task.status == TaskStatus.PENDING:
            task.status = TaskStatus.CLAIMED

        await self.session.flush()
        self.log.info("Task claimed", task_id=str(task_id), agent_id=str(agent_id))
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
                self.log.warning(
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
            self.log.warning(
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

        self.log.info("Task started", task_id=str(task_id))
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

        self.log.info(
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

        self.log.info(
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

        self.log.info("Task unblocked", task_id=str(task_id))
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

        self.log.info("Task paused", task_id=str(task_id))
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

        self.log.info("Task resumed", task_id=str(task_id))
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

        self.log.info("Task submitted for verification", task_id=str(task_id))
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

        self.log.info(
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

        self.log.info("Task passed QA", task_id=str(task_id))
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
            self.log.info(
                "Task reassigned to original developer for revision",
                task_id=str(task_id),
                original_developer=original_dev,
            )
        else:
            # If no original developer found, unassign so it can be claimed
            task.assigned_to = None
            self.log.warning(
                "No original developer found, task unassigned",
                task_id=str(task_id),
            )

        await self.session.flush()

        self.log.info("Task failed QA", task_id=str(task_id))
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
            self.log.warning(
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

        self.log.info(
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
            self.log.warning(
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

        self.log.info("Task completed", task_id=str(task_id))
        return task

    async def cancel(self, task_id: UUID) -> TaskTable | None:
        """Cancel a task."""
        task = await self.get(task_id)
        if not task:
            return None

        task.status = TaskStatus.CANCELLED
        await self.session.flush()

        self.log.info("Task cancelled", task_id=str(task_id))
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
                self.log.info(
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
