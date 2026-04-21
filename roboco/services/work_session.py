"""
WorkSession Service

Manages git working sessions that link agents to tasks on projects.
WorkSessions track branch management, commits, and PR lifecycle.
"""

from datetime import UTC, datetime
from typing import Any, ClassVar, cast
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.db.tables import ProjectTable, TaskTable, WorkSessionTable
from roboco.models.work_session import (
    WorkSessionCreate,
    WorkSessionStatus,
    WorkSessionUpdate,
)
from roboco.services.base import (
    BaseService,
    ConflictError,
    NotFoundError,
    ValidationError,
)

# Branch ID truncation length for readability
_BRANCH_ID_LENGTH = 8


class WorkSessionService(BaseService):
    """
    Service for managing git work sessions.

    Provides:
    - Session creation when developer claims a task
    - Branch name generation following naming conventions
    - Commit and file tracking
    - PR lifecycle management
    - Session completion and abandonment
    """

    service_name: ClassVar[str] = "work_session"

    # =========================================================================
    # BRANCH NAMING
    # =========================================================================

    @staticmethod
    def generate_branch_name(
        reason: str,
        team: str,
        task_id: str,
        subtask_id: str | None = None,
    ) -> str:
        """
        Generate a branch name following the convention.

        Pattern: reason/team/task-id[/subtask-id]
        Examples:
            - feature/backend/ABC123
            - feature/backend/ABC123/XYZ789

        Args:
            reason: Branch type (feature, bug, chore, docs, hotfix)
            team: Cell/team name (backend, frontend, ux_ui)
            task_id: Parent task ID (truncated to 8 chars)
            subtask_id: Optional subtask ID (truncated to 8 chars)

        Returns:
            Generated branch name
        """
        # Truncate IDs for readability
        short_task = (
            task_id[:_BRANCH_ID_LENGTH] if len(task_id) > _BRANCH_ID_LENGTH else task_id
        )
        branch = f"{reason}/{team}/{short_task}"

        if subtask_id:
            short_subtask = (
                subtask_id[:_BRANCH_ID_LENGTH]
                if len(subtask_id) > _BRANCH_ID_LENGTH
                else subtask_id
            )
            branch = f"{branch}/{short_subtask}"

        return branch

    # =========================================================================
    # CRUD OPERATIONS
    # =========================================================================

    async def create(
        self,
        data: WorkSessionCreate,
    ) -> WorkSessionTable:
        """
        Create a new work session.

        Called when a developer claims a task.

        Args:
            data: Work session creation data

        Returns:
            The created work session

        Raises:
            ConflictError: If agent already has active session on this task
            ValidationError: If project or task doesn't exist
        """
        # Validate project exists
        project_result = await self.session.execute(
            select(ProjectTable).where(ProjectTable.id == data.project_id)
        )
        project = project_result.scalar_one_or_none()
        if not project:
            raise ValidationError(
                f"Project {data.project_id} not found",
                field="project_id",
            )

        # Validate task exists
        task_result = await self.session.execute(
            select(TaskTable).where(TaskTable.id == data.task_id)
        )
        task = task_result.scalar_one_or_none()
        if not task:
            raise ValidationError(
                f"Task {data.task_id} not found",
                field="task_id",
            )

        # Check for existing active session on this task by this agent
        existing = await self.get_active_for_task_and_agent(
            task_id=data.task_id,
            agent_id=data.agent_id,
        )
        if existing:
            raise ConflictError(
                f"Agent already has active session on task {data.task_id}",
                resource_type="work_session",
            )

        work_session = WorkSessionTable(
            project_id=data.project_id,
            task_id=data.task_id,
            agent_id=data.agent_id,
            branch_name=data.branch_name,
            base_branch=data.base_branch,
            target_branch=data.target_branch,
            status=WorkSessionStatus.ACTIVE,
        )

        self.session.add(work_session)
        await self.session.flush()

        self.log.info(
            "Work session created",
            session_id=str(work_session.id),
            project_id=str(data.project_id),
            task_id=str(data.task_id),
            branch=data.branch_name,
        )
        return work_session

    async def get(self, session_id: UUID) -> WorkSessionTable | None:
        """Get a work session by ID."""
        result = await self.session.execute(
            select(WorkSessionTable).where(WorkSessionTable.id == session_id)
        )
        return result.scalar_one_or_none()

    async def get_or_raise(self, session_id: UUID) -> WorkSessionTable:
        """Get a work session by ID or raise NotFoundError."""
        work_session = await self.get(session_id)
        if not work_session:
            raise NotFoundError("WorkSession", str(session_id))
        return work_session

    async def update(
        self,
        session_id: UUID,
        data: WorkSessionUpdate,
    ) -> WorkSessionTable | None:
        """
        Update a work session.

        Args:
            session_id: Session to update
            data: Update data (only non-None fields are applied)

        Returns:
            The updated session or None if not found
        """
        work_session = await self.get(session_id)
        if not work_session:
            return None

        # Apply updates for non-None fields
        update_data = data.model_dump(exclude_unset=True, exclude_none=True)
        for key, value in update_data.items():
            if hasattr(work_session, key):
                setattr(work_session, key, value)

        await self.session.flush()

        self.log.info(
            "Work session updated",
            session_id=str(session_id),
            updates=list(update_data.keys()),
        )
        return work_session

    # =========================================================================
    # QUERIES
    # =========================================================================

    async def get_active_for_task(
        self,
        task_id: UUID,
    ) -> WorkSessionTable | None:
        """
        Get the active work session for a task.

        Args:
            task_id: Task to find session for

        Returns:
            Active work session or None
        """
        result = await self.session.execute(
            select(WorkSessionTable).where(
                and_(
                    WorkSessionTable.task_id == task_id,
                    WorkSessionTable.status == WorkSessionStatus.ACTIVE,
                )
            )
        )
        return result.scalar_one_or_none()

    async def get_active_for_task_and_agent(
        self,
        task_id: UUID,
        agent_id: UUID,
    ) -> WorkSessionTable | None:
        """
        Get active work session for a task by a specific agent.

        Args:
            task_id: Task ID
            agent_id: Agent ID

        Returns:
            Active work session or None
        """
        result = await self.session.execute(
            select(WorkSessionTable).where(
                and_(
                    WorkSessionTable.task_id == task_id,
                    WorkSessionTable.agent_id == agent_id,
                    WorkSessionTable.status == WorkSessionStatus.ACTIVE,
                )
            )
        )
        return result.scalar_one_or_none()

    async def list_by_agent(
        self,
        agent_id: UUID,
        status: WorkSessionStatus | None = None,
    ) -> list[WorkSessionTable]:
        """
        List work sessions for an agent.

        Args:
            agent_id: Agent to list sessions for
            status: Optional status filter

        Returns:
            List of work sessions
        """
        query = select(WorkSessionTable).where(WorkSessionTable.agent_id == agent_id)

        if status:
            query = query.where(WorkSessionTable.status == status)

        query = query.order_by(WorkSessionTable.started_at.desc())

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def list_by_project(
        self,
        project_id: UUID,
        status: WorkSessionStatus | None = None,
    ) -> list[WorkSessionTable]:
        """
        List work sessions for a project.

        Args:
            project_id: Project to list sessions for
            status: Optional status filter

        Returns:
            List of work sessions
        """
        query = select(WorkSessionTable).where(
            WorkSessionTable.project_id == project_id
        )

        if status:
            query = query.where(WorkSessionTable.status == status)

        query = query.order_by(WorkSessionTable.started_at.desc())

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def list_active_sessions(
        self,
        project_id: UUID | None = None,
    ) -> list[WorkSessionTable]:
        """
        List all active work sessions.

        Args:
            project_id: Optional project filter

        Returns:
            List of active work sessions
        """
        query = select(WorkSessionTable).where(
            WorkSessionTable.status == WorkSessionStatus.ACTIVE
        )

        if project_id:
            query = query.where(WorkSessionTable.project_id == project_id)

        query = query.order_by(WorkSessionTable.started_at.desc())

        result = await self.session.execute(query)
        return list(result.scalars().all())

    # =========================================================================
    # COMMIT TRACKING
    # =========================================================================

    async def add_commit(
        self,
        session_id: UUID,
        commit_sha: str,
    ) -> WorkSessionTable | None:
        """
        Add a commit to the work session.

        Args:
            session_id: Session to update
            commit_sha: Git commit SHA

        Returns:
            The updated session or None if not found
        """
        work_session = await self.get(session_id)
        if not work_session:
            return None

        if commit_sha not in work_session.commits:
            work_session.commits = [*work_session.commits, commit_sha]
            await self.session.flush()

            self.log.debug(
                "Commit added to session",
                session_id=str(session_id),
                commit_sha=commit_sha[:8],
            )

        return work_session

    async def add_files_modified(
        self,
        session_id: UUID,
        file_paths: list[str],
    ) -> WorkSessionTable | None:
        """
        Add modified files to the work session.

        Args:
            session_id: Session to update
            file_paths: List of file paths modified

        Returns:
            The updated session or None if not found
        """
        work_session = await self.get(session_id)
        if not work_session:
            return None

        # Add new files (deduplicate)
        existing_files = set(work_session.files_modified)
        new_files = [f for f in file_paths if f not in existing_files]

        if new_files:
            work_session.files_modified = [*work_session.files_modified, *new_files]
            await self.session.flush()

        return work_session

    # =========================================================================
    # PR LIFECYCLE
    # =========================================================================

    async def create_pr(
        self,
        session_id: UUID,
        pr_number: int,
        pr_url: str,
    ) -> WorkSessionTable | None:
        """
        Record PR creation for the work session.

        Called when developer creates a PR for their branch.

        Args:
            session_id: Session to update
            pr_number: GitHub/GitLab PR number
            pr_url: Full URL to the PR

        Returns:
            The updated session or None if not found
        """
        work_session = await self.get(session_id)
        if not work_session:
            return None

        work_session.pr_number = pr_number
        work_session.pr_url = pr_url
        work_session.pr_status = "open"
        work_session.pr_created_at = datetime.now(UTC)
        await self.session.flush()

        self.log.info(
            "PR created for session",
            session_id=str(session_id),
            pr_number=pr_number,
            pr_url=pr_url,
        )
        return work_session

    async def update_pr_status(
        self,
        session_id: UUID,
        pr_status: str,
    ) -> WorkSessionTable | None:
        """
        Update the PR status.

        Args:
            session_id: Session to update
            pr_status: New PR status (open, merged, closed)

        Returns:
            The updated session or None if not found
        """
        work_session = await self.get(session_id)
        if not work_session:
            return None

        work_session.pr_status = pr_status
        await self.session.flush()

        return work_session

    async def merge_pr(
        self,
        session_id: UUID,
        merged_by: UUID,
    ) -> WorkSessionTable | None:
        """
        Record PR merge and complete the session.

        Called when the PR is merged (by PM approval).

        Args:
            session_id: Session to update
            merged_by: Agent who approved/merged the PR

        Returns:
            The updated session or None if not found
        """
        work_session = await self.get(session_id)
        if not work_session:
            return None

        work_session.pr_status = "merged"
        work_session.pr_merged_at = datetime.now(UTC)
        work_session.merged_by = cast("Any", merged_by)
        work_session.status = WorkSessionStatus.COMPLETED
        work_session.ended_at = datetime.now(UTC)
        await self.session.flush()

        self.log.info(
            "PR merged, session completed",
            session_id=str(session_id),
            pr_number=work_session.pr_number,
            merged_by=str(merged_by),
        )
        return work_session

    # =========================================================================
    # SESSION LIFECYCLE
    # =========================================================================

    async def complete(
        self,
        session_id: UUID,
    ) -> WorkSessionTable | None:
        """
        Mark the session as completed.

        Used when work is merged successfully.

        Args:
            session_id: Session to complete

        Returns:
            The updated session or None if not found
        """
        work_session = await self.get(session_id)
        if not work_session:
            return None

        if work_session.status != WorkSessionStatus.ACTIVE:
            self.log.warning(
                "Cannot complete non-active session",
                session_id=str(session_id),
                current_status=work_session.status.value,
            )
            return None

        work_session.status = WorkSessionStatus.COMPLETED
        work_session.ended_at = datetime.now(UTC)
        await self.session.flush()

        self.log.info(
            "Work session completed",
            session_id=str(session_id),
            branch=work_session.branch_name,
        )
        return work_session

    async def abandon(
        self,
        session_id: UUID,
        reason: str | None = None,
    ) -> WorkSessionTable | None:
        """
        Abandon/cancel the work session.

        Used when work is discarded (e.g., task cancelled).

        Args:
            session_id: Session to abandon
            reason: Optional reason for abandonment

        Returns:
            The updated session or None if not found
        """
        work_session = await self.get(session_id)
        if not work_session:
            return None

        if work_session.status != WorkSessionStatus.ACTIVE:
            self.log.warning(
                "Cannot abandon non-active session",
                session_id=str(session_id),
                current_status=work_session.status.value,
            )
            return None

        work_session.status = WorkSessionStatus.ABANDONED
        work_session.ended_at = datetime.now(UTC)
        await self.session.flush()

        self.log.info(
            "Work session abandoned",
            session_id=str(session_id),
            branch=work_session.branch_name,
            reason=reason,
        )
        return work_session

    async def close(
        self,
        session_id: UUID,
        reason: str | None = None,
    ) -> WorkSessionTable | None:
        """Close the work session on successful task completion.

        Sets status=COMPLETED so reporting can distinguish merged-
        successfully work from abandoned work. Idempotent: if the
        session is already COMPLETED or ABANDONED, returns without
        changing state.
        """
        work_session = await self.get(session_id)
        if not work_session:
            return None

        if work_session.status != WorkSessionStatus.ACTIVE:
            # Already terminal — nothing to do. Not a warning because the
            # caller may legitimately double-call on retry/idempotency.
            return work_session

        work_session.status = WorkSessionStatus.COMPLETED
        work_session.ended_at = datetime.now(UTC)
        await self.session.flush()

        self.log.info(
            "Work session closed",
            session_id=str(session_id),
            branch=work_session.branch_name,
            reason=reason,
        )
        return work_session


# =============================================================================
# SERVICE FACTORY
# =============================================================================


def get_work_session_service(session: AsyncSession) -> WorkSessionService:
    """Get a WorkSessionService instance."""
    return WorkSessionService(session)
