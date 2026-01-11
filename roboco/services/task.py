"""
Task Service

Provides CRUD operations and lifecycle management for tasks.
Handles status transitions, assignments, and queries.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any, ClassVar, cast
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.db.tables import (
    AgentTable,
    ProjectTable,
    SessionTaskTable,
    TaskTable,
    WorkSessionTable,
)
from roboco.enforcement import (
    GitContext,
    TaskOwnershipError,
    validate_git_requirements,
    validate_task_ownership,
    validate_task_transition,
)
from roboco.events import Event, EventType, get_event_bus
from roboco.models.base import AgentRole, TaskStatus, Team
from roboco.models.task import TaskCreateRequest
from roboco.models.work_session import WorkSessionStatus
from roboco.services.base import BaseService
from roboco.utils.converters import require_uuid, to_python_uuid

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
        # QA can claim:
        # - PENDING: when PM assigns a QA task directly
        # - AWAITING_QA: normal workflow after dev verification
        statuses = {TaskStatus.PENDING, TaskStatus.AWAITING_QA}
        if allow_reassign:
            statuses.add(TaskStatus.CLAIMED)
        return statuses
    elif role == "documenter":
        # Documenters can claim:
        # - PENDING: when PM assigns a docs task directly
        # - AWAITING_DOCUMENTATION: normal workflow after QA passes
        statuses = {TaskStatus.PENDING, TaskStatus.AWAITING_DOCUMENTATION}
        if allow_reassign:
            statuses.add(TaskStatus.CLAIMED)
        return statuses
    elif role in ("cell_pm", "main_pm"):
        # PMs can claim:
        # - PENDING: standard task claiming
        # - AWAITING_PM_REVIEW: tasks submitted for PM approval
        statuses = {TaskStatus.PENDING, TaskStatus.AWAITING_PM_REVIEW}
        if allow_reassign:
            statuses.add(TaskStatus.CLAIMED)
        return statuses
    else:
        # Developer and other roles
        # NEEDS_REVISION for when task is reassigned after QA rejection
        statuses = {TaskStatus.PENDING, TaskStatus.NEEDS_REVISION}
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
    _background_tasks: ClassVar[set[asyncio.Task[None]]] = set()

    # =========================================================================
    # STATUS TRANSITION HELPER
    # =========================================================================

    def _validate_and_set_status(
        self,
        task: TaskTable,
        new_status: TaskStatus,
        agent_role: str | None = None,
    ) -> None:
        """
        Validate and set task status with lifecycle enforcement.

        This is the single point of truth for status changes. All transitions
        are validated against VALID_TRANSITIONS, ROLE_RESTRICTED_TRANSITIONS,
        and git requirements.

        Args:
            task: The task to update
            new_status: Target status
            agent_role: Optional role for role-restricted transitions

        Raises:
            TaskLifecycleError: If transition is invalid or role not permitted
            GitRequirementError: If git requirements not met
        """
        current = (
            task.status.value if isinstance(task.status, TaskStatus) else task.status
        )
        target = new_status.value if isinstance(new_status, TaskStatus) else new_status

        # Validate the transition (raises TaskLifecycleError if invalid)
        validate_task_transition(current, target, agent_role)

        # Validate git requirements (raises GitRequirementError if not met)
        if task.requires_git:
            git_ctx = GitContext(
                requires_git=True,
                docs_complete=bool(task.docs_complete),
                pr_created=bool(task.pr_created),
                pr_number=task.pr_number,
                branch_name=str(task.branch_name) if task.branch_name else None,
            )
            validate_git_requirements(current, target, git_ctx)

        # Apply the status change
        task.status = new_status
        self.log.info(
            "Task status transition",
            task_id=str(task.id),
            from_status=current,
            to_status=target,
            agent_role=agent_role,
        )

    # =========================================================================
    # CRUD OPERATIONS
    # =========================================================================

    async def create(self, req: TaskCreateRequest) -> TaskTable:
        """
        Create a new task.

        Default status is PENDING. PM can pass status=BACKLOG when creating
        subtasks that need session setup before activation.
        """
        task = TaskTable(
            title=req.title,
            description=req.description,
            acceptance_criteria=req.acceptance_criteria,
            team=req.team,
            created_by=req.created_by,
            assigned_to=req.assigned_to,
            priority=req.priority,
            parent_task_id=req.parent_task_id,
            target_date=req.target_date,
            estimated_complexity=req.estimated_complexity,
            nature=req.nature,
            status=req.status if req.status else TaskStatus.PENDING,
            sequence=req.sequence,  # Task ordering within siblings
            dependency_ids=req.dependency_ids,  # Task IDs that must complete first
            # Git configuration - CRITICAL: These must be passed through
            task_type=req.task_type,
            requires_git=req.requires_git,
            project_id=req.project_id,
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

        # ENFORCEMENT: Git tasks require project_id before activation
        if task.requires_git and not task.project_id:
            raise ValueError(
                f"Cannot activate task '{task.title}' - requires git but no project. "
                "Fix: (1) Re-create task with project_slug, OR "
                "(2) Set requires_git=False if git not needed."
            )

        # NOTE: Git branch is auto-created on claim, not required at activation

        # Transition to PENDING
        task.status = TaskStatus.PENDING
        await self.session.flush()

        self.log.info(
            "Task activated",
            task_id=str(task_id),
            session_id=str(session_link.session_id),
        )
        return task

    async def _ensure_branch_for_git_task(
        self,
        task: TaskTable,
        agent_id: UUID,
    ) -> str:
        """Auto-create hierarchical branch for git tasks. Raises on failure.

        Strategy:
        - If branch exists: return it
        - If no project: raise error
        - Create NEW branch (hierarchical name built by build_branch_name)
        - Branch created from parent's branch (or default if root)

        Raises:
            ValueError: If branch cannot be created (mandatory for git tasks)
        """
        if not task.requires_git:
            raise ValueError("Task does not require git")

        if task.branch_name:
            return str(task.branch_name)

        if not task.project_id:
            raise ValueError(
                "Git task requires project_id to create branch. "
                "Assign a project before claiming."
            )

        return await self._auto_create_branch(task, agent_id)

    async def _auto_create_branch(
        self,
        task: TaskTable,
        agent_id: UUID,
    ) -> str:
        """Create hierarchical branch for git task. Raises on failure.

        Branch naming (via build_branch_name):
        - Root: feature/team/ROOT_ID
        - Subtask: feature/team/ROOT_ID/SUB_ID
        - Sub-subtask: feature/team/ROOT_ID/SUB_ID/SUBSUB_ID

        Parent branch resolution:
        - Subtask: uses parent task's branch_name
        - Root: uses project's default branch (main/master)

        Raises:
            ValueError: If branch cannot be created
        """
        from roboco.api.schemas.git import GitCreateBranchRequest
        from roboco.services.git import get_git_service
        from roboco.services.project import get_project_service

        git_service = get_git_service(self.session)
        project_service = get_project_service(self.session)

        project = await project_service.get(UUID(str(task.project_id)))
        if not project:
            raise ValueError(f"Project {task.project_id} not found")

        parent_branch: str | None = None
        if task.parent_task_id:
            parent_task = await self.get(UUID(str(task.parent_task_id)))
            if parent_task and parent_task.branch_name:
                parent_branch = str(parent_task.branch_name)
            else:
                raise ValueError(
                    "Parent task must be claimed first. "
                    "Subtasks fork from parent branch (auto-created on claim)."
                )

        workspace = await git_service.get_workspace(project.slug, agent_id)

        project_cell = (
            project.assigned_cell.value
            if project.assigned_cell and hasattr(project.assigned_cell, "value")
            else str(project.assigned_cell)
            if project.assigned_cell
            else None
        )
        task_team = (
            task.team.value if task.team and hasattr(task.team, "value") else None
        )

        if project_cell == "fullstack":
            team = f"{project.slug}/{task_team or 'cross'}"
        elif project_cell:
            team = project_cell
        elif task_team:
            team = task_team
        else:
            team = "cross"

        request = GitCreateBranchRequest(
            task_id=str(task.id),
            project_slug=project.slug,
            branch_type="feature",
            agent_id=str(agent_id),
            parent_branch=parent_branch,
        )

        branch_name, _ = await git_service.create_branch(workspace, team, request)

        task.branch_name = branch_name
        await self.session.flush()

        self.log.info(
            "Auto-created hierarchical branch",
            task_id=str(task.id),
            branch_name=branch_name,
            parent_branch=parent_branch or "default",
        )
        return branch_name

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
        """Delete a task and all its descendants."""
        task = await self.get(task_id)
        if not task:
            return False

        # Delete all descendants first (children, grandchildren, etc.)
        # Process in reverse order to delete leaves before parents
        descendants = await self.get_all_descendants(task_id)
        descendants.reverse()  # Delete deepest children first

        for descendant in descendants:
            await self.session.delete(descendant)

        if descendants:
            self.log.info(
                "Cascaded delete to descendants",
                task_id=str(task_id),
                deleted_count=len(descendants),
            )

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

    # Management roles that can claim tasks from any team
    _MANAGEMENT_ROLES = frozenset(
        {"main_pm", "product_owner", "head_marketing", "auditor"}
    )

    def _validate_claim_team(
        self, task: TaskTable, agent: AgentTable | None
    ) -> str | None:
        """
        Validate agent belongs to task's team.

        Management roles (main_pm, product_owner, head_marketing, auditor)
        can claim tasks from any team.

        Returns:
            Error message if invalid, None if valid
        """
        if not agent or not task.team:
            return None

        # Get agent role as string
        agent_role = (
            agent.role.value if hasattr(agent.role, "value") else str(agent.role)
        )

        # Management roles can claim any task
        if agent_role in self._MANAGEMENT_ROLES:
            return None

        # Regular agents must match team
        if agent.team != task.team:
            return "agent not in task's team"
        return None

    def _validate_not_self_review(
        self, task: TaskTable, agent: AgentTable | None, agent_id: UUID
    ) -> str | None:
        """Prevent QA/Documenter from claiming tasks they developed."""
        if not agent or not agent.role:
            return None
        role = agent.role.value if hasattr(agent.role, "value") else str(agent.role)
        if role not in ("qa", "documenter"):
            return None
        original_dev = extract_original_developer(task.quick_context)
        if original_dev and original_dev == str(agent_id):
            return "cannot review your own work (self-review)"
        return None

    def _set_original_developer_context(
        self, task: TaskTable, agent: AgentTable | None
    ) -> None:
        """Set original developer context for QA/Documenter claims.

        Important: We only store original_developer if it's a DIFFERENT agent.
        If PM assigned directly to QA/Documenter (no prior developer), we don't
        set original_developer to avoid blocking them with self-review check.
        """
        if not agent or not agent.role:
            return
        role = agent.role.value if hasattr(agent.role, "value") else str(agent.role)
        if role not in ("qa", "documenter"):
            return
        existing_context = task.quick_context or ""
        if "original_developer:" in existing_context:
            return

        # Only set original_developer if it's a DIFFERENT agent than the one claiming
        # This prevents blocking QA/Documenter when PM assigns directly to them
        if task.assigned_to and str(task.assigned_to) != str(agent.id):
            task.quick_context = f"original_developer:{task.assigned_to}"

    async def claim(
        self, task_id: UUID, agent_id: UUID, allow_reassign: bool = False
    ) -> TaskTable | None:
        """
        Claim a task for an agent.

        Role-based claiming:
        - Developers/PMs: can claim PENDING tasks
        - QA: can claim AWAITING_QA tasks
        - Documenters: can claim PENDING (direct assignment) or AWAITING_DOCUMENTATION
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

        # Prevent self-review: QA/Documenter cannot claim tasks they developed
        if error := self._validate_not_self_review(task, agent, agent_id):
            self.log.warning(f"Cannot claim task - {error}", task_id=str(task_id))
            return None

        # Set context for QA/Documenter claims (only if not already set)
        self._set_original_developer_context(task, agent)

        # Update assignment
        task.assigned_to = cast("Any", agent_id)
        task.claimed_at = datetime.now(UTC)

        # Transition to CLAIMED - validated with role for proper enforcement
        agent_role = agent.role.value if agent and agent.role else None
        claimable_statuses = {
            TaskStatus.PENDING,
            TaskStatus.AWAITING_QA,
            TaskStatus.AWAITING_DOCUMENTATION,
            TaskStatus.AWAITING_PM_REVIEW,
        }
        if task.status in claimable_statuses:
            self._validate_and_set_status(task, TaskStatus.CLAIMED, agent_role)

        await self.session.flush()

        # Auto-create hierarchical branch for git tasks (mandatory - raises on failure)
        # Must happen BEFORE work session creation (work session needs branch_name)
        if task.requires_git and not task.branch_name:
            await self._ensure_branch_for_git_task(task, agent_id)
            await self.session.refresh(task)

        # Create work session for git-enabled tasks claimed by developers
        # (now branch exists, so work session can be created)
        await self._create_work_session_if_needed(task, agent_id, agent_role)

        # Trigger proactive knowledge injection (fire-and-forget)
        bg_task = asyncio.create_task(self._inject_proactive_context(task, agent_id))
        self._background_tasks.add(bg_task)
        bg_task.add_done_callback(self._background_tasks.discard)

        return task

    async def _inject_proactive_context(self, task: TaskTable, agent_id: UUID) -> None:
        """Inject proactive knowledge context when task is claimed.

        Runs as a background task, so uses its own database session.
        """
        from uuid import UUID as PyUUID

        from roboco.db.base import get_session_factory
        from roboco.services.proactive import get_proactive_service

        task_id = PyUUID(str(task.id))
        task_title = task.title
        task_description = task.description or ""

        try:
            proactive = await get_proactive_service()
            agent_uuid = PyUUID(str(agent_id))

            context = await proactive.on_task_claimed(
                task_id=task_id,
                agent_id=agent_uuid,
                task_title=task_title,
                task_description=task_description,
                task_type=None,
            )

            if context and not context.is_empty():
                # Store context in the task using a fresh session
                session_factory = get_session_factory()
                async with session_factory() as session:
                    from sqlalchemy import update

                    await session.execute(
                        update(TaskTable)
                        .where(TaskTable.id == task_id)
                        .values(proactive_context=context.to_dict())
                    )
                    await session.commit()

                self.log.info(
                    "Stored proactive context",
                    task_id=str(task_id),
                    items=len(context.similar_tasks)
                    + len(context.relevant_learnings)
                    + len(context.code_patterns),
                )
        except Exception as e:
            # Don't fail - this is fire-and-forget
            self.log.warning(
                "Failed to inject proactive context",
                task_id=str(task_id),
                error=str(e),
            )

    # =========================================================================
    # GIT WORK SESSION INTEGRATION
    # =========================================================================

    async def _create_work_session_if_needed(
        self,
        task: TaskTable,
        agent_id: UUID,
        agent_role: str | None,
    ) -> WorkSessionTable | None:
        """
        Create a WorkSession when a developer claims a git-enabled task.

        Only creates a session if:
        - Task requires git (requires_git=True)
        - Task has a project_id set
        - Task has a branch_name set (auto-created on claim)
        - Agent is a developer (not QA/Documenter claiming for review)

        Args:
            task: The task being claimed
            agent_id: Agent claiming the task
            agent_role: Agent's role

        Returns:
            Created WorkSession or None if not applicable
        """
        # Only developers need work sessions (not QA/Documenter claiming)
        if agent_role not in ("developer", None):
            return None

        # Check if task requires git workflow
        if not getattr(task, "requires_git", True):
            return None

        # Need project and branch to create a session
        project_id = getattr(task, "project_id", None)
        branch_name = getattr(task, "branch_name", None)

        if not project_id or not branch_name:
            self.log.debug(
                "Skipping work session - no project or branch",
                task_id=str(task.id),
                has_project=bool(project_id),
                has_branch=bool(branch_name),
            )
            return None

        # Get project to determine base/target branches
        result = await self.session.execute(
            select(ProjectTable).where(ProjectTable.id == project_id)
        )
        project = result.scalar_one_or_none()
        if not project:
            self.log.warning(
                "Project not found for work session",
                task_id=str(task.id),
                project_id=str(project_id),
            )
            return None

        # Check if session already exists for this task+agent
        existing = await self.session.execute(
            select(WorkSessionTable).where(
                and_(
                    WorkSessionTable.task_id == task.id,
                    WorkSessionTable.agent_id == agent_id,
                    WorkSessionTable.status == WorkSessionStatus.ACTIVE,
                )
            )
        )
        if existing.scalar_one_or_none():
            self.log.debug(
                "Work session already exists",
                task_id=str(task.id),
                agent_id=str(agent_id),
            )
            return None

        # Determine target branch:
        # - For subtasks: merge into parent task's branch
        # - For parent tasks: merge into default branch (main)
        default_branch = project.default_branch
        target_branch: str = str(default_branch) if default_branch else "main"
        if task.parent_task_id:
            # Get parent task's branch
            parent_id = cast("UUID", task.parent_task_id)
            parent = await self.get(parent_id)
            parent_branch = getattr(parent, "branch_name", None) if parent else None
            if parent_branch:
                target_branch = str(parent_branch)

        # Create the work session
        work_session = WorkSessionTable(
            project_id=project_id,
            task_id=task.id,
            agent_id=agent_id,
            branch_name=branch_name,
            base_branch=target_branch,  # Created from target
            target_branch=target_branch,  # Will merge back to target
            status=WorkSessionStatus.ACTIVE,
        )

        self.session.add(work_session)
        await self.session.flush()

        # Link session to task
        task.work_session_id = cast("Any", work_session.id)
        await self.session.flush()

        self.log.info(
            "Work session created for task",
            task_id=str(task.id),
            session_id=str(work_session.id),
            branch=branch_name,
            target=target_branch,
        )

        return work_session

    # =========================================================================
    # RAG AUTO-INDEXING HOOKS (Fire-and-forget background tasks)
    # =========================================================================

    # Learning extraction thresholds
    _DURATION_OVER_RATIO = 1.5  # Flag if task took 1.5x expected time
    _DURATION_UNDER_RATIO = 0.3  # Flag if task took less than 30% expected
    _MIN_COMMITS_GOOD = 5  # Minimum commits for "good granularity" pattern
    _MIN_NOTES_LENGTH = 50  # Minimum notes length to extract learnings

    async def _extract_completion_learnings(
        self, task: TaskTable, agent_id: UUID | None
    ) -> None:
        """Extract and record learnings from a completed task (fire-and-forget)."""
        from roboco.services.learning import (
            LearningType,
            RecordLearningParams,
            get_learning_service,
        )

        # Extract data before session detaches
        task_id = task.id
        task_title = task.title
        task_team = task.team.value if task.team else None
        started_at = task.started_at
        completed_at = task.completed_at
        estimated_complexity = task.estimated_complexity
        commits = list(task.commits) if task.commits else []
        dev_notes = task.dev_notes
        qa_notes = task.qa_notes
        assigned_to = task.assigned_to

        try:
            learning_svc = await get_learning_service()
            learnings: list[tuple[str, LearningType]] = []

            # Determine scope based on team
            scope = self._determine_learning_scope(task_team)

            # 1. Duration vs estimate insight
            if started_at and completed_at:
                duration_hours = (completed_at - started_at).total_seconds() / 3600
                complexity_hours = {"low": 2.0, "medium": 8.0, "high": 24.0}
                complexity_val = (
                    estimated_complexity.value
                    if hasattr(estimated_complexity, "value")
                    else str(estimated_complexity)
                )
                expected = complexity_hours.get(complexity_val, 8.0)
                ratio = duration_hours / expected if expected > 0 else 1.0

                if ratio > self._DURATION_OVER_RATIO:
                    msg = (
                        f"Task '{task_title}' ({complexity_val}) took "
                        f"{duration_hours:.1f}h vs expected {expected:.0f}h."
                    )
                    learnings.append((msg, LearningType.INSIGHT))
                elif ratio < self._DURATION_UNDER_RATIO:
                    msg = (
                        f"Task '{task_title}' ({complexity_val}) completed "
                        f"quickly in {duration_hours:.1f}h."
                    )
                    learnings.append((msg, LearningType.INSIGHT))

            # 2. Commit pattern analysis
            if len(commits) >= self._MIN_COMMITS_GOOD:
                msg = f"Good commit granularity on '{task_title}': {len(commits)}."
                learnings.append((msg, LearningType.PATTERN))
            elif len(commits) == 1:
                learnings.append(
                    (
                        f"Single commit on '{task_title}'. Try smaller increments.",
                        LearningType.GOTCHA,
                    )
                )

            # 3. Extract from dev_notes
            if dev_notes and len(dev_notes) > self._MIN_NOTES_LENGTH:
                learnings.append(
                    (
                        f"[DEV NOTES] {task_title}: {dev_notes[:500]}",
                        LearningType.SOLUTION,
                    )
                )

            # 4. Extract from qa_notes
            if qa_notes and len(qa_notes) > self._MIN_NOTES_LENGTH:
                learnings.append(
                    (
                        f"[QA FEEDBACK] {task_title}: {qa_notes[:500]}",
                        LearningType.REVIEW_FEEDBACK,
                    )
                )

            # Record all learnings
            for content, ltype in learnings:
                await learning_svc.record_learning(
                    RecordLearningParams(
                        agent_id=to_python_uuid(assigned_to) or agent_id or UUID(int=0),
                        agent_role="developer",
                        content=content,
                        learning_type=ltype,
                        scope=scope,
                        task_id=to_python_uuid(task_id),
                        tags=["auto-extracted", task_team or "general"],
                    )
                )

            if learnings:
                self.log.info(
                    "Extracted completion learnings",
                    task_id=str(task_id),
                    count=len(learnings),
                )
        except Exception as e:
            self.log.warning(
                "Failed to extract learnings",
                task_id=str(task_id),
                error=str(e),
            )

    def _determine_learning_scope(self, team: str | None) -> Any:
        """Map team to learning scope."""
        from roboco.services.learning import LearningScope

        if team in ("backend", "frontend", "ux_ui"):
            return LearningScope.CELL
        if team in ("board", "main_pm"):
            return LearningScope.ORG
        return LearningScope.TEAM

    async def _index_code_changes_background(
        self, task_id: UUID, commits: list[dict[str, Any]], project: str
    ) -> None:
        """Index code files from task commits (fire-and-forget)."""
        from roboco.services.optimal import get_optimal_service

        try:
            optimal = await get_optimal_service()

            # Extract unique file paths from commits
            files: set[str] = set()
            for commit in commits:
                commit_files = commit.get("files", [])
                if isinstance(commit_files, list):
                    files.update(str(f) for f in commit_files)

            if files:
                count = await optimal.index_code(list(files), project=project)
                self.log.debug(
                    "Indexed code files",
                    task_id=str(task_id),
                    files_count=count,
                )
        except Exception as e:
            self.log.warning(
                "Failed to index code",
                task_id=str(task_id),
                error=str(e),
            )

    def _extract_decisions_from_notes(
        self, notes: str, task_title: str
    ) -> list[dict[str, str]]:
        """Parse notes for decision patterns."""
        decisions = []
        decision_patterns = [
            "decided to",
            "chose",
            "decision:",
            "went with",
            "selected",
            "opted for",
            "rationale:",
            "instead of",
        ]

        notes_lower = notes.lower()
        for pattern in decision_patterns:
            if pattern in notes_lower:
                lines = notes.split(".")
                for line in lines:
                    if pattern in line.lower():
                        decisions.append(
                            {
                                "topic": task_title,
                                "decision": line.strip()[:300],
                                "rationale": "Auto-extracted from task notes",
                            }
                        )
                        break
        return decisions

    async def _index_decisions_background(
        self,
        task_id: UUID,
        task_title: str,
        task_team: Team | None,
        dev_notes: str | None,
        agent_id: UUID | None,
    ) -> None:
        """Index decisions detected in notes (fire-and-forget)."""
        from roboco.models.optimal import IndexDecisionParams
        from roboco.services.optimal import get_optimal_service

        if not dev_notes:
            return

        try:
            optimal = await get_optimal_service()
            decisions = self._extract_decisions_from_notes(dev_notes, task_title)

            for decision in decisions:
                await optimal.index_decision(
                    IndexDecisionParams(
                        topic=decision["topic"],
                        decision=decision["decision"],
                        rationale=decision["rationale"],
                        agent_id=agent_id,
                        task_id=task_id,
                        scope="team",
                        tags=[task_team.value if task_team else "general", "auto"],
                    )
                )

            if decisions:
                self.log.debug(
                    "Indexed decisions",
                    task_id=str(task_id),
                    count=len(decisions),
                )
        except Exception as e:
            self.log.warning(
                "Failed to index decisions",
                task_id=str(task_id),
                error=str(e),
            )

    async def _index_docs_background(
        self, task_id: UUID, documents: list[dict[str, Any]]
    ) -> None:
        """Index documentation from completed doc task (fire-and-forget)."""
        from roboco.services.optimal import get_optimal_service

        try:
            optimal = await get_optimal_service()

            # Extract doc paths from documents array
            doc_paths: list[str] = [
                str(d.get("path")) for d in documents if d.get("path")
            ]

            if doc_paths:
                count = await optimal.index_documentation(doc_paths, project="roboco")
                self.log.debug(
                    "Indexed docs",
                    task_id=str(task_id),
                    docs_count=count,
                )
        except Exception as e:
            self.log.warning(
                "Failed to index docs",
                task_id=str(task_id),
                error=str(e),
            )

    # =========================================================================
    # QA AND ERROR INDEXING HOOKS
    # =========================================================================

    def _parse_qa_notes(self, qa_notes: str) -> list[dict[str, str]]:
        """Parse QA notes into structured issues."""
        issues = []
        for raw_line in qa_notes.split("\n"):
            stripped = raw_line.strip()
            if stripped.startswith(("-", "*", "•")):
                issues.append(
                    {
                        "severity": "error",
                        "description": stripped.lstrip("-*• "),
                    }
                )
            elif stripped and stripped[0].isdigit() and "." in stripped[:3]:
                parts = stripped.split(".", 1)
                desc = parts[1].strip() if len(parts) > 1 else stripped
                issues.append({"severity": "error", "description": desc})
        if not issues and qa_notes.strip():
            issues.append({"severity": "error", "description": qa_notes[:500]})
        return issues

    async def _index_qa_review_background(
        self,
        task_id: UUID,
        quick_context: str | None,
        passed: bool,
        qa_notes: str,
        qa_agent_id: UUID | None,
    ) -> None:
        """Index QA review (fire-and-forget)."""
        from roboco.models.optimal import IndexReviewParams
        from roboco.services.optimal import get_optimal_service

        try:
            optimal = await get_optimal_service()
            original_dev = extract_original_developer(quick_context)

            await optimal.record_review(
                IndexReviewParams(
                    file_path=f"task/{task_id}",
                    comments=[
                        {
                            "body": qa_notes,
                            "type": "qa",
                            "severity": "info" if passed else "error",
                        }
                    ],
                    approved=passed,
                    summary=qa_notes[:500] if qa_notes else "QA Review",
                    reviewer_id=qa_agent_id,
                    author_id=UUID(original_dev) if original_dev else None,
                    task_id=task_id,
                )
            )
            self.log.debug("Indexed QA review", task_id=str(task_id), passed=passed)
        except Exception as e:
            self.log.warning(
                "Failed to index QA review",
                task_id=str(task_id),
                error=str(e),
            )

    async def _index_qa_errors_background(
        self,
        task_id: UUID,
        task_title: str,
        task_team: Team | None,
        qa_notes: str,
    ) -> None:
        """Index QA failure issues as error patterns (fire-and-forget)."""
        from roboco.models.optimal import IndexErrorParams
        from roboco.services.optimal import get_optimal_service

        try:
            optimal = await get_optimal_service()
            issues = self._parse_qa_notes(qa_notes)

            for issue in issues:
                await optimal.index_error(
                    IndexErrorParams(
                        error_message=f"QA Failure: {issue['description'][:200]}",
                        context=f"Task: {task_title}",
                        solution="",
                        worked=False,
                        task_id=task_id,
                        team=task_team.value if task_team else None,
                        tags=["qa_failure", issue["severity"]],
                    )
                )

            self.log.debug(
                "Indexed QA errors",
                task_id=str(task_id),
                count=len(issues),
            )
        except Exception as e:
            self.log.warning(
                "Failed to index QA errors",
                task_id=str(task_id),
                error=str(e),
            )

    async def _index_blocker_background(
        self,
        task_id: UUID,
        task_team: Team | None,
        blocker_info: dict[str, str],
    ) -> None:
        """Index blocker as error pattern (fire-and-forget).

        Args:
            task_id: Task UUID
            task_team: Team for categorization
            blocker_info: Dict with keys: type, title, reason, what_needed
        """
        from roboco.models.optimal import IndexErrorParams
        from roboco.services.optimal import get_optimal_service

        try:
            optimal = await get_optimal_service()
            blocker_type = blocker_info.get("type", "unknown")
            reason = blocker_info.get("reason", "")
            title = blocker_info.get("title", "")
            what_needed = blocker_info.get("what_needed", "")

            await optimal.index_error(
                IndexErrorParams(
                    error_message=f"Blocker ({blocker_type}): {reason[:200]}",
                    context=f"Task: {title}\nNeeded: {what_needed}",
                    solution="",
                    worked=False,
                    task_id=task_id,
                    team=task_team.value if task_team else None,
                    tags=["blocker", blocker_type.lower()],
                )
            )
            self.log.debug("Indexed blocker", task_id=str(task_id))
        except Exception as e:
            self.log.warning(
                "Failed to index blocker",
                task_id=str(task_id),
                error=str(e),
            )

    async def _index_lifecycle_event_background(
        self,
        task_id: UUID,
        event_type: str,
        task_title: str,
        task_team: Team | None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Index lifecycle event for pattern analysis (fire-and-forget).

        Tracks task state transitions for organizational learning:
        - Cancellation patterns (what gets cancelled and why)
        - Pause/resume patterns (context switching costs)
        - Block/unblock patterns (dependency bottlenecks)

        Args:
            task_id: Task UUID
            event_type: One of: cancel, pause, resume, block, unblock
            task_title: Task title for context
            task_team: Team for categorization
            details: Additional event details
        """
        from roboco.models.optimal import IndexJournalEntryParams
        from roboco.services.optimal import get_optimal_service

        try:
            optimal = await get_optimal_service()
            details = details or {}

            # Build content for indexing
            content = f"[{event_type.upper()}] {task_title}"
            if details:
                content += f"\nDetails: {details}"

            # Index to journals for lifecycle tracking
            await optimal.index_journal_entry(
                IndexJournalEntryParams(
                    content=content,
                    entry_id=None,  # Will be auto-generated
                    agent_id=None,  # System event, no specific agent
                    entry_type=f"lifecycle_{event_type}",
                    task_id=task_id,
                    tags=[event_type, task_team.value if task_team else "default"],
                )
            )
            self.log.debug(
                "Indexed lifecycle event",
                task_id=str(task_id),
                event_type=event_type,
            )
        except Exception as e:
            self.log.warning(
                "Failed to index lifecycle event",
                task_id=str(task_id),
                event_type=event_type,
                error=str(e),
            )

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

        # PLAN required before starting from CLAIMED (everyone must plan)
        if task.status == TaskStatus.CLAIMED and not task.plan:
            self.log.warning(
                "Cannot start task - no plan",
                task_id=str(task_id),
            )
            return None

        # Only update started_at if this is the first time starting
        if task.started_at is None:
            task.started_at = datetime.now(UTC)
        self._validate_and_set_status(task, TaskStatus.IN_PROGRESS)
        await self.session.flush()
        return task

    async def block(self, task_id: UUID, blocker_task_id: UUID) -> TaskTable | None:
        """Block a task due to a dependency."""
        task = await self.get(task_id)
        if not task:
            return None

        if blocker_task_id not in task.dependency_ids:
            new_deps = [*task.dependency_ids, blocker_task_id]
            task.dependency_ids = new_deps
        self._validate_and_set_status(task, TaskStatus.BLOCKED)
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

        # Index lifecycle event (fire-and-forget)
        blocker_title = blocker.title if blocker else "unknown"
        bg_task = asyncio.create_task(
            self._index_lifecycle_event_background(
                task_id=task_id,
                event_type="block",
                task_title=task.title,
                task_team=task.team,
                details={
                    "blocker_task_id": str(blocker_task_id),
                    "blocker_title": blocker_title,
                },
            )
        )
        self._background_tasks.add(bg_task)
        bg_task.add_done_callback(self._background_tasks.discard)

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

        # Index blocker as error pattern (fire-and-forget)
        blocker_info = {
            "type": blocker_type,
            "title": task.title,
            "reason": reason,
            "what_needed": what_needed,
        }
        bg_task = asyncio.create_task(
            self._index_blocker_background(
                require_uuid(task.id), task.team, blocker_info
            )
        )
        self._background_tasks.add(bg_task)
        bg_task.add_done_callback(self._background_tasks.discard)

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

        # Index lifecycle event (fire-and-forget)
        bg_task = asyncio.create_task(
            self._index_lifecycle_event_background(
                task_id=task_id,
                event_type="unblock",
                task_title=task.title,
                task_team=task.team,
            )
        )
        self._background_tasks.add(bg_task)
        bg_task.add_done_callback(self._background_tasks.discard)

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

        # Index lifecycle event (fire-and-forget)
        bg_task = asyncio.create_task(
            self._index_lifecycle_event_background(
                task_id=task_id,
                event_type="pause",
                task_title=task.title,
                task_team=task.team,
            )
        )
        self._background_tasks.add(bg_task)
        bg_task.add_done_callback(self._background_tasks.discard)

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

        # Index lifecycle event (fire-and-forget)
        bg_task = asyncio.create_task(
            self._index_lifecycle_event_background(
                task_id=task_id,
                event_type="resume",
                task_title=task.title,
                task_team=task.team,
            )
        )
        self._background_tasks.add(bg_task)
        bg_task.add_done_callback(self._background_tasks.discard)

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

        # Store original developer BEFORE clearing assignment - authoritative
        # record for self-review prevention (QA can't review own work).
        original_dev = str(task.assigned_to) if task.assigned_to else None
        if original_dev:
            task.quick_context = f"original_developer:{original_dev}"

        # Clear assignment so QA can claim the task
        # The original developer is preserved in quick_context
        task.assigned_to = None
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
        """Mark task as passed QA.

        QA workflow: awaiting_qa → claimed → in_progress → pass_qa
        → awaiting_documentation. Accept claimed/in_progress status.
        """
        task = await self.get(task_id)
        if not task:
            return None

        # Accept tasks QA is actively working on (claimed or in_progress)
        # as well as awaiting_qa (for direct pass without starting)
        valid_statuses = {
            TaskStatus.AWAITING_QA,
            TaskStatus.CLAIMED,
            TaskStatus.IN_PROGRESS,
        }
        if task.status not in valid_statuses:
            return None

        if notes:
            task.qa_notes = notes

        # Store QA agent before clearing assignment
        qa_agent_id = task.assigned_to

        # Clear assignment so documenter can claim the task
        task.assigned_to = None
        task.qa_verified = True
        task.status = TaskStatus.AWAITING_DOCUMENTATION
        await self.session.flush()

        # Index positive QA review (fire-and-forget)
        bg_task = asyncio.create_task(
            self._index_qa_review_background(
                require_uuid(task.id),
                task.quick_context,
                True,
                notes or "Passed QA review",
                to_python_uuid(qa_agent_id),
            )
        )
        self._background_tasks.add(bg_task)
        bg_task.add_done_callback(self._background_tasks.discard)

        self.log.info("Task passed QA", task_id=str(task_id))
        return task

    async def fail_qa(self, task_id: UUID, notes: str) -> TaskTable | None:
        """
        Mark task as failed QA and reassign to original developer.

        When QA fails a task, it goes back to the original developer for revision.
        The original developer is extracted from quick_context which stores
        "original_developer:{uuid}" when the task was submitted to QA.

        QA workflow: awaiting_qa → claimed → in_progress → fail_qa → needs_revision
        So we need to accept tasks in claimed or in_progress status.
        """
        task = await self.get(task_id)
        if not task:
            return None

        # Accept tasks QA is actively working on
        valid_statuses = {
            TaskStatus.AWAITING_QA,
            TaskStatus.CLAIMED,
            TaskStatus.IN_PROGRESS,
        }
        if task.status not in valid_statuses:
            return None

        task.qa_notes = notes
        task.qa_verified = False
        task.status = TaskStatus.NEEDS_REVISION

        # Store QA agent before reassigning
        qa_agent_id = task.assigned_to

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

        # Index negative QA review (fire-and-forget)
        review_task = asyncio.create_task(
            self._index_qa_review_background(
                require_uuid(task.id),
                task.quick_context,
                False,
                notes,
                to_python_uuid(qa_agent_id),
            )
        )
        self._background_tasks.add(review_task)
        review_task.add_done_callback(self._background_tasks.discard)

        # Index issues as error patterns (fire-and-forget)
        error_task = asyncio.create_task(
            self._index_qa_errors_background(
                require_uuid(task.id), task.title, task.team, notes
            )
        )
        self._background_tasks.add(error_task)
        error_task.add_done_callback(self._background_tasks.discard)

        self.log.info("Task failed QA", task_id=str(task_id))
        return task

    async def docs_complete(
        self,
        task_id: UUID,
        doc_notes: str | None = None,
    ) -> TaskTable | None:
        """
        Mark documentation as complete (documenter only).

        Documenter workflow: awaiting_documentation → claim → plan → start
        → docs_complete. Accept claimed/in_progress (documenter working).

        Args:
            task_id: The task to mark docs complete
            doc_notes: Optional notes about the documentation

        Returns:
            The updated task or None if not allowed
        """
        task = await self.get(task_id)
        if not task:
            return None

        # Accept documenter workflow statuses: awaiting_documentation, claimed,
        # in_progress (documenter actively working on documentation)
        valid_statuses = {
            TaskStatus.AWAITING_DOCUMENTATION,
            TaskStatus.CLAIMED,
            TaskStatus.IN_PROGRESS,
        }
        if task.status not in valid_statuses:
            self.log.warning(
                "Cannot mark docs complete - invalid status for documenter workflow",
                task_id=str(task_id),
                current_status=task.status.value,
            )
            return None

        # Check all descendants are in terminal states before escalating
        all_descendants = await self.get_all_descendants(task_id)
        incomplete = [
            d
            for d in all_descendants
            if d.status not in (TaskStatus.COMPLETED, TaskStatus.CANCELLED)
        ]
        if incomplete:
            self.log.warning(
                "Cannot mark docs complete - incomplete descendants",
                task_id=str(task_id),
                incomplete_count=len(incomplete),
                incomplete_ids=[str(d.id) for d in incomplete[:5]],
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

        # Mark docs as complete
        task.docs_complete = True

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

        # For git tasks: check if BOTH docs_complete AND pr_created are true
        # (Developer works in parallel, creating PR)
        from roboco.enforcement.task_lifecycle import check_parallel_completion

        ready_for_pm = check_parallel_completion(
            docs_complete=True,  # We just set this
            pr_created=task.pr_created,
            requires_git=task.requires_git,
        )

        if ready_for_pm:
            # Both conditions met - transition to PM review (validated)
            self._validate_and_set_status(
                task, TaskStatus.AWAITING_PM_REVIEW, "documenter"
            )
            # Clear assignment so PM can claim the task for review
            task.assigned_to = None
            self.log.info(
                "Documentation complete, awaiting PM review",
                task_id=str(task_id),
                requires_git=task.requires_git,
                pr_created=task.pr_created,
            )
        else:
            # Git task: docs done but PR not yet created
            # Stay in awaiting_documentation, waiting for developer to create PR
            self.log.info(
                "Documentation complete, waiting for developer to create PR",
                task_id=str(task_id),
                docs_complete=True,
                pr_created=task.pr_created,
            )

        await self.session.flush()

        # Index documentation artifacts (fire-and-forget)
        if task.documents:
            bg_task = asyncio.create_task(
                self._index_docs_background(require_uuid(task.id), task.documents)
            )
            self._background_tasks.add(bg_task)
            bg_task.add_done_callback(self._background_tasks.discard)

        return task

    async def mark_pr_created(
        self,
        task_id: UUID,
        pr_number: int,
        pr_url: str,
    ) -> TaskTable | None:
        """
        Mark that developer has created a PR for the task.

        Called when developer uses roboco_git_create_pr(). This method:
        1. Sets pr_created=True, pr_number, pr_url on the task
        2. Checks if docs_complete is also True
        3. If both complete, transitions to awaiting_pm_review

        This works in parallel with documenter's docs_complete().

        Args:
            task_id: The task ID
            pr_number: GitHub/GitLab PR number
            pr_url: Full URL to the PR

        Returns:
            The updated task or None if not allowed
        """
        task = await self.get(task_id)
        if not task:
            return None

        # Only allow in awaiting_documentation (parallel execution phase)
        if task.status != TaskStatus.AWAITING_DOCUMENTATION:
            self.log.warning(
                "Cannot mark PR created - task not in awaiting_documentation",
                task_id=str(task_id),
                current_status=task.status.value,
            )
            return None

        # Set PR info
        task.pr_created = True
        task.pr_number = pr_number
        task.pr_url = pr_url

        # Store developer who created PR in quick_context
        if task.assigned_to:
            existing_context = task.quick_context or ""
            if "pr_author:" not in existing_context:
                pr_context = f"pr_author:{task.assigned_to}"
                task.quick_context = (
                    f"{existing_context}\n{pr_context}".strip()
                    if existing_context
                    else pr_context
                )

        # Check if BOTH docs_complete AND pr_created are now true
        from roboco.enforcement.task_lifecycle import check_parallel_completion

        ready_for_pm = check_parallel_completion(
            docs_complete=task.docs_complete,
            pr_created=True,  # We just set this
            requires_git=task.requires_git,
        )

        if ready_for_pm:
            # Both conditions met - transition to PM review
            task.status = TaskStatus.AWAITING_PM_REVIEW
            # Clear assignment so PM can claim the task for review
            task.assigned_to = None
            self.log.info(
                "PR created, awaiting PM review",
                task_id=str(task_id),
                pr_number=pr_number,
                docs_complete=task.docs_complete,
            )
        else:
            # PR created but docs not yet complete
            # Stay in awaiting_documentation, waiting for documenter
            self.log.info(
                "PR created, waiting for documenter to complete",
                task_id=str(task_id),
                pr_number=pr_number,
                docs_complete=task.docs_complete,
            )

        await self.session.flush()
        return task

    async def submit_for_pm_review(
        self,
        task_id: UUID,
        notes: str | None = None,
    ) -> TaskTable | None:
        """
        Submit a task directly for PM review (any assigned agent).

        Use this for tasks that don't follow the standard dev→QA→docs workflow,
        such as PM validation tasks, QA audit tasks, or other directly-assigned work.

        Transitions task from IN_PROGRESS to AWAITING_PM_REVIEW.

        Args:
            task_id: The task to submit
            notes: Optional completion notes

        Returns:
            The updated task or None if not allowed
        """
        task = await self.get(task_id)
        if not task:
            return None

        # Only allow submission from in_progress status
        if task.status != TaskStatus.IN_PROGRESS:
            self.log.warning(
                "Cannot submit for PM review - task not in progress",
                task_id=str(task_id),
                current_status=task.status.value,
            )
            return None

        # Check all descendants are in terminal states before escalating
        all_descendants = await self.get_all_descendants(task_id)
        incomplete = [
            d
            for d in all_descendants
            if d.status not in (TaskStatus.COMPLETED, TaskStatus.CANCELLED)
        ]
        if incomplete:
            self.log.warning(
                "Cannot submit for PM review - incomplete descendants",
                task_id=str(task_id),
                incomplete_count=len(incomplete),
                incomplete_ids=[str(d.id) for d in incomplete[:5]],
            )
            return None

        # Store notes in quick_context
        if notes:
            existing_context = task.quick_context or ""
            note_entry = f"completion_notes:{notes}"
            task.quick_context = (
                f"{existing_context}\n{note_entry}".strip()
                if existing_context
                else note_entry
            )

        task.status = TaskStatus.AWAITING_PM_REVIEW
        await self.session.flush()

        self.log.info(
            "Task submitted for PM review",
            task_id=str(task_id),
        )
        return task

    async def _get_completing_agent_role(self, agent_id: UUID | None) -> str | None:
        """Get the role of the completing agent."""
        if not agent_id:
            return None
        agent_result = await self.session.execute(
            select(AgentTable).where(AgentTable.id == agent_id)
        )
        agent = agent_result.scalar_one_or_none()
        if agent and agent.role:
            return agent.role.value if hasattr(agent.role, "value") else str(agent.role)
        return None

    def _is_valid_completion_status(
        self, task: TaskTable, agent_id: UUID | None
    ) -> bool:
        """Check if task is in a valid status for completion."""
        if task.status == TaskStatus.AWAITING_PM_REVIEW:
            return True
        is_own_task = agent_id and task.assigned_to == agent_id
        return task.status == TaskStatus.IN_PROGRESS and bool(is_own_task)

    async def _handle_cell_pm_escalation(
        self, task: TaskTable, task_id: UUID, agent_id: UUID | None
    ) -> TaskTable | None:
        """Handle Cell PM escalation to Main PM. Returns task if escalated."""
        main_pm_result = await self.session.execute(
            select(AgentTable).where(AgentTable.role == AgentRole.MAIN_PM)
        )
        main_pm = main_pm_result.scalar_one_or_none()
        if not main_pm:
            self.log.warning(
                "No Main PM found - proceeding with completion", task_id=str(task_id)
            )
            return None

        task.assigned_to = cast("Any", main_pm.id)
        await self.session.flush()
        await self._emit_task_event(
            EventType.TASK_ESCALATED_TO_MAIN_PM,
            task_id,
            {
                "main_pm_id": str(main_pm.id),
                "cell_pm_id": str(agent_id) if agent_id else None,
            },
        )
        self.log.info(
            "Cell PM approved - escalating to Main PM",
            task_id=str(task_id),
            main_pm_id=str(main_pm.id),
        )
        return task

    async def _validate_completion_prerequisites(
        self, task: TaskTable, task_id: UUID, agent_id: UUID | None
    ) -> list[TaskTable] | None:
        """Validate task can be completed. Returns descendants or None."""
        if not self._is_valid_completion_status(task, agent_id):
            self.log.warning("Cannot complete - invalid status", task_id=str(task_id))
            return None

        all_descendants = await self.get_all_descendants(task_id)
        incomplete = [
            st
            for st in all_descendants
            if st.status not in (TaskStatus.COMPLETED, TaskStatus.CANCELLED)
        ]
        if incomplete:
            self.log.warning(
                "Cannot complete - incomplete descendants", task_id=str(task_id)
            )
            return None
        return all_descendants

    async def _trigger_completion_hooks(
        self, task: TaskTable, agent_id: UUID | None
    ) -> None:
        """Trigger background RAG indexing hooks after completion."""
        bg_task = asyncio.create_task(
            self._extract_completion_learnings(task, agent_id)
        )
        self._background_tasks.add(bg_task)
        bg_task.add_done_callback(self._background_tasks.discard)

        if task.commits:
            code_task = asyncio.create_task(
                self._index_code_changes_background(
                    require_uuid(task.id),
                    task.commits,
                    task.team.value if task.team else "default",
                )
            )
            self._background_tasks.add(code_task)
            code_task.add_done_callback(self._background_tasks.discard)

        if task.dev_notes:
            decision_task = asyncio.create_task(
                self._index_decisions_background(
                    require_uuid(task.id),
                    task.title,
                    task.team,
                    task.dev_notes,
                    to_python_uuid(task.assigned_to),
                )
            )
            self._background_tasks.add(decision_task)
            decision_task.add_done_callback(self._background_tasks.discard)

    async def complete(
        self,
        task_id: UUID,
        agent_id: UUID | None = None,
        force_with_cancelled: bool = False,
        justification: str | None = None,
    ) -> TaskTable | None:
        """
        Mark task as completed (PM only).

        Approval hierarchy:
        1. Cell PM reviews → reassigns to Main PM (same awaiting_pm_review state)
        2. Main PM reviews leaf task → completes
        3. Main PM reviews parent task (all descendants terminal) → escalates to CEO
        """
        task = await self.get(task_id)
        if not task:
            return None

        completing_agent_role = await self._get_completing_agent_role(agent_id)
        all_descendants = await self._validate_completion_prerequisites(
            task, task_id, agent_id
        )
        if all_descendants is None:
            return None

        # APPROVAL HIERARCHY: Cell PM → Main PM → CEO
        if task.status == TaskStatus.AWAITING_PM_REVIEW:
            if completing_agent_role == "cell_pm":
                escalated = await self._handle_cell_pm_escalation(
                    task, task_id, agent_id
                )
                if escalated:
                    return escalated
            # Only escalate root-level parents to CEO (subtasks complete directly)
            is_root_parent = all_descendants and not task.parent_task_id
            if completing_agent_role == "main_pm" and is_root_parent:
                self.log.info(
                    "Main PM approved root parent - escalating to CEO",
                    task_id=str(task_id),
                )
                return await self.escalate_to_ceo(task_id, "main_pm")

        # Handle cancelled descendants - require force flag and justification
        cancelled = [st for st in all_descendants if st.status == TaskStatus.CANCELLED]
        if cancelled and (not force_with_cancelled or not justification):
            self.log.warning(
                "Cannot complete - cancelled descendants", task_id=str(task_id)
            )
            return None

        task.completed_at = datetime.now(UTC)
        self._validate_and_set_status(
            task, TaskStatus.COMPLETED, completing_agent_role or "cell_pm"
        )
        await self.session.flush()

        await self._trigger_completion_hooks(task, agent_id)
        await self._unblock_dependents(task_id)
        return task

    # =========================================================================
    # CEO APPROVAL WORKFLOW
    # =========================================================================

    async def escalate_to_ceo(
        self,
        task_id: UUID,
        agent_role: str = "cell_pm",
        notes: str | None = None,
    ) -> TaskTable | None:
        """
        Escalate a task to CEO for final approval (PM only).

        Used for major tasks that require CEO sign-off before merge:
        - Parent tasks with subtasks
        - High-priority features
        - Breaking changes

        Args:
            task_id: The task to escalate
            agent_role: Role of the agent escalating (must be PM)
            notes: Optional notes for the CEO

        Returns:
            The escalated task or None if escalation not allowed
        """
        task = await self.get(task_id)
        if not task:
            return None

        # Only allow escalation from awaiting_pm_review
        if task.status != TaskStatus.AWAITING_PM_REVIEW:
            self.log.warning(
                "Cannot escalate to CEO - task not in PM review",
                task_id=str(task_id),
                current_status=task.status.value,
            )
            return None

        # Only parent tasks can be escalated to CEO (not subtasks)
        if task.parent_task_id:
            self.log.warning(
                "Cannot escalate subtask to CEO - only parent tasks allowed",
                task_id=str(task_id),
                parent_task_id=str(task.parent_task_id),
            )
            return None

        # ENFORCEMENT: Git tasks must have PR created before CEO approval
        if task.requires_git and not task.pr_number:
            self.log.warning(
                "Cannot escalate to CEO - git task has no PR",
                task_id=str(task_id),
                requires_git=task.requires_git,
                pr_created=task.pr_created,
            )
            return None

        # Store escalation notes
        if notes:
            existing_context = task.quick_context or ""
            note_entry = f"escalation_notes:{notes}"
            task.quick_context = (
                f"{existing_context}\n{note_entry}".strip()
                if existing_context
                else note_entry
            )

        # Validate transition with PM role requirement
        self._validate_and_set_status(
            task, TaskStatus.AWAITING_CEO_APPROVAL, agent_role
        )
        await self.session.flush()

        # Emit event for CEO approval queue
        await self._emit_task_event(
            EventType.TASK_AWAITING_CEO_APPROVAL,
            task_id,
            {"escalated_by_role": agent_role, "notes": notes},
        )

        self.log.info(
            "Task escalated to CEO for approval",
            task_id=str(task_id),
            escalated_by_role=agent_role,
        )
        return task

    async def ceo_approve(
        self,
        task_id: UUID,
        notes: str | None = None,
    ) -> TaskTable | None:
        """
        CEO approves and completes a task.

        Final approval step for major tasks. Only CEO can perform this action.

        Args:
            task_id: The task to approve
            notes: Optional CEO notes

        Returns:
            The completed task or None if approval not allowed
        """
        task = await self.get(task_id)
        if not task:
            return None

        # Only allow approval from awaiting_ceo_approval
        if task.status != TaskStatus.AWAITING_CEO_APPROVAL:
            self.log.warning(
                "Cannot CEO approve - task not awaiting CEO approval",
                task_id=str(task_id),
                current_status=task.status.value,
            )
            return None

        # Store CEO notes
        if notes:
            existing_context = task.quick_context or ""
            note_entry = f"ceo_approval_notes:{notes}"
            task.quick_context = (
                f"{existing_context}\n{note_entry}".strip()
                if existing_context
                else note_entry
            )

        task.completed_at = datetime.now(UTC)
        # Validate transition with CEO role requirement
        self._validate_and_set_status(task, TaskStatus.COMPLETED, "ceo")
        await self.session.flush()

        # Extract learnings (fire-and-forget)
        bg_task = asyncio.create_task(self._extract_completion_learnings(task, None))
        self._background_tasks.add(bg_task)
        bg_task.add_done_callback(self._background_tasks.discard)

        # Unblock any tasks waiting on this one
        await self._unblock_dependents(task_id)

        # Emit event for CEO approval
        await self._emit_task_event(
            EventType.TASK_CEO_APPROVED,
            task_id,
            {"notes": notes},
        )

        self.log.info(
            "Task approved by CEO",
            task_id=str(task_id),
        )
        return task

    async def ceo_reject(
        self,
        task_id: UUID,
        reason: str,
    ) -> TaskTable | None:
        """
        CEO rejects a task and sends back for revision.

        Task goes back to NEEDS_REVISION and is reassigned to the
        original developer (if tracked in quick_context).

        Args:
            task_id: The task to reject
            reason: Required reason for rejection

        Returns:
            The rejected task or None if rejection not allowed
        """
        task = await self.get(task_id)
        if not task:
            return None

        # Only allow rejection from awaiting_ceo_approval
        if task.status != TaskStatus.AWAITING_CEO_APPROVAL:
            self.log.warning(
                "Cannot CEO reject - task not awaiting CEO approval",
                task_id=str(task_id),
                current_status=task.status.value,
            )
            return None

        # Store CEO rejection reason
        existing_context = task.quick_context or ""
        rejection_entry = f"ceo_rejection:{reason}"
        task.quick_context = (
            f"{existing_context}\n{rejection_entry}".strip()
            if existing_context
            else rejection_entry
        )

        # Validate transition with CEO role requirement
        self._validate_and_set_status(task, TaskStatus.NEEDS_REVISION, "ceo")

        # Try to reassign to original developer
        original_dev = extract_original_developer(task.quick_context)
        if original_dev:
            task.assigned_to = cast("Any", UUID(original_dev))
            self.log.info(
                "Task reassigned to original developer after CEO rejection",
                task_id=str(task_id),
                original_developer=original_dev,
            )
        else:
            # Clear assignment so it can be claimed
            task.assigned_to = None

        await self.session.flush()

        # Emit event for CEO rejection
        await self._emit_task_event(
            EventType.TASK_CEO_REJECTED,
            task_id,
            {"reason": reason, "reassigned_to": original_dev},
        )

        self.log.info(
            "Task rejected by CEO",
            task_id=str(task_id),
            reason=reason,
        )
        return task

    async def cancel(
        self, task_id: UUID, agent_role: str = "cell_pm"
    ) -> TaskTable | None:
        """Cancel a task and all its descendants (PM only)."""
        task = await self.get(task_id)
        if not task:
            return None

        # Cancel all descendants first (children, grandchildren, etc.)
        # Skip tasks already in terminal states (completed or cancelled)
        descendants = await self.get_all_descendants(task_id)
        cancelled_count = 0
        for descendant in descendants:
            if descendant.status not in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
                descendant.status = TaskStatus.CANCELLED
                cancelled_count += 1

        if cancelled_count > 0:
            self.log.info(
                "Cascaded cancel to descendants",
                task_id=str(task_id),
                cancelled_count=cancelled_count,
            )

        # Validate transition with PM role requirement
        self._validate_and_set_status(task, TaskStatus.CANCELLED, agent_role)
        await self.session.flush()

        # Index lifecycle event (fire-and-forget)
        bg_task = asyncio.create_task(
            self._index_lifecycle_event_background(
                task_id=task_id,
                event_type="cancel",
                task_title=task.title,
                task_team=task.team,
                details={
                    "cancelled_by_role": agent_role,
                    "descendants_cancelled": cancelled_count,
                },
            )
        )
        self._background_tasks.add(bg_task)
        bg_task.add_done_callback(self._background_tasks.discard)

        return task

    async def _emit_task_event(
        self,
        event_type: EventType,
        task_id: UUID,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Emit a task lifecycle event to the event bus.

        Events are published asynchronously. Failures are logged but
        do not interrupt the calling operation.

        Args:
            event_type: The type of event to emit
            task_id: The task this event relates to
            data: Optional additional event data
        """
        try:
            bus = get_event_bus()
            if bus.is_connected():
                event_data = {"task_id": str(task_id)}
                if data:
                    event_data.update(data)
                await bus.publish(Event(type=event_type, data=event_data))
        except Exception as e:
            self.log.warning(
                "Failed to emit task event",
                event_type=event_type.value,
                task_id=str(task_id),
                error=str(e),
            )

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
        """List tasks for a team, ordered by priority, sequence, created_at."""
        query = select(TaskTable).where(TaskTable.team == team)

        if status:
            query = query.where(TaskTable.status == status)

        query = query.order_by(
            TaskTable.priority,
            TaskTable.sequence,
            TaskTable.created_at,
        )
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
        """List tasks by status, ordered by priority, sequence, created_at."""
        query = select(TaskTable).where(TaskTable.status == status)

        if team:
            query = query.where(TaskTable.team == team)

        # Order by priority first, then sequence (for sibling order), then created_at
        query = query.order_by(
            TaskTable.priority,
            TaskTable.sequence,
            TaskTable.created_at,
        )

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def list_pending(
        self,
        team: Team | None = None,
        filter_by_dependencies: bool = True,
    ) -> list[TaskTable]:
        """
        List pending tasks (available to claim).

        Args:
            team: Filter by team
            filter_by_dependencies: If True, exclude tasks with incomplete dependencies

        Returns:
            List of pending tasks, ordered by priority, sequence, then created_at
        """
        tasks = await self.list_by_status(TaskStatus.PENDING, team)

        if not filter_by_dependencies:
            return tasks

        # Filter out tasks whose dependencies aren't complete
        available_tasks = []
        for task in tasks:
            if not task.dependency_ids:
                available_tasks.append(task)
                continue

            # Check if all dependencies are complete
            deps_result = await self.session.execute(
                select(TaskTable.status).where(TaskTable.id.in_(task.dependency_ids))
            )
            dep_statuses = deps_result.scalars().all()

            # All dependencies must be COMPLETED or CANCELLED
            terminal_statuses = {TaskStatus.COMPLETED, TaskStatus.CANCELLED}
            if all(s in terminal_statuses for s in dep_statuses):
                available_tasks.append(task)

        return available_tasks

    async def list_blocked(self, team: Team | None = None) -> list[TaskTable]:
        """List blocked tasks."""
        return await self.list_by_status(TaskStatus.BLOCKED, team)

    async def list_awaiting_qa(self, team: Team | None = None) -> list[TaskTable]:
        """List tasks awaiting QA review."""
        return await self.list_by_status(TaskStatus.AWAITING_QA, team)

    async def list_awaiting_docs(self, team: Team | None = None) -> list[TaskTable]:
        """List tasks awaiting documentation."""
        return await self.list_by_status(TaskStatus.AWAITING_DOCUMENTATION, team)

    async def list_awaiting_pm_review(
        self, team: Team | None = None
    ) -> list[TaskTable]:
        """List tasks awaiting PM review."""
        return await self.list_by_status(TaskStatus.AWAITING_PM_REVIEW, team)

    async def list_awaiting_ceo_approval(self) -> list[TaskTable]:
        """List tasks awaiting CEO approval (org-wide, no team filter)."""
        return await self.list_by_status(TaskStatus.AWAITING_CEO_APPROVAL)

    async def get_subtasks(self, parent_task_id: UUID) -> list[TaskTable]:
        """Get all subtasks of a parent task."""
        result = await self.session.execute(
            select(TaskTable)
            .where(TaskTable.parent_task_id == parent_task_id)
            .order_by(TaskTable.created_at)
        )
        return list(result.scalars().all())

    async def get_all_descendants(self, task_id: UUID) -> list[TaskTable]:
        """Recursively get ALL descendant tasks (children, grandchildren, etc.).

        Uses iterative BFS to avoid recursion limits and handle arbitrary depth.
        """
        descendants: list[TaskTable] = []
        to_process: list[UUID] = [task_id]

        while to_process:
            current_id = to_process.pop(0)
            children = await self.get_subtasks(current_id)
            for child in children:
                descendants.append(child)
                # child.id is SQLAlchemy Mapped[UUID]
                # but resolves to uuid.UUID at runtime
                to_process.append(child.id)  # type: ignore[arg-type]

        return descendants

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
# PM RESOLUTION HELPERS
# =============================================================================


async def resolve_pm_for_substitute(
    db: AsyncSession,
    agent_slug: str | None,
    task_team: Team | None,
) -> tuple[str | None, UUID | None]:
    """
    Resolve the PM slug and UUID for a substitute request.

    Args:
        db: Database session
        agent_slug: The agent's slug for PM lookup
        task_team: The task's team for fallback PM lookup

    Returns:
        Tuple of (pm_slug, pm_uuid) or (None, None) if not found
    """
    from roboco.agents_config import get_pm_for_agent, get_pm_for_team

    target_pm_slug = None
    if agent_slug:
        target_pm_slug = get_pm_for_agent(agent_slug)
    if not target_pm_slug and task_team:
        target_pm_slug = get_pm_for_team(task_team.value)

    if not target_pm_slug:
        return None, None

    pm_result = await db.execute(
        select(AgentTable).where(AgentTable.slug == target_pm_slug)
    )
    pm_agent = pm_result.scalar_one_or_none()
    return target_pm_slug, to_python_uuid(pm_agent.id) if pm_agent else None


async def notify_pm_for_substitute(
    db: AsyncSession,
    pm_slug: str,
    task_id: UUID,
    from_agent_id: UUID,
    message: tuple[str, str],
) -> None:
    """
    Create and deliver a notification to PM for substitute request.

    Args:
        db: Database session
        pm_slug: Target PM's slug
        task_id: The task being substituted
        from_agent_id: Agent requesting substitution
        message: Tuple of (subject, body) for the notification
    """
    from roboco.db.tables import NotificationTable
    from roboco.services.notification_delivery import get_notification_delivery_service

    pm_result = await db.execute(select(AgentTable).where(AgentTable.slug == pm_slug))
    pm_agent = pm_result.scalar_one_or_none()
    if not pm_agent:
        return

    subject, body = message
    notification = NotificationTable(
        type="task_assignment",
        priority="high",
        from_agent=from_agent_id,
        to_agents=[pm_agent.id],
        subject=subject,
        body=body,
        related_task_id=task_id,
        requires_ack=True,
    )
    db.add(notification)
    await db.flush()

    delivery_service = get_notification_delivery_service(db)
    await delivery_service.deliver(require_uuid(notification.id))


# =============================================================================
# SERVICE FACTORY
# =============================================================================


def get_task_service(session: AsyncSession) -> TaskService:
    """Get a TaskService instance."""
    return TaskService(session)
