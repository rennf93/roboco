"""
Task Service

Provides CRUD operations and lifecycle management for tasks.
Handles status transitions, assignments, and queries.
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar, cast
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
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
    TaskLifecycleError,
    TaskOwnershipError,
    validate_git_requirements,
    validate_task_ownership,
    validate_task_transition,
)
from roboco.events import Event, EventType, get_event_bus
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    BlockerResolverType,
    TaskNature,
    TaskStatus,
    Team,
)
from roboco.models.permissions import AgentContext, TaskAction
from roboco.models.task import TaskCreateRequest
from roboco.models.work_session import WorkSessionStatus
from roboco.services.base import (
    BaseService,
    NotFoundError,
    ServiceError,
    UnauthorizedError,
    ValidationError,
)
from roboco.utils.converters import require_uuid, to_python_uuid

if TYPE_CHECKING:
    from roboco.services.permissions import PermissionService

# UUID format constants for validation
_UUID_LENGTH = 36  # Standard UUID string length
_UUID_HYPHEN_COUNT = 4  # Number of hyphens in a UUID


_ROLE_CLAIM_STATUSES: dict[str, set[TaskStatus]] = {
    "qa": {TaskStatus.PENDING, TaskStatus.AWAITING_QA},
    "documenter": {TaskStatus.PENDING, TaskStatus.AWAITING_DOCUMENTATION},
    "cell_pm": {TaskStatus.PENDING, TaskStatus.AWAITING_PM_REVIEW},
    "main_pm": {TaskStatus.PENDING, TaskStatus.AWAITING_PM_REVIEW},
}


# Notes fields (dev_notes, qa_notes, quick_context) are append-only —
# every revision cycle adds more. Cap total size so a task that cycles
# dozens of times doesn't grow into megabytes. When we exceed the cap,
# keep the latest entries and prepend a "[...truncated]" marker so the
# reader can tell something was dropped.
_MAX_NOTES_CHARS = 8000
_TRUNCATION_MARKER = "[...earlier notes truncated for size...]\n"


def _append_capped(existing: str | None, addition: str) -> str:
    """Append `addition` to `existing`, capped at _MAX_NOTES_CHARS.

    When the combined size exceeds the cap, drops OLDEST content (not
    newest — the newest entry is what the current agent/reviewer needs).
    """
    base = existing.strip() if existing else ""
    joined = f"{base}\n\n{addition}" if base else addition
    if len(joined) <= _MAX_NOTES_CHARS:
        return joined
    # Keep the newest, drop enough of the head to fit the marker + content.
    keep = _MAX_NOTES_CHARS - len(_TRUNCATION_MARKER)
    return _TRUNCATION_MARKER + joined[-keep:]


@dataclass
class _CompletionSnapshot:
    """Fields copied off a TaskTable before its session detaches.

    Bundles the inputs to `_collect_completion_learnings` so we stay
    under PLR0913 without losing the explicit contract.
    """

    task_title: str | None
    started_at: Any
    completed_at: Any
    estimated_complexity: Any
    commits: list[Any]
    dev_notes: str | None
    qa_notes: str | None


@dataclass(frozen=True)
class SoftBlockInput:
    """Primitive blocker fields from the API layer.

    Route-layer DTO that bundles the raw string inputs so the service
    method signature stays under PLR0913 without leaking the API's
    Pydantic schema into the service. `resolver_type_raw` is coerced to
    BlockerResolverType inside the service.
    """

    blocker_type: str
    reason: str
    what_needed: str
    resolver_type_raw: str


@dataclass
class SoftBlockInfo:
    """Blocker metadata for `TaskService.soft_block`.

    Bundles the four blocker fields — reason, type, what's needed, and
    resolver type — so soft_block stays under PLR0913.
    """

    reason: str
    blocker_type: str
    what_needed: str
    resolver_type: BlockerResolverType | None = None


@dataclass(frozen=True)
class GatewayAgentView:
    """Read-only union of DB-backed and config-derived agent attributes.

    The Choreographer reads `agent.id`, `agent.role`, `agent.team`,
    `agent.skills`, and `agent.escalation_target` uniformly. The first
    three live on AgentTable; the last two come from `agents_config`. This
    view assembles them so the Choreographer can stay agnostic about
    storage location.
    """

    id: UUID
    role: str
    team: str | None
    escalation_target: str | None
    skills: list[dict[str, Any]]


def _default_claim_statuses(role: str | None) -> set[TaskStatus]:
    """Return the base (non-reassign) claim statuses for a role."""
    if role is None:
        return {TaskStatus.PENDING}
    if role in _ROLE_CLAIM_STATUSES:
        return set(_ROLE_CLAIM_STATUSES[role])
    # Developer and other roles
    # NEEDS_REVISION for when task is reassigned after QA rejection
    return {TaskStatus.PENDING, TaskStatus.NEEDS_REVISION}


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
    role: str | None = None
    if agent and agent.role:
        role = agent.role.value if hasattr(agent.role, "value") else str(agent.role)

    statuses = _default_claim_statuses(role)
    if allow_reassign:
        statuses.add(TaskStatus.CLAIMED)
    return statuses


def extract_original_developer(quick_context: str | None) -> str | None:
    """
    Safely extract original developer ID from quick_context.

    The quick_context stores original developer as the "original_developer:
    {uuid}" entry on its own line. Other entries (doc_notes, documenter,
    etc.) may be appended on subsequent lines, so scan line-by-line rather
    than assuming the field is the first and only token.

    Args:
        quick_context: The task's quick_context field value

    Returns:
        UUID string of original developer, or None if not found/invalid
    """
    if not quick_context:
        return None

    prefix = "original_developer:"
    for raw in quick_context.splitlines():
        line = raw.strip()
        if not line.startswith(prefix):
            continue
        dev_id = line[len(prefix) :].strip()
        if len(dev_id) == _UUID_LENGTH and dev_id.count("-") == _UUID_HYPHEN_COUNT:
            return dev_id
        return None
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
        audit_agent_id: str | UUID | None = None,
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
            audit_agent_id: Optional explicit agent_id for the audit row.
                Required for transitions where the caller has already
                cleared ``task.claimed_by`` BEFORE invoking this method
                (e.g. ``submit_for_qa`` clears claimed_by so QA can claim).
                Without this, the audit writer reads the now-cleared value
                and stores ``agent_id=NULL`` on rows that should attribute
                the transition to the developer, QA, or PM who triggered it.

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
        git_ctx = GitContext(
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

        # Poke the orchestrator's dispatcher so it reacts immediately to
        # this transition. Without this, agents wait up to 30 seconds for
        # the next poll (10 minutes cumulative in the pathological case we
        # saw on be-dev-1 spawn). Uses lazy import + silent fallback so
        # code paths that don't have an orchestrator (tests, sync tools)
        # don't break.
        try:
            from roboco.api.deps import get_orchestrator

            get_orchestrator().trigger_dispatch()
        except Exception as e:
            # Swallow so task creation succeeds even when the
            # orchestrator singleton isn't wired (e.g. tests, CLI
            # scripts) — but log so a real regression is visible.
            self.log.debug(
                "Dispatch trigger skipped after task create",
                error=str(e),
            )

        # Fire-and-forget audit write. Critical: we must hold a strong
        # reference to the Task object (via `_background_tasks`) — the event
        # loop only weak-refs tasks, so without this the audit write can be
        # garbage-collected before it commits. That's why audit_log was
        # coming up empty even though the log call ran.
        import asyncio
        import contextlib

        from roboco.services.audit import get_audit_service

        # Prefer the explicit `audit_agent_id` when the caller passed one
        # (capture-before-mutate pattern: callers like `submit_for_qa` and
        # `pass_qa` clear `task.claimed_by` BEFORE calling us so the next
        # role can claim, but still want the audit row attributed to the
        # outgoing agent). Fall back to `task.claimed_by` for transitions
        # where the assignment didn't change (claim, start_work, etc.).
        if audit_agent_id is not None:
            resolved_audit_agent_id: str | None = str(audit_agent_id)
        elif task.claimed_by is not None:
            resolved_audit_agent_id = str(task.claimed_by)
        else:
            resolved_audit_agent_id = None

        audit = get_audit_service()
        with contextlib.suppress(RuntimeError):
            bg = asyncio.get_running_loop().create_task(
                audit.log_task_event(
                    event_type=f"task.{target}",
                    task_id=str(task.id),
                    agent_id=resolved_audit_agent_id,
                    details={
                        "from_status": current,
                        "to_status": target,
                        "agent_role": agent_role,
                        "team": (
                            task.team.value
                            if hasattr(task.team, "value")
                            else str(task.team)
                        ),
                    },
                )
            )
            self._background_tasks.add(bg)
            bg.add_done_callback(self._background_tasks.discard)

    # =========================================================================
    # CRUD OPERATIONS
    # =========================================================================

    async def _validate_parent_depth(self, parent_task_id: UUID) -> None:
        """Enforce MAX_TASK_DEPTH at creation time.

        Walks up the parent chain counting ancestors. Raises ValueError if
        adding a child under this parent would exceed MAX_TASK_DEPTH.
        Previously this was only enforced at branch-name generation time,
        so invalid hierarchies could be created and only fail later at claim.
        """
        from roboco.templates.git.constants import MAX_TASK_DEPTH

        current_id: UUID | None = parent_task_id
        depth = 0
        visited: set[str] = set()
        while current_id is not None:
            key = str(current_id)
            if key in visited:
                raise ValueError(
                    f"Circular reference detected at {key} while validating depth"
                )
            visited.add(key)
            parent = await self.get(current_id)
            if parent is None:
                raise ValueError(f"Parent task {current_id} not found")
            depth += 1
            if depth >= MAX_TASK_DEPTH:
                raise ValueError(
                    f"Task hierarchy would exceed MAX_TASK_DEPTH={MAX_TASK_DEPTH}. "
                    "Create this work as a sibling of the deepest task instead "
                    "of a further nested subtask."
                )
            parent_parent = parent.parent_task_id
            current_id = UUID(str(parent_parent)) if parent_parent else None

    async def create(self, req: TaskCreateRequest) -> TaskTable:
        """
        Create a new task.

        Default status is PENDING. PM can pass status=BACKLOG when creating
        subtasks that need session setup before activation.
        """
        if req.parent_task_id:
            await self._validate_parent_depth(req.parent_task_id)

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
            # Git configuration (all tasks follow git workflow)
            task_type=req.task_type,
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

    async def activate(self, task_id: UUID, agent_role: str = "cell_pm") -> TaskTable:
        """
        Activate a task from BACKLOG to PENDING status.

        This is a PM-only operation that transitions a task from setup
        phase to ready-for-work phase. The orchestrator will then spawn
        agents to work on it.

        REQUIRES: Task must have at least one linked session.

        Args:
            task_id: The task to activate
            agent_role: Role of the agent performing activation (must be PM)

        Returns:
            The activated task

        Raises:
            ValueError: If task not found, not in BACKLOG, or has no session
            TaskLifecycleError: If role is not allowed to activate
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

        # ENFORCEMENT: All tasks require project_id (double-check here)
        if not task.project_id:
            raise ValueError(
                f"Cannot activate task '{task.title}' - no project set. "
                "All tasks require a project for git workflow."
            )

        # NOTE: Git branch is auto-created on claim, not required at activation

        # Transition to PENDING with role enforcement
        # (PM-only per ROLE_RESTRICTED_TRANSITIONS)
        self._validate_and_set_status(task, TaskStatus.PENDING, agent_role)
        await self.session.flush()

        self.log.info(
            "Task activated",
            task_id=str(task_id),
            session_id=str(session_link.session_id),
        )
        return task

    async def _ensure_branch_for_task(
        self,
        task: TaskTable,
        agent_id: UUID,
    ) -> str:
        """Auto-create hierarchical branch for task. Raises on failure.

        Strategy:
        - If branch exists: return it
        - If no project: raise error (should not happen - project_id is required)
        - Create NEW branch (hierarchical name built by build_branch_name)
        - Branch created from parent's branch (or default if root)

        Raises:
            ValueError: If branch cannot be created
        """
        if task.branch_name:
            return str(task.branch_name)

        if not task.project_id:
            raise ValueError(
                "Task requires project_id to create branch. "
                "Assign a project before claiming."
            )

        return await self._auto_create_branch(task, agent_id)

    async def _find_ancestor_branch(self, task: TaskTable) -> str | None:
        """Walk up task hierarchy to find nearest ancestor with a branch.

        This handles cases where immediate parent doesn't have a branch
        (e.g., planning tasks created by PMs that don't need branches).

        Returns:
            Branch name of nearest ancestor, or None if no ancestor has one.
        """
        current_parent_id = task.parent_task_id
        visited: set[str] = set()  # Prevent infinite loops

        while current_parent_id:
            parent_id_str = str(current_parent_id)
            if parent_id_str in visited:
                self.log.warning(
                    "Circular reference detected in task hierarchy",
                    task_id=str(task.id),
                    cycle_at=parent_id_str,
                )
                break
            visited.add(parent_id_str)

            parent = await self.get(UUID(parent_id_str))
            if not parent:
                break

            if parent.branch_name:
                self.log.info(
                    "Found ancestor branch",
                    task_id=str(task.id),
                    ancestor_id=parent_id_str,
                    branch=str(parent.branch_name),
                )
                return str(parent.branch_name)

            current_parent_id = parent.parent_task_id

        return None

    async def _resolve_parent_branch(self, task: TaskTable, project: Any) -> str:
        """Pick parent branch for a new task branch, fall back to project default."""
        parent_branch: str | None = None
        if task.parent_task_id:
            parent_branch = await self._find_ancestor_branch(task)

        if not parent_branch:
            default_branch = (
                str(project.default_branch) if project.default_branch else "main"
            )
            self.log.info(
                "No ancestor branch found, using project default",
                task_id=str(task.id),
                default_branch=default_branch,
            )
            parent_branch = default_branch
        return parent_branch

    @staticmethod
    def _resolve_team_dir(project: Any, task: TaskTable) -> str:
        """Pick the workspace team directory for a task's branch.

        Uses the TASK's team first (who is doing the work), then falls
        back to the project's assigned cell. This ensures a main-pm
        planning task gets a main_pm branch even if the project cell
        is backend.
        """
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
            return f"{project.slug}/{task_team or 'cross'}"
        if task_team:
            return task_team
        if project_cell:
            return project_cell
        return "cross"

    async def _auto_create_branch(
        self,
        task: TaskTable,
        agent_id: UUID,
    ) -> str:
        """Create hierarchical branch for git task. Raises on failure.

        Branch naming (via build_branch_name):
        - Root: feature/team/ROOT_ID
        - Subtask: feature/team/ROOT_ID--SUB_ID
        - Sub-subtask: feature/team/ROOT_ID--SUB_ID--SUBSUB_ID

        Uses '--' separator for task hierarchy to avoid git ref conflicts.

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

        parent_branch = await self._resolve_parent_branch(task, project)
        workspace = await git_service.get_workspace(project.slug, agent_id)
        team = self._resolve_team_dir(project, task)

        request = GitCreateBranchRequest(
            task_id=require_uuid(task.id),
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

    _CLAIMABLE_STATUSES: ClassVar[set[TaskStatus]] = {
        TaskStatus.PENDING,
        TaskStatus.AWAITING_QA,
        TaskStatus.AWAITING_DOCUMENTATION,
        TaskStatus.AWAITING_PM_REVIEW,
    }

    async def _validate_claim_preconditions(
        self,
        task: TaskTable,
        agent: AgentTable | None,
        agent_id: UUID,
        allow_reassign: bool,
    ) -> bool:
        """Run all per-claim validators; log + return False on failure."""
        valid_statuses = _get_valid_claim_statuses(agent, allow_reassign)

        if error := self._validate_claim_status(task, agent, valid_statuses):
            self.log.warning(f"Cannot claim task - {error}", task_id=str(task.id))
            return False

        if error := self._validate_claim_team(task, agent):
            self.log.warning(f"Cannot claim task - {error}", task_id=str(task.id))
            return False

        # Prevent pre-assigned theft: if the task is already assigned to a
        # DIFFERENT agent and the claimant is not allowed to reassign, reject.
        # PMs with allow_reassign=True can still take over (used for handoffs).
        if (
            task.assigned_to is not None
            and str(task.assigned_to) != str(agent_id)
            and not allow_reassign
        ):
            self.log.warning(
                "Cannot claim task - assigned to another agent",
                task_id=str(task.id),
                assigned_to=str(task.assigned_to),
                requesting_agent=str(agent_id),
            )
            return False

        if error := self._validate_not_self_review(task, agent, agent_id):
            self.log.warning(f"Cannot claim task - {error}", task_id=str(task.id))
            return False
        return True

    async def _finalize_claim(
        self,
        task: TaskTable,
        agent: AgentTable | None,
        agent_id: UUID,
    ) -> None:
        """
        Apply claim-side-effects: status transition, branch + work session + context.

        On branch-creation failure, the claim fields are rolled back to
        their pre-claim values so a retry starts from a clean state. Without
        this, a partial failure leaves the task CLAIMED with branch_name=NULL,
        and `git checkout -b` on retry fails non-idempotent (audit S-01/D-39).
        """
        # Set context for QA/Documenter claims (only if not already set)
        self._set_original_developer_context(task, agent)

        # Snapshot for rollback on branch-creation failure.
        original_status = task.status
        original_assigned_to = task.assigned_to
        original_claimed_by = task.claimed_by
        original_claimed_at = task.claimed_at
        original_heartbeat = task.last_heartbeat_at
        original_claimant_id = task.active_claimant_id

        now = datetime.now(UTC)
        task.assigned_to = cast("Any", agent_id)
        task.claimed_by = cast("Any", agent_id)
        task.claimed_at = now
        # Seed the heartbeat at claim time. The reaper treats
        # last_heartbeat_at IS NULL as stale; without this seed, a
        # freshly-claimed task is reaped on the next dispatch tick
        # (~250ms) before the agent has a chance to call any verb that
        # would touch the heartbeat — leading to an unclaim/reclaim
        # tight loop hammering the orchestrator.
        task.last_heartbeat_at = now
        # Single-claimant invariant (alembic 006): claimant_lock.try_acquire
        # and trigger_filter.decide_spawn both branch on this column. Was
        # declared but never written (audit D-05); now wired so the
        # invariant is functional.
        task.active_claimant_id = cast("Any", agent_id)

        agent_role = agent.role.value if agent and agent.role else None
        if task.status in self._CLAIMABLE_STATUSES:
            self._validate_and_set_status(task, TaskStatus.CLAIMED, agent_role)

        await self.session.flush()

        if not task.branch_name:
            try:
                await self._ensure_branch_for_task(task, agent_id)
            except Exception:
                # Roll back claim fields so the task is reclaimable.
                task.status = original_status
                task.assigned_to = original_assigned_to
                task.claimed_by = original_claimed_by
                task.claimed_at = original_claimed_at
                task.last_heartbeat_at = original_heartbeat
                task.active_claimant_id = original_claimant_id
                await self.session.flush()
                raise
            await self.session.refresh(task)

        await self._create_work_session_if_needed(task, agent_id, agent_role)

        bg_task = asyncio.create_task(self._inject_proactive_context(task, agent_id))
        self._background_tasks.add(bg_task)
        bg_task.add_done_callback(self._background_tasks.discard)

    async def claim(
        self, task_id: UUID, agent_id: UUID, allow_reassign: bool = False
    ) -> TaskTable | None:
        """
        Claim a task for an agent.

        Role-based claiming:
        - Developers/PMs: can claim PENDING tasks
        - QA: can claim AWAITING_QA tasks
        - Documenters: can claim PENDING (direct assignment) or AWAITING_DOCUMENTATION

        Uses SELECT ... FOR UPDATE to serialize concurrent claim attempts on
        the same task, preventing last-write-wins races between two agents
        racing for the same pending task.
        """
        # Lock the task row for the duration of this transaction so concurrent
        # claim attempts serialize at the DB level. `with_for_update(of=...)`
        # scopes the lock to tasks only — otherwise, because TaskTable.project
        # is lazy="joined", SA emits an outer join and Postgres rejects
        # `FOR UPDATE` on the nullable side with FeatureNotSupportedError.
        lock_result = await self.session.execute(
            select(TaskTable)
            .where(TaskTable.id == task_id)
            .with_for_update(of=TaskTable)
        )
        task = lock_result.scalar_one_or_none()
        if not task:
            return None

        agent_result = await self.session.execute(
            select(AgentTable).where(AgentTable.id == agent_id)
        )
        agent = agent_result.scalar_one_or_none()

        if not await self._validate_claim_preconditions(
            task, agent, agent_id, allow_reassign
        ):
            return None

        await self._finalize_claim(task, agent, agent_id)
        return task

    async def _inject_proactive_context(self, task: TaskTable, agent_id: UUID) -> None:
        """Inject proactive knowledge context when task is claimed.

        Runs as a background task with its own DB session. Pre-fix the
        outer claim() transaction could roll back (branch-creation
        failure, FOR UPDATE conflict, etc.) but this fire-and-forget
        survived and wrote stale context onto a task whose claim was
        reverted (audit D-44).

        Now performs a confirm-after-commit check at the top: re-reads
        the task in a fresh session and skips if (a) task is gone, or
        (b) the claim is no longer held by ``agent_id``. Outer rollback
        clears ``assigned_to``, so this guard is enough to avoid stale
        writes; it also fires correctly under successful commits because
        a fresh read sees the post-commit state.
        """
        from uuid import UUID as PyUUID

        from roboco.db.base import get_session_factory
        from roboco.services.proactive import get_proactive_service

        task_id = PyUUID(str(task.id))
        task_title = task.title
        task_description = task.description or ""

        try:
            session_factory = get_session_factory()
            async with session_factory() as session:
                fresh = await session.get(TaskTable, task_id)
                if fresh is None or fresh.assigned_to != agent_id:
                    self.log.debug(
                        "skipping proactive context — claim was rolled back"
                        " or task is gone",
                        task_id=str(task_id),
                    )
                    return

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
        Create a WorkSession when a developer claims a task.

        Only creates a session if:
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

    def _duration_learning(
        self,
        task_title: str | None,
        started_at: Any,
        completed_at: Any,
        estimated_complexity: Any,
    ) -> tuple[str, Any] | None:
        """Emit an INSIGHT learning when duration diverges from complexity."""
        from roboco.services.learning import LearningType

        if not (started_at and completed_at):
            return None
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
            return msg, LearningType.INSIGHT
        if ratio < self._DURATION_UNDER_RATIO:
            msg = (
                f"Task '{task_title}' ({complexity_val}) completed "
                f"quickly in {duration_hours:.1f}h."
            )
            return msg, LearningType.INSIGHT
        return None

    def _commit_pattern_learning(
        self, task_title: str | None, commits: list[Any]
    ) -> tuple[str, Any] | None:
        """Emit a PATTERN/GOTCHA learning based on commit count."""
        from roboco.services.learning import LearningType

        if len(commits) >= self._MIN_COMMITS_GOOD:
            msg = f"Good commit granularity on '{task_title}': {len(commits)}."
            return msg, LearningType.PATTERN
        if len(commits) == 1:
            return (
                f"Single commit on '{task_title}'. Try smaller increments.",
                LearningType.GOTCHA,
            )
        return None

    def _collect_completion_learnings(
        self,
        snapshot: _CompletionSnapshot,
    ) -> list[tuple[str, Any]]:
        """Gather all auto-extractable learnings from a completed task."""
        from roboco.services.learning import LearningType

        learnings: list[tuple[str, Any]] = []
        duration = self._duration_learning(
            snapshot.task_title,
            snapshot.started_at,
            snapshot.completed_at,
            snapshot.estimated_complexity,
        )
        if duration:
            learnings.append(duration)

        commit_pattern = self._commit_pattern_learning(
            snapshot.task_title, snapshot.commits
        )
        if commit_pattern:
            learnings.append(commit_pattern)

        if snapshot.dev_notes and len(snapshot.dev_notes) > self._MIN_NOTES_LENGTH:
            learnings.append(
                (
                    f"[DEV NOTES] {snapshot.task_title}: {snapshot.dev_notes[:500]}",
                    LearningType.SOLUTION,
                )
            )
        if snapshot.qa_notes and len(snapshot.qa_notes) > self._MIN_NOTES_LENGTH:
            learnings.append(
                (
                    f"[QA FEEDBACK] {snapshot.task_title}: {snapshot.qa_notes[:500]}",
                    LearningType.REVIEW_FEEDBACK,
                )
            )
        return learnings

    async def _extract_completion_learnings(
        self, task: TaskTable, agent_id: UUID | None
    ) -> None:
        """Extract and record learnings from a completed task (fire-and-forget)."""
        from roboco.services.learning import (
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
            scope = self._determine_learning_scope(task_team)
            learnings = self._collect_completion_learnings(
                _CompletionSnapshot(
                    task_title=task_title,
                    started_at=started_at,
                    completed_at=completed_at,
                    estimated_complexity=estimated_complexity,
                    commits=commits,
                    dev_notes=dev_notes,
                    qa_notes=qa_notes,
                )
            )
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
        from pathlib import Path

        from roboco.services.docs import DOCS_BASE_PATH
        from roboco.services.optimal import get_optimal_service

        try:
            optimal = await get_optimal_service()

            # Extract doc paths from documents array and resolve to absolute paths
            doc_paths: list[str] = []
            for d in documents:
                rel_path = d.get("path")
                if rel_path:
                    absolute_path = str(DOCS_BASE_PATH / Path(rel_path))
                    doc_paths.append(absolute_path)

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
        from uuid import NAMESPACE_URL, uuid5

        from roboco.models.optimal import IndexJournalEntryParams
        from roboco.services.optimal import get_optimal_service

        try:
            optimal = await get_optimal_service()
            details = details or {}

            # Build content for indexing
            content = f"[{event_type.upper()}] {task_title}"
            if details:
                content += f"\nDetails: {details}"

            # Lifecycle events have no journal-entry row of their own. Derive
            # a deterministic synthetic UUID from (task, event, timestamp)
            # so the index_journal_entry source is meaningful and unique
            # per event — never the literal "None" that the old fallback
            # produced.
            now_iso = datetime.now(UTC).isoformat()
            synthetic_entry_id = uuid5(
                NAMESPACE_URL,
                f"roboco-lifecycle/{task_id}/{event_type}/{now_iso}",
            )

            # Index to journals for lifecycle tracking
            await optimal.index_journal_entry(
                IndexJournalEntryParams(
                    content=content,
                    entry_id=synthetic_entry_id,
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
        self,
        task_id: UUID,
        agent_id: UUID | None = None,
        agent_role: str | None = None,
    ) -> TaskTable | None:
        """
        Start working on a task.

        Args:
            task_id: The task to start
            agent_id: Optional agent ID to validate ownership
            agent_role: Optional agent role for transition validation

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
        self._validate_and_set_status(task, TaskStatus.IN_PROGRESS, agent_role)
        await self.session.flush()
        return task

    async def heartbeat(self, task_id: UUID) -> None:
        """Touch ``last_heartbeat_at`` to mark the claimant as alive.

        Called from gateway verb entry points so a dead container's
        claim becomes recoverable after the heartbeat TTL expires.
        No-op if the task does not exist — the UPDATE simply matches zero
        rows. The column is ``DateTime(timezone=True)`` so we write a
        timezone-aware UTC value to match the schema and avoid SA's naive-
        vs-aware mismatch warning.
        """
        await self.session.execute(
            update(TaskTable)
            .where(TaskTable.id == task_id)
            .values(last_heartbeat_at=datetime.now(UTC))
        )

    async def list_in_progress_or_claimed(self) -> list[TaskTable]:
        """All tasks currently in claimed or in_progress state.

        Used by the orchestrator's stale-claim reaper to find rows whose
        holder may have gone silent. Returns the bare row set; the reaper
        applies the heartbeat-TTL filter in Python because the cutoff is a
        runtime decision tied to settings, not a column.
        """
        result = await self.session.execute(
            select(TaskTable).where(
                TaskTable.status.in_([TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS])
            )
        )
        return list(result.scalars().all())

    async def unclaim_for_reaper(self, task_id: UUID) -> None:
        """Reaper-only unclaim: skip role checks, force the row back to pending.

        Routes through ``_validate_and_set_status`` (audit P2-4/D-20) so the
        canonical state machine in ``enforcement/task_lifecycle.py`` records
        the transition. Pre-fix this used raw UPDATE which bypassed
        VALID_TRANSITIONS — making the lifecycle module's invariants diverge
        from production reality.

        Also abandons the active WorkSession so a re-claim by the same
        agent doesn't trip the uniqueness constraint at
        ``WorkSessionService.create`` (audit D-41). Best-effort: if the
        WorkSession lookup fails for any reason, the task is still
        rolled back to pending.

        The operation is named with ``_for_reaper`` so callers cannot
        accidentally use it as a regular unclaim path; uses ``agent_role=None``
        because the system itself is performing the transition. Bypasses
        ownership/role checks because the holder is provably dead (no
        heartbeat past TTL).
        """
        task = await self.get(task_id)
        if task is None:
            return
        if task.status not in (TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS):
            return
        try:
            self._validate_and_set_status(task, TaskStatus.PENDING, None)
        except TaskLifecycleError:
            return
        if task.work_session_id:
            await self._abandon_work_session_best_effort(
                task.work_session_id, reason="reaper-unclaim"
            )
            task.work_session_id = cast("Any", None)
        task.assigned_to = cast("Any", None)
        task.last_heartbeat_at = None
        task.active_claimant_id = cast("Any", None)
        await self.session.flush()

    async def _abandon_work_session_best_effort(
        self, session_id: Any, *, reason: str
    ) -> None:
        """Mark a WorkSession ABANDONED. Logs and continues on any failure.

        Audit D-41 fix — unclaim must not leave ACTIVE WorkSessions
        behind, but a service-layer error here mustn't block the task-
        side unclaim from completing.
        """
        try:
            from roboco.services.work_session import (
                WorkSessionService,
            )

            ws_service = WorkSessionService(self.session)
            await ws_service.abandon(UUID(str(session_id)), reason=reason)
        except Exception as exc:
            self.log.warning(
                "abandon WorkSession failed; continuing",
                session_id=str(session_id),
                reason=reason,
                error=str(exc),
            )

    async def unclaim_for_agent(
        self, task_id: UUID, agent_id: UUID
    ) -> TaskTable | None:
        """Voluntary unclaim by the current claimant.

        Distinct from ``unclaim_for_reaper`` (which the orchestrator's
        stale-claim sweeper calls when the holder is provably dead): this
        path is the agent itself releasing the lock. Returns ``None`` and
        makes no write when:

        - the task does not exist
        - the requesting agent is not the current claimant
        - the task status is not claimed/in_progress
        - the lifecycle layer rejects the transition (defense-in-depth
          against future ``VALID_TRANSITIONS``/``ROLE_RESTRICTED_TRANSITIONS``
          changes; the choreographer pre-checks status today)

        On success, clears ``assigned_to`` and transitions the row back to
        ``pending`` so another agent (or the same one, fresh) can pick it
        up. The work-in-progress branch is preserved — only the claim is
        released.
        """
        task = await self.get(task_id)
        if task is None or task.assigned_to != agent_id:
            return None
        if task.status not in (TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS):
            return None

        # Look up the requesting agent's role so role-restricted-transition
        # rules apply if/when claimed→pending or in_progress→pending ever
        # gain restrictions. Mirrors the pattern in `_finalize_claim`.
        agent_result = await self.session.execute(
            select(AgentTable).where(AgentTable.id == agent_id)
        )
        agent = agent_result.scalar_one_or_none()
        agent_role = agent.role.value if agent and agent.role else None

        # Route through the single point of truth for status transitions.
        # _validate_and_set_status raises TaskLifecycleError on invalid
        # transition or role; treat that as a clean rejection (return None)
        # so the choreographer's existing "invalid_state" envelope still
        # fires instead of a 500 leaking out.
        try:
            self._validate_and_set_status(task, TaskStatus.PENDING, agent_role)
        except TaskLifecycleError:
            return None

        # _validate_and_set_status only updates `status`; clearing the
        # claim is the unclaim's specific side effect. Also abandon the
        # active WorkSession so a re-claim doesn't trip the uniqueness
        # constraint (audit D-41).
        if task.work_session_id:
            await self._abandon_work_session_best_effort(
                task.work_session_id, reason="agent-unclaim"
            )
            task.work_session_id = cast("Any", None)
        task.assigned_to = cast("Any", None)
        task.active_claimant_id = cast("Any", None)
        await self.session.flush()
        return task

    async def resume_for_agent(self, task_id: UUID, agent_id: UUID) -> TaskTable | None:
        """Voluntary resume: transition paused task → in_progress for the assignee.

        Distinct from ``resume`` (which takes only ``agent_role`` and is
        called by closure-dispatcher / management code paths): this path
        enforces that ``agent_id`` is the current claimant. Returns ``None``
        and makes no write when:

        - the task does not exist
        - the requesting agent is not the current assignee
        - the task status is not paused
        - the lifecycle layer rejects the transition (defense-in-depth
          against future ``VALID_TRANSITIONS``/``ROLE_RESTRICTED_TRANSITIONS``
          changes; the choreographer pre-checks status today)

        Delegates to ``resume`` after the gateway-specific ownership/state
        pre-checks so we inherit its structlog "Task resumed" event and the
        fire-and-forget RAG lifecycle-event indexing — gateway-driven resumes
        must remain visible to logs and the RAG corpus. Mirrors
        ``pause_for_agent``'s delegation pattern.
        """
        task = await self.get(task_id)
        if task is None or task.assigned_to != agent_id:
            return None
        if task.status != TaskStatus.PAUSED:
            return None

        # Look up the requesting agent's role so role-restricted-transition
        # rules apply if/when paused→in_progress ever gains restrictions.
        # Mirrors the pattern in `unclaim_for_agent` and `_finalize_claim`.
        agent_result = await self.session.execute(
            select(AgentTable).where(AgentTable.id == agent_id)
        )
        agent = agent_result.scalar_one_or_none()
        agent_role = agent.role.value if agent and agent.role else None

        # `resume` calls `_validate_and_set_status` internally, which raises
        # TaskLifecycleError on invalid transition or role. Treat that as a
        # clean rejection (return None) so the choreographer's
        # "invalid_state" envelope still fires instead of a 500 leaking out.
        try:
            return await self.resume(task_id, agent_role)
        except TaskLifecycleError:
            return None

    async def block(
        self,
        task_id: UUID,
        blocker_task_id: UUID,
        agent_role: str | None = None,
    ) -> TaskTable | None:
        """Block a task due to a dependency.

        Args:
            task_id: The task to block
            blocker_task_id: The task causing the block
            agent_role: Role of agent performing the block (for validation)
        """
        task = await self.get(task_id)
        if not task:
            return None

        if blocker_task_id not in task.dependency_ids:
            new_deps = [*task.dependency_ids, blocker_task_id]
            task.dependency_ids = new_deps
        # Task-dependency blocks always resolve when the blocker task
        # completes — that's inherently an agent-resolvable condition.
        task.blocker_resolver_type = BlockerResolverType.AGENT
        # Remember who was working this task so `unblock` can hand it
        # back. Dependency blocks don't reassign, but stash anyway so
        # the unblock path has a consistent source of truth.
        if task.assigned_to and not task.blocker_raised_by:
            task.blocker_raised_by = task.assigned_to
        self._validate_and_set_status(task, TaskStatus.BLOCKED, agent_role)
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
        info: SoftBlockInfo,
        agent_role: str | None = None,
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
            agent_role: Role of agent performing the block (for validation)

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
            f"[BLOCKED - {info.blocker_type.upper()}]\n"
            f"Reason: {info.reason}\n"
            f"What's needed: {info.what_needed}"
        )
        task.dev_notes = _append_capped(task.dev_notes, blocker_note)

        # Default resolver is AGENT — preserves pre-existing behavior where
        # the dispatcher would respawn. Caller passes HUMAN to tell the
        # dispatcher to stop churning and wait for HITL.
        task.blocker_resolver_type = info.resolver_type or BlockerResolverType.AGENT
        # Remember the raiser so `unblock` can restore the task to them.
        if task.assigned_to and not task.blocker_raised_by:
            task.blocker_raised_by = task.assigned_to
        self._validate_and_set_status(task, TaskStatus.BLOCKED, agent_role)
        await self.session.flush()

        # Index blocker as error pattern (fire-and-forget)
        blocker_info = {
            "type": info.blocker_type,
            "title": task.title,
            "reason": info.reason,
            "what_needed": info.what_needed,
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
            blocker_type=info.blocker_type,
            reason=info.reason,
        )
        return task

    async def unblock(
        self, task_id: UUID, agent_role: str | None = None
    ) -> TaskTable | None:
        """Unblock a task and hand it back to the agent who raised the block.

        If the block was an escalation, `apply_escalation` stashed the
        original dev's UUID in `blocker_raised_by`. We restore the
        assignment here (and clear the stash) so the orchestrator's
        dispatcher spawns the original agent on the next tick, rather
        than leaving the task in_progress on the resolver PM who is
        already parked waiting_long.

        Args:
            task_id: The task to unblock
            agent_role: Role of agent performing the unblock (for validation)
        """
        task = await self.get(task_id)
        if not task:
            return None

        if task.status != TaskStatus.BLOCKED:
            return None

        # Restore the raiser so the orchestrator dispatcher (which
        # includes `in_progress` tasks in its pickup list) respawns the
        # original agent — not the PM who merely resolved the block.
        if task.blocker_raised_by:
            task.assigned_to = cast("Any", task.blocker_raised_by)
            task.blocker_raised_by = None
        # Clear resolver metadata — only meaningful while BLOCKED.
        task.blocker_resolver_type = None
        self._validate_and_set_status(task, TaskStatus.IN_PROGRESS, agent_role)
        await self.session.flush()

        self.log.info(
            "Task unblocked",
            task_id=str(task_id),
            restored_assignee=(str(task.assigned_to) if task.assigned_to else None),
        )

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

    async def pause(
        self, task_id: UUID, agent_role: str | None = None
    ) -> TaskTable | None:
        """Pause a task.

        Args:
            task_id: The task to pause
            agent_role: Role of agent pausing the task (for validation)
        """
        task = await self.get(task_id)
        if not task:
            return None

        if task.status != TaskStatus.IN_PROGRESS:
            return None

        self._validate_and_set_status(task, TaskStatus.PAUSED, agent_role)
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

    async def resume(
        self, task_id: UUID, agent_role: str | None = None
    ) -> TaskTable | None:
        """Resume a paused task.

        Args:
            task_id: The task to resume
            agent_role: Role of agent resuming the task (for validation)
        """
        task = await self.get(task_id)
        if not task:
            return None

        if task.status != TaskStatus.PAUSED:
            return None

        self._validate_and_set_status(task, TaskStatus.IN_PROGRESS, agent_role)
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

    async def submit_for_verification(
        self, task_id: UUID, agent_role: str | None = None
    ) -> TaskTable | None:
        """Submit task for self-verification.

        Args:
            task_id: The task to verify
            agent_role: Role of agent submitting for verification (for validation)
        """
        task = await self.get(task_id)
        if not task:
            return None

        if task.status != TaskStatus.IN_PROGRESS:
            return None

        # self_verified is the dev's attestation that they've reviewed
        # their own work before handing to QA. Setting it here (rather
        # than later in submit_for_qa) means the submit-qa route's
        # NOT_SELF_VERIFIED gate has something to check against.
        task.self_verified = True
        self._validate_and_set_status(task, TaskStatus.VERIFYING, agent_role)
        await self.session.flush()

        self.log.info("Task submitted for verification", task_id=str(task_id))
        return task

    async def submit_for_qa(
        self, task_id: UUID, agent_role: str | None = None
    ) -> TaskTable | None:
        """Submit task for QA review.

        Args:
            task_id: The task to submit for QA
            agent_role: Role of agent submitting (for validation)
        """
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

        # Capture the developer's UUID BEFORE clearing claimed_by so the
        # `task.awaiting_qa` audit row is attributed to the dev who
        # submitted, not NULL. Capture-before-mutate per Audit I30.
        captured_dev_id = to_python_uuid(task.claimed_by)

        # Clear assignment so QA can claim the task
        # The original developer is preserved in quick_context
        task.assigned_to = None
        task.claimed_by = None
        task.self_verified = True
        self._validate_and_set_status(
            task,
            TaskStatus.AWAITING_QA,
            agent_role,
            audit_agent_id=captured_dev_id,
        )
        await self.session.flush()

        self.log.info(
            "Task submitted for QA",
            task_id=str(task_id),
            original_developer=original_dev,
        )
        return task

    async def pass_qa(
        self, task_id: UUID, notes: str | None = None, agent_role: str = "qa"
    ) -> TaskTable | None:
        """Mark task as passed QA.

        QA workflow: awaiting_qa → claimed → in_progress → pass_qa
        → awaiting_documentation. Accept claimed/in_progress status.

        Args:
            task_id: The task to pass
            notes: Optional QA notes
            agent_role: Role of agent passing QA (must be 'qa')
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

        # Capture the QA agent's UUID BEFORE clearing claimed_by so the
        # `task.awaiting_documentation` audit row is attributed to QA,
        # not NULL. Capture-before-mutate per Audit I30.
        captured_qa_id = to_python_uuid(task.claimed_by)

        # Clear assignment so documenter can claim the task
        task.assigned_to = None
        task.claimed_by = None
        task.qa_verified = True
        # Reset docs so the documenter writes fresh docs for this cycle.
        # DO NOT reset pr_created — the PR exists pre-QA under the current
        # workflow (see CLAUDE.md: "awaiting_documentation | PR already
        # open from pre-QA"). Clearing it here falsely signaled "no PR"
        # to `_maybe_advance_to_pm_review` and left tasks stuck in CLAIMED
        # after docs_complete, since the parallel-completion gate never
        # saw both flags together.
        task.docs_complete = False
        # Use validated transition - QA role required per ROLE_RESTRICTED_TRANSITIONS
        self._validate_and_set_status(
            task,
            TaskStatus.AWAITING_DOCUMENTATION,
            agent_role,
            audit_agent_id=captured_qa_id,
        )
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

    async def fail_qa(
        self, task_id: UUID, notes: str, agent_role: str = "qa"
    ) -> TaskTable | None:
        """
        Mark task as failed QA and reassign to original developer.

        When QA fails a task, it goes back to the original developer for revision.
        The original developer is extracted from quick_context which stores
        "original_developer:{uuid}" when the task was submitted to QA.

        QA workflow: awaiting_qa → claimed → in_progress → fail_qa → needs_revision
        So we need to accept tasks in claimed or in_progress status.

        Args:
            task_id: The task to fail
            notes: QA notes explaining why it failed
            agent_role: Role of agent failing the task (must be 'qa')
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
        # Use validated transition - QA role required per ROLE_RESTRICTED_TRANSITIONS
        self._validate_and_set_status(task, TaskStatus.NEEDS_REVISION, agent_role)

        # Store QA agent before reassigning
        qa_agent_id = task.assigned_to

        # Reassign to original developer so they can work on revisions
        original_dev = extract_original_developer(task.quick_context)
        if original_dev:
            task.assigned_to = cast("Any", UUID(original_dev))
            task.claimed_by = cast("Any", UUID(original_dev))
            self.log.info(
                "Task reassigned to original developer for revision",
                task_id=str(task_id),
                original_developer=original_dev,
            )
        else:
            # If no original developer found, unassign so it can be claimed
            task.assigned_to = None
            task.claimed_by = None
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

        if not self._validate_docs_complete_status(task, task_id):
            return None
        if not await self._validate_docs_complete_descendants(task_id):
            return None

        self._record_doc_notes(task, doc_notes)
        task.docs_complete = True
        self._record_documenter_context(task)
        await self._maybe_advance_to_pm_review(task, task_id)

        await self.session.flush()

        # Index documentation artifacts (fire-and-forget)
        if task.documents:
            bg_task = asyncio.create_task(
                self._index_docs_background(require_uuid(task.id), task.documents)
            )
            self._background_tasks.add(bg_task)
            bg_task.add_done_callback(self._background_tasks.discard)

        return task

    _DOCS_COMPLETE_VALID_STATUSES: ClassVar[set[TaskStatus]] = {
        TaskStatus.AWAITING_DOCUMENTATION,
        TaskStatus.CLAIMED,
        TaskStatus.IN_PROGRESS,
    }

    def _validate_docs_complete_status(self, task: TaskTable, task_id: UUID) -> bool:
        """Reject docs_complete attempts from wrong task statuses."""
        if task.status not in self._DOCS_COMPLETE_VALID_STATUSES:
            self.log.warning(
                "Cannot mark docs complete - invalid status for documenter workflow",
                task_id=str(task_id),
                current_status=task.status.value,
            )
            return False
        return True

    async def _validate_docs_complete_descendants(self, task_id: UUID) -> bool:
        """Refuse docs_complete while any descendant is still live."""
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
            return False
        return True

    @staticmethod
    def _record_doc_notes(task: TaskTable, doc_notes: str | None) -> None:
        """Append doc_notes into quick_context if supplied."""
        if not doc_notes:
            return
        task.quick_context = _append_capped(
            task.quick_context, f"doc_notes:{doc_notes}"
        )

    @staticmethod
    def _record_documenter_context(task: TaskTable) -> None:
        """Stamp documenter id into quick_context if missing."""
        if not task.assigned_to:
            return
        existing_context = task.quick_context or ""
        if "documenter:" in existing_context:
            return
        doc_context = f"documenter:{task.assigned_to}"
        task.quick_context = (
            f"{existing_context}\n{doc_context}".strip()
            if existing_context
            else doc_context
        )

    async def _resolve_pm_for_review(self, task: TaskTable) -> UUID | None:
        """Walk up the parent chain to find the PM who owns this work.

        A dev subtask's parent is the Cell PM's planning task; that PM is
        who should own the review. Main-PM-level tasks (no parent or
        parent's assignee is Main-PM) route to Main-PM. Returns None if
        nothing useful is found — caller falls back to leaving the task
        unassigned for scan-claim.
        """
        parent_id = to_python_uuid(task.parent_task_id)
        while parent_id:
            parent = await self.get(parent_id)
            if not parent:
                return None
            candidate = to_python_uuid(parent.assigned_to)
            if candidate:
                return candidate
            parent_id = to_python_uuid(parent.parent_task_id)
        return None

    async def _maybe_advance_to_pm_review(self, task: TaskTable, task_id: UUID) -> None:
        """If doc+PR gates both pass, promote to awaiting_pm_review.

        On advance, assign the task to the PM up the parent chain
        (Cell PM for a dev's subtask; Main PM for a Cell-PM task).
        Leaving `assigned_to` null forced PMs to scan-and-claim, which
        added ~1 dispatcher round-trip and occasionally stalled when
        the target PM was already parked waiting_long.
        """
        from roboco.enforcement.task_lifecycle import check_parallel_completion

        ready_for_pm = check_parallel_completion(
            docs_complete=True,
            pr_created=task.pr_created,
        )
        if ready_for_pm:
            self._validate_and_set_status(
                task, TaskStatus.AWAITING_PM_REVIEW, "documenter"
            )
            owning_pm = await self._resolve_pm_for_review(task)
            task.assigned_to = cast("Any", owning_pm) if owning_pm else None
            self.log.info(
                "Documentation complete, awaiting PM review",
                task_id=str(task_id),
                pr_created=task.pr_created,
                routed_to_pm=str(owning_pm) if owning_pm else None,
            )
        else:
            self.log.info(
                "Documentation complete, waiting for developer to create PR",
                task_id=str(task_id),
                docs_complete=True,
                pr_created=task.pr_created,
            )

    async def mark_pr_created(
        self,
        task_id: UUID,
        pr_number: int,
        pr_url: str,
    ) -> TaskTable | None:
        """
        Mark that developer has created a PR for the task.

        Called by the choreographer when the developer's submit_for_qa()
        flow opens the PR. This method:
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

        # Accept statuses valid for the parallel phase. awaiting_documentation
        # is the "pool"; claimed/in_progress happen when doc or dev claims+
        # starts their own task during the parallel phase (status shifts
        # out of awaiting_documentation but the PR-creation side is still
        # the dev's responsibility). Without accepting these, the flag
        # setter refuses and the task stays stuck with pr_created=false
        # forever — the dispatcher then respawns the dev in a loop.
        # Also accept verifying/awaiting_qa/needs_revision because the PR
        # may be created (or re-created) during those states.
        allowed_statuses = {
            TaskStatus.AWAITING_DOCUMENTATION,
            TaskStatus.AWAITING_QA,
            TaskStatus.CLAIMED,
            TaskStatus.IN_PROGRESS,
            TaskStatus.NEEDS_REVISION,
            TaskStatus.VERIFYING,
        }
        if task.status not in allowed_statuses:
            self.log.warning(
                "Cannot mark PR created - task status outside parallel phase",
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
        )

        if ready_for_pm:
            # Both conditions met - transition to PM review using proper validation
            self._validate_and_set_status(
                task, TaskStatus.AWAITING_PM_REVIEW, "developer"
            )
            # Clear assignment so PM can claim the task for review
            task.assigned_to = None
            task.claimed_by = None
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

    def _validate_submit_review_status(self, task: TaskTable, task_id: UUID) -> bool:
        """Task must be in_progress with branch + PR; otherwise log + False."""
        if task.status != TaskStatus.IN_PROGRESS:
            self.log.warning(
                "Cannot submit for PM review - task not in progress",
                task_id=str(task_id),
                current_status=task.status.value,
            )
            return False
        if not task.branch_name:
            self.log.warning(
                "Cannot submit for PM review - no branch (claim task first)",
                task_id=str(task_id),
            )
            return False
        if not task.pr_created or not task.pr_number:
            self.log.warning(
                "Cannot submit for PM review - PR must be created first",
                task_id=str(task_id),
                pr_created=task.pr_created,
                pr_number=task.pr_number,
            )
            return False
        return True

    async def _validate_submit_review_descendants(self, task_id: UUID) -> bool:
        """Parent tasks can't submit for review while children are live."""
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
            return False
        return True

    @staticmethod
    def _record_completion_notes(task: TaskTable, notes: str | None) -> None:
        """Append completion_notes entry to quick_context when supplied."""
        if not notes:
            return
        existing_context = task.quick_context or ""
        note_entry = f"completion_notes:{notes}"
        task.quick_context = (
            f"{existing_context}\n{note_entry}".strip()
            if existing_context
            else note_entry
        )

    async def submit_for_pm_review(
        self,
        task_id: UUID,
        agent_role: str = "cell_pm",
        notes: str | None = None,
    ) -> TaskTable | None:
        """
        Submit a task directly for PM review (PM, QA, or Documenter only).

        Use this for tasks that don't follow the standard dev→QA→docs workflow,
        such as PM validation tasks, QA audit tasks, or other directly-assigned work.
        Even these tasks must have a branch and PR created to maintain git workflow.

        Transitions task from IN_PROGRESS to AWAITING_PM_REVIEW.

        Note: Only PM roles, QA, and Documenter can use this method (not developers).

        Args:
            task_id: The task to submit
            agent_role: Role of the agent submitting (must be PM, QA, or documenter)
            notes: Optional completion notes

        Returns:
            The updated task or None if not allowed
        """
        task = await self.get(task_id)
        if not task:
            return None

        if not self._validate_submit_review_status(task, task_id):
            return None
        if not await self._validate_submit_review_descendants(task_id):
            return None

        self._record_completion_notes(task, notes)
        self._validate_and_set_status(task, TaskStatus.AWAITING_PM_REVIEW, agent_role)
        await self.session.flush()

        self.log.info(
            "Task submitted for PM review",
            task_id=str(task_id),
            agent_role=agent_role,
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
            select(AgentTable)
            .where(AgentTable.role == AgentRole.MAIN_PM)
            .order_by(AgentTable.created_at)
            .limit(1)
        )
        main_pm = main_pm_result.scalar_one_or_none()
        if not main_pm:
            self.log.warning(
                "No Main PM found - proceeding with completion", task_id=str(task_id)
            )
            return None

        task.assigned_to = cast("Any", main_pm.id)
        task.claimed_by = cast("Any", main_pm.id)
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

    async def _apply_complete_approval_chain(
        self,
        task: TaskTable,
        task_id: UUID,
        agent_id: UUID | None,
        completing_agent_role: str | None,
        all_descendants: list[TaskTable],
    ) -> TaskTable | None:
        """Run the Cell PM → Main PM → CEO chain; return escalated task or None."""
        if task.status != TaskStatus.AWAITING_PM_REVIEW:
            return None

        if completing_agent_role == "cell_pm":
            escalated = await self._handle_cell_pm_escalation(task, task_id, agent_id)
            if escalated:
                return escalated
        is_root_parent = all_descendants and not task.parent_task_id
        if completing_agent_role == "main_pm" and is_root_parent:
            self.log.info(
                "Main PM approved root parent - escalating to CEO",
                task_id=str(task_id),
            )
            return await self.escalate_to_ceo(task_id, "main_pm")
        return None

    async def _assert_pr_merged_for_complete(self, task: TaskTable) -> bool:
        """True if the task's PR is merged (or no PR gate applies).

        Non-root tasks must have their PR merged before a PM can mark
        them completed — mirrors the CEO-approve guard but applies to
        the PM's own awaiting_pm_review → completed transition.
        Root parent tasks and tasks without a work_session skip this
        check (escalation to CEO handles them).
        """
        if not task.work_session_id:
            return True
        result = await self.session.execute(
            select(WorkSessionTable).where(WorkSessionTable.id == task.work_session_id)
        )
        ws = result.scalar_one_or_none()
        if ws is None or ws.pr_status == "merged":
            return True
        self.log.warning(
            "Cannot complete - PR must be merged first",
            task_id=str(task.id),
            pr_status=ws.pr_status,
            pr_number=ws.pr_number,
        )
        return False

    @staticmethod
    def _cancelled_force_allowed(
        all_descendants: list[TaskTable],
        force_with_cancelled: bool,
        justification: str | None,
    ) -> bool:
        """Return True if any cancelled descendants are acceptable to the PM."""
        has_cancelled = any(st.status == TaskStatus.CANCELLED for st in all_descendants)
        if not has_cancelled:
            return True
        return force_with_cancelled and bool(justification)

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

        escalated = await self._apply_complete_approval_chain(
            task, task_id, agent_id, completing_agent_role, all_descendants
        )
        if escalated:
            return escalated

        if not self._cancelled_force_allowed(
            all_descendants, force_with_cancelled, justification
        ):
            self.log.warning(
                "Cannot complete - cancelled descendants", task_id=str(task_id)
            )
            return None

        if not await self._assert_pr_merged_for_complete(task):
            return None

        task.completed_at = datetime.now(UTC)
        self._validate_and_set_status(
            task, TaskStatus.COMPLETED, completing_agent_role or "cell_pm"
        )
        await self._close_work_session_for_task(task, reason="task completed")
        await self.session.flush()

        await self._trigger_completion_hooks(task, agent_id)
        await self._unblock_dependents(task_id)
        return task

    async def apply_escalation(
        self,
        *,
        task: TaskTable,
        target_agent_id: UUID,
        escalator_slug: str,
        target_slug: str,
        reason: str,
    ) -> None:
        """Apply the state mutations for a generic chain escalation.

        Sets the task to BLOCKED, reassigns to the escalation target, and
        appends an [ESCALATED] line to dev_notes. Notification delivery is
        handled upstream by `NotificationDeliveryService.escalate_and_notify`.

        Records the pre-escalation assignee as `blocker_raised_by` so the
        subsequent `unblock` call hands the task back to the original dev
        and the orchestrator re-spawns them. Without this, escalation
        loses the dev's identity permanently.
        """
        if task.assigned_to and not task.blocker_raised_by:
            task.blocker_raised_by = cast("Any", task.assigned_to)
        task.assigned_to = cast("Any", target_agent_id)
        task.claimed_by = cast("Any", target_agent_id)
        task.status = TaskStatus.BLOCKED
        existing_notes = task.dev_notes or ""
        escalation_note = (
            f"\n\n[ESCALATED] From {escalator_slug} to {target_slug}\nReason: {reason}"
        )
        task.dev_notes = existing_notes + escalation_note
        await self.session.flush()
        self.log.info(
            "Task escalated and blocked",
            task_id=str(task.id),
            escalator=escalator_slug,
            target=target_slug,
        )

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

        # ENFORCEMENT: Tasks must have PR created before CEO approval
        if not task.pr_number:
            self.log.warning(
                "Cannot escalate to CEO - task has no PR",
                task_id=str(task_id),
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
        PR must be merged before approval (CEO merges as final action).

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

        # Verify PR is merged (CEO merges as final action before approving)
        if task.work_session_id:
            work_session_result = await self.session.execute(
                select(WorkSessionTable).where(
                    WorkSessionTable.id == task.work_session_id
                )
            )
            work_session = work_session_result.scalar_one_or_none()
            if work_session and work_session.pr_status != "merged":
                self.log.warning(
                    "Cannot CEO approve - PR must be merged first",
                    task_id=str(task_id),
                    pr_status=work_session.pr_status,
                    pr_number=work_session.pr_number,
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
            task.claimed_by = cast("Any", UUID(original_dev))
            self.log.info(
                "Task reassigned to original developer after CEO rejection",
                task_id=str(task_id),
                original_developer=original_dev,
            )
        else:
            # Clear assignment so it can be claimed
            task.assigned_to = None
            task.claimed_by = None

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

    async def _abandon_work_session_for_task(
        self, task: TaskTable, reason: str
    ) -> None:
        """Mark the task's active work session as abandoned, if any.

        Without this, cancelled tasks leave their WorkSessionTable row in
        ACTIVE status forever, polluting list_active_sessions queries.
        """
        if not task.work_session_id:
            return
        from roboco.services.work_session import get_work_session_service

        ws_service = get_work_session_service(self.session)
        await ws_service.abandon(require_uuid(task.work_session_id), reason=reason)

    async def _delete_task_branch_best_effort(self, task: TaskTable) -> None:
        """Delete the task's remote branch on cancel. Never raises.

        Skipped for tasks that didn't make it to a branch yet, or whose
        PR already merged (merge path deletes the source branch).
        """
        branch = task.branch_name
        if not branch:
            return
        try:
            project_result = await self.session.execute(
                select(ProjectTable.slug).where(ProjectTable.id == task.project_id)
            )
            project_slug = project_result.scalar_one_or_none()
            if not project_slug:
                return
            from roboco.services.git import get_git_service

            git_service = get_git_service(self.session)
            await git_service.delete_task_branch(project_slug, str(branch))
        except Exception as e:
            # Cleanup is best-effort — don't fail the cancel if the
            # remote is unreachable or the branch is already gone.
            self.log.warning(
                "Branch cleanup skipped",
                task_id=str(task.id),
                branch=str(branch),
                error=str(e),
            )

    async def _close_work_session_for_task(self, task: TaskTable, reason: str) -> None:
        """Close the task's work session on successful completion.

        `abandon()` is for cancellation (work was discarded). `close()` is
        for successful completion — the PR is merged and we want the
        session marked completed rather than abandoned so reporting can
        distinguish the two outcomes.
        """
        if not task.work_session_id:
            return
        from roboco.services.work_session import get_work_session_service

        ws_service = get_work_session_service(self.session)
        await ws_service.close(require_uuid(task.work_session_id), reason=reason)

    async def cancel(
        self,
        task_id: UUID,
        agent_role: str = "cell_pm",
        cancellation_note: str | None = None,
    ) -> TaskTable | None:
        """Cancel a task and all its descendants (PM only).

        If `cancellation_note` is supplied it's appended to `dev_notes` so
        the audit trail captures who cancelled and why — keeps this out of
        route handlers.
        """
        task = await self.get(task_id)
        if not task:
            return None

        if cancellation_note:
            task.dev_notes = (
                f"{task.dev_notes}\n{cancellation_note}"
                if task.dev_notes
                else cancellation_note
            )
            await self.session.flush()

        # Cancel all descendants first (children, grandchildren, etc.)
        # Skip tasks already in terminal states (completed or cancelled).
        # Route every descendant through _validate_and_set_status so role
        # restrictions (e.g., only CEO can cancel awaiting_ceo_approval) still
        # apply to cascaded cancels — skip descendants that fail validation
        # rather than bypassing the rules.
        descendants = await self.get_all_descendants(task_id)
        cancelled_count = 0
        for descendant in descendants:
            if descendant.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
                continue
            try:
                self._validate_and_set_status(
                    descendant, TaskStatus.CANCELLED, agent_role
                )
            except Exception as e:
                self.log.warning(
                    "Skipping cascade-cancel of descendant; role not permitted",
                    descendant_id=str(descendant.id),
                    descendant_status=descendant.status.value,
                    agent_role=agent_role,
                    error=str(e),
                )
                continue
            cancelled_count += 1
            await self._abandon_work_session_for_task(
                descendant, reason="parent task cancelled"
            )
            await self._delete_task_branch_best_effort(descendant)

        if cancelled_count > 0:
            self.log.info(
                "Cascaded cancel to descendants",
                task_id=str(task_id),
                cancelled_count=cancelled_count,
            )

        # Validate transition with PM role requirement
        self._validate_and_set_status(task, TaskStatus.CANCELLED, agent_role)
        await self._abandon_work_session_for_task(task, reason="task cancelled")
        await self._delete_task_branch_best_effort(task)
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
            # If no more dependencies, unblock (system action - no role validation)
            if not task.dependency_ids and task.status == TaskStatus.BLOCKED:
                self._validate_and_set_status(task, TaskStatus.IN_PROGRESS, None)
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

    async def list_strategic_for_board(self) -> list[TaskTable]:
        """Root tasks (no parent) in awaiting_pm_review with strategic nature.

        Strategic = non-technical roots: product strategy, marketing, vision —
        the work the Board (Product Owner, Head Marketing) curates before
        escalating to CEO. The codebase models nature as a binary
        TECHNICAL/NON_TECHNICAL split, so non_technical is the strategic-board
        bucket.
        """
        query = (
            select(TaskTable)
            .where(
                TaskTable.parent_task_id.is_(None),
                TaskTable.status == TaskStatus.AWAITING_PM_REVIEW,
                TaskTable.nature == TaskNature.NON_TECHNICAL,
            )
            .order_by(
                TaskTable.priority,
                TaskTable.sequence,
                TaskTable.created_at,
            )
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def list_long_running_blocked(
        self, *, threshold_minutes: int = 30
    ) -> list[TaskTable]:
        """Tasks in 'blocked' state whose updated_at is older than threshold_minutes.

        Surfaces anomalies for the Auditor to observe. Most-stale first, ordered by
        updated_at ascending so the oldest blocker is at the head of the list.
        """
        cutoff = datetime.now(UTC) - timedelta(minutes=threshold_minutes)
        query = (
            select(TaskTable)
            .where(
                TaskTable.status == TaskStatus.BLOCKED,
                TaskTable.updated_at.is_not(None),
                TaskTable.updated_at < cutoff,
            )
            .order_by(
                TaskTable.updated_at,
                TaskTable.priority,
                TaskTable.created_at,
            )
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

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

    async def resolve_agent_id(self, agent_id_str: str) -> UUID:
        """Resolve a UUID-string-or-slug into an agent UUID.

        Used by claim-style endpoints where callers may pass either form.
        Raises NotFoundError if the slug doesn't exist. Exists on the service
        so route modules never issue raw AgentTable queries.
        """
        try:
            return UUID(agent_id_str)
        except ValueError:
            pass

        result = await self.session.execute(
            select(AgentTable.id).where(AgentTable.slug == agent_id_str)
        )
        agent_uuid = result.scalar_one_or_none()
        if not agent_uuid:
            raise NotFoundError(resource_type="Agent", resource_id=agent_id_str)
        return UUID(str(agent_uuid))

    # =========================================================================
    # ROUTE-LEVEL ORCHESTRATION
    #
    # These methods encapsulate the full flow for each task-lifecycle HTTP
    # endpoint. Routes stay thin adapters: parse input → call one of these
    # → translate ServiceError → format response. Business logic and
    # notifications live here.
    # =========================================================================

    # Minimum character count for notes fields that must be substantive
    # (QA pass notes, doc-complete notes, escalation notes).
    MIN_NOTES_CHARS: ClassVar[int] = 20
    # Max descendant IDs shown inline in an error before truncating.
    MAX_ERR_IDS: ClassVar[int] = 5
    # Substitute reason → target status table.
    _SUBSTITUTE_REASON_TO_STATUS: ClassVar[dict[str, TaskStatus]] = {
        "task_complete": TaskStatus.AWAITING_QA,
        "low_context": TaskStatus.PENDING,
        "out_of_scope_team": TaskStatus.PENDING,
        "out_of_scope_role": TaskStatus.PENDING,
        "max_retries": TaskStatus.PENDING,
        "blocked_external": TaskStatus.BLOCKED,
    }

    async def _load_task_or_raise(self, task_id: UUID) -> TaskTable:
        task = await self.get(task_id)
        if not task:
            raise NotFoundError(resource_type="Task", resource_id=str(task_id))
        return task

    @staticmethod
    def _task_status_value(task: TaskTable) -> str:
        return task.status.value if hasattr(task.status, "value") else str(task.status)

    async def claim_task_for_agent(
        self,
        task_id: UUID,
        agent: AgentContext,
        permissions: "PermissionService",
        claim_target_slug: str | None,
    ) -> TaskTable:
        """Claim a task (self or, for privileged agents, on behalf of another)."""
        task = await self._load_task_or_raise(task_id)

        if not permissions.can_perform_task_action(agent, TaskAction.CLAIM, task.team):
            raise UnauthorizedError(
                action="claim", reason="Not authorized to claim tasks"
            )

        # QA / Documenter cannot claim what they themselves developed.
        if agent.role in (AgentRole.QA, AgentRole.DOCUMENTER):
            original_dev = extract_original_developer(task.quick_context)
            if original_dev and str(agent.agent_id) == original_dev:
                raise UnauthorizedError(
                    action="claim",
                    reason=(
                        "SELF_REVIEW: Cannot claim a task that you developed. "
                        f"Leave it for another {agent.role.value}."
                    ),
                )

        can_assign = permissions.can_perform_task_action(
            agent, TaskAction.ASSIGN, task.team
        )
        claim_agent_id = agent.agent_id
        allow_reassign = False
        if claim_target_slug and can_assign:
            claim_agent_id = await self.resolve_agent_id(claim_target_slug)
            allow_reassign = True

        claimed = await self.claim(
            task_id, claim_agent_id, allow_reassign=allow_reassign
        )
        if not claimed:
            status_msg = "not pending or claimed" if allow_reassign else "not pending"
            raise ValidationError(f"Cannot claim task - {status_msg}")
        await self.session.commit()
        return claimed

    async def soft_block_task_for_agent(
        self,
        task_id: UUID,
        agent: AgentContext,
        request: SoftBlockInput,
    ) -> TaskTable:
        """Soft-block a task + notify the owning PM.

        Takes a domain DTO so the API's Pydantic schema doesn't leak into
        the service layer; `SoftBlockInfo` and `BlockerDetails` are built
        internally.
        """
        task = await self._load_task_or_raise(task_id)
        if task.assigned_to != agent.agent_id and agent.role not in (
            AgentRole.CELL_PM,
            AgentRole.MAIN_PM,
        ):
            raise UnauthorizedError(
                action="soft_block", reason="Not authorized to block this task"
            )

        # Coerce the raw string to the domain enum — fall back on AGENT
        # (agent-self-resolvable is the safest default).
        try:
            resolver = BlockerResolverType(request.resolver_type_raw)
        except ValueError:
            resolver = BlockerResolverType.AGENT
        info = SoftBlockInfo(
            reason=request.reason,
            blocker_type=request.blocker_type,
            what_needed=request.what_needed,
            resolver_type=resolver,
        )

        blocked = await self.soft_block(task_id, info, agent.role)
        if not blocked:
            raise ValidationError("Cannot block task - must be in_progress")

        from roboco.services.notification_delivery import (
            BlockerDetails,
            get_notification_delivery_service,
        )

        delivery = get_notification_delivery_service(self.session)
        await delivery.notify_pm_of_block(
            task=blocked,
            task_id=task_id,
            blocker_agent_id=agent.agent_id,
            details=BlockerDetails(
                blocker_type=request.blocker_type,
                reason=request.reason,
                what_needed=request.what_needed,
            ),
        )
        await self.session.commit()
        return blocked

    async def docs_complete_for_task(
        self,
        task_id: UUID,
        agent: AgentContext,
        notes: str | None,
    ) -> TaskTable:
        """Mark documentation complete (documenter role only)."""
        task = await self._load_task_or_raise(task_id)

        if agent.role != AgentRole.DOCUMENTER:
            raise UnauthorizedError(
                action="docs_complete",
                reason="Only documenters can mark documentation as complete",
            )

        original_dev = extract_original_developer(task.quick_context)
        if original_dev and str(agent.agent_id) == original_dev:
            from roboco.services.audit import get_audit_service

            audit = get_audit_service()
            await audit.log_task_action_denial(
                agent_id=agent.agent_id,
                agent_role=agent.role.value,
                task_id=task_id,
                action="docs_complete",
                reason="Self-documentation not permitted",
            )
            raise UnauthorizedError(
                action="docs_complete", reason="Cannot document your own task"
            )

        if not notes or len(notes.strip()) < self.MIN_NOTES_CHARS:
            raise ValidationError(
                f"DOC_NOTES_REQUIRED: docs_complete must include notes "
                f"(>={self.MIN_NOTES_CHARS} chars) listing what was "
                "documented and where. "
                "Use roboco_task_docs_complete(notes='...')."
            )

        completed = await self.docs_complete(task_id, notes)
        if not completed:
            raise ValidationError(
                "Cannot mark docs complete - invalid status for documenter workflow"
            )

        from roboco.services.notification_delivery import (
            get_notification_delivery_service,
        )

        delivery = get_notification_delivery_service(self.session)
        await delivery.notify_pm_of_docs_complete(
            task=completed, task_id=task_id, submitter_agent_id=agent.agent_id
        )
        await self.session.commit()
        return completed

    def _format_id_list(self, ids: list[str]) -> str:
        shown = ", ".join(ids[: self.MAX_ERR_IDS])
        extra = (
            f" (+{len(ids) - self.MAX_ERR_IDS} more)"
            if len(ids) > self.MAX_ERR_IDS
            else ""
        )
        return f"{shown}{extra}"

    async def _explain_complete_failure(
        self, task_id: UUID, original_status: str
    ) -> str:
        """Diagnose why `complete` returned None, to surface a clear error."""
        refetch = await self.get(task_id)
        if refetch:
            descendants = await self.get_all_descendants(task_id)
            incomplete = [
                str(d.id)[:8]
                for d in descendants
                if self._task_status_value(d) not in ("completed", "cancelled")
            ]
            if incomplete:
                return (
                    f"Cannot complete task - {len(incomplete)} subtask(s) "
                    f"still in progress: {self._format_id_list(incomplete)}. "
                    "Monitor and help unblock stuck tasks."
                )
            if original_status not in ("awaiting_pm_review", "in_progress"):
                return (
                    f"Cannot complete - status is '{original_status}'. "
                    "Must be 'awaiting_pm_review' or 'in_progress'."
                )
        return "Cannot complete task - check task status and subtasks."

    async def complete_task_for_agent(
        self,
        task_id: UUID,
        agent: AgentContext,
        permissions: "PermissionService",
        force_with_cancelled: bool = False,
        justification: str | None = None,
    ) -> TaskTable:
        """Complete a task (PM/CEO). Includes self-approval + force gates."""
        task = await self._load_task_or_raise(task_id)

        if not permissions.can_perform_task_action(agent, TaskAction.CLOSE, task.team):
            raise UnauthorizedError(
                action="complete", reason="Only PMs can complete tasks"
            )

        # PM self-approval block: the PM who kicked off a task can't also
        # sign off at awaiting_pm_review. CEO is exempt.
        if (
            self._task_status_value(task) == "awaiting_pm_review"
            and task.created_by == agent.agent_id
            and agent.role != AgentRole.CEO
        ):
            raise UnauthorizedError(
                action="complete",
                reason=(
                    "SELF_APPROVAL: You created this task; a different PM "
                    "must complete it from awaiting_pm_review. Escalate or "
                    "hand off to another PM."
                ),
            )

        if force_with_cancelled:
            if agent.role != AgentRole.CEO:
                raise UnauthorizedError(
                    action="force_complete",
                    reason=(
                        "force_with_cancelled requires CEO approval. Only "
                        "CEO can complete tasks when subtasks are not all "
                        "completed."
                    ),
                )
            if not justification:
                raise ValidationError("force_with_cancelled requires justification")

        original_status = self._task_status_value(task)
        completed = await self.complete(
            task_id,
            agent_id=agent.agent_id,
            force_with_cancelled=force_with_cancelled,
            justification=justification,
        )
        if not completed:
            raise ValidationError(
                await self._explain_complete_failure(task_id, original_status)
            )
        await self.session.commit()
        return completed

    async def _validate_escalation_preconditions(
        self,
        task: TaskTable,
        task_id: UUID,
        agent: AgentContext,
        permissions: "PermissionService",
        notes: str | None,
    ) -> None:
        """Run all PR/permission/descendants/notes gates for escalate-to-CEO.

        Raises UnauthorizedError or ValidationError on failure; returns
        cleanly when every gate passes. Extracted from
        ``escalate_to_ceo_for_agent`` to keep that orchestrating method
        below B-rank cyclomatic complexity (audit P2-2 / xenon).
        """
        if not permissions.can_perform_task_action(agent, TaskAction.CLOSE, task.team):
            raise UnauthorizedError(
                action="escalate_to_ceo",
                reason="Only PMs can escalate tasks to CEO",
            )
        if task.pr_number is None:
            raise ValidationError(
                "NO_PR: Cannot escalate to CEO without an open PR. Ensure "
                "the PR exists and pr_number is set on the task."
            )
        if not task.pr_created:
            raise ValidationError(
                "PR_NOT_CONFIRMED: pr_created flag is false. The PR handler "
                "must confirm the PR exists before escalation."
            )
        descendants = await self.get_all_descendants(task_id)
        active = [
            d
            for d in descendants
            if self._task_status_value(d) not in ("completed", "cancelled")
        ]
        if active:
            ids_shown = self._format_id_list([str(d.id)[:8] for d in active])
            raise ValidationError(
                f"ACTIVE_SUBTASKS: Cannot escalate while {len(active)} "
                f"subtask(s) remain active: {ids_shown}."
            )
        if not notes or len(notes.strip()) < self.MIN_NOTES_CHARS:
            raise ValidationError(
                f"ESCALATION_NOTES_REQUIRED: Escalation to CEO must "
                f"include notes (>={self.MIN_NOTES_CHARS} chars) explaining "
                "why CEO review is needed (scope, risk, breaking-change, "
                "etc)."
            )

    async def escalate_to_ceo_for_agent(
        self,
        task_id: UUID,
        agent: AgentContext,
        permissions: "PermissionService",
        notes: str | None,
    ) -> TaskTable:
        """Escalate a task to CEO for final approval (PM-role, PR-gated)."""
        task = await self._load_task_or_raise(task_id)
        await self._validate_escalation_preconditions(
            task, task_id, agent, permissions, notes
        )

        escalated = await self.escalate_to_ceo(task_id, agent.role.value, notes)
        if not escalated:
            raise ValidationError(
                "Cannot escalate to CEO - task must be in awaiting_pm_review status"
            )

        from roboco.services.notification_delivery import (
            get_notification_delivery_service,
        )

        delivery = get_notification_delivery_service(self.session)
        await delivery.notify_ceo_of_escalation(
            task=escalated,
            task_id=task_id,
            escalator_agent_id=agent.agent_id,
            escalator_role=agent.role.value,
            notes=notes,
        )
        await self.session.commit()
        return escalated

    async def substitute_task_for_agent(
        self,
        task_id: UUID,
        agent: AgentContext,
        reason_raw: str,
        details: str,
    ) -> TaskTable:
        """Agent releases a task they can't continue; may route to PM review."""
        from roboco.models.base import SubstituteReason

        try:
            reason = SubstituteReason(reason_raw)
        except ValueError as e:
            valid = [r.value for r in SubstituteReason]
            raise ValidationError(
                f"Invalid reason: {reason_raw}. Valid: {valid}"
            ) from e

        task = await self._load_task_or_raise(task_id)
        if task.assigned_to != agent.agent_id:
            raise UnauthorizedError(
                action="substitute",
                reason="You can only substitute out of tasks you own",
            )

        # QA / doc `task_complete` routes to PM review; other reasons use
        # the plain mapping.
        new_status = self._SUBSTITUTE_REASON_TO_STATUS.get(
            reason.value, TaskStatus.PENDING
        )
        if reason == SubstituteReason.TASK_COMPLETE and agent.role in (
            AgentRole.QA,
            AgentRole.DOCUMENTER,
        ):
            new_status = TaskStatus.AWAITING_PM_REVIEW

        update_data, target_pm_slug = await self.build_substitute_update(
            agent_id=agent.agent_id,
            task=task,
            new_status=new_status,
            reason=reason_raw,
            details=details,
        )

        updated = await self.update(task_id, **update_data)
        if not updated:
            raise ServiceError("Update failed")

        if new_status == TaskStatus.AWAITING_PM_REVIEW and target_pm_slug:
            await notify_pm_for_substitute(
                self.session,
                pm_slug=target_pm_slug,
                task_id=task_id,
                from_agent_id=agent.agent_id,
                message=(
                    f"Task needs review: {updated.title or 'Unknown task'}",
                    f"Task {task_id} requires PM review.\n\n"
                    f"Reason: {reason.value}\n"
                    f"Details: {details}\n\n"
                    "Please review and reassign as needed.",
                ),
            )

        await self.session.commit()
        return updated

    async def build_substitute_update(
        self,
        *,
        agent_id: UUID,
        task: TaskTable,
        new_status: TaskStatus,
        reason: str,
        details: str,
    ) -> tuple[dict[str, Any], str | None]:
        """Assemble the update payload + pm_slug for a substitute operation.

        Resolves the submitting agent's slug (for PM-chain lookup) internally
        so routes pass only the agent UUID + primitive strings (no API
        schema types leak down into the service layer). Returns the patch
        dict and the PM slug to notify (None if no handoff).
        """
        result = await self.session.execute(
            select(AgentTable).where(AgentTable.id == agent_id)
        )
        agent_record = result.scalar_one_or_none()
        agent_slug = agent_record.slug if agent_record else None

        update_data: dict[str, Any] = {
            "status": new_status.value,
            "dev_notes": f"[SUBSTITUTE] Reason: {reason}\n{details}",
            "assigned_to": None,
        }

        target_pm_slug: str | None = None
        if new_status == TaskStatus.AWAITING_PM_REVIEW:
            target_pm_slug, pm_uuid = await resolve_pm_for_substitute(
                self.session, agent_slug, task.team
            )
            if pm_uuid:
                update_data["assigned_to"] = pm_uuid
        return update_data, target_pm_slug

    # =========================================================================
    # GATEWAY (CHOREOGRAPHER) BACKFILL
    #
    # Thin wrappers + queries the gateway Choreographer composes into
    # intent-verb sequences. Most are aliases over canonical service
    # methods; a handful (qa_claim, doc_claim, qa_pass, qa_fail, escalate,
    # cell_pm_complete) are flow-specific variants that don't fit cleanly
    # into the existing API.
    # =========================================================================

    # Active states a developer counts as "current work" for an agent.
    _DEV_ACTIVE_STATUSES: ClassVar[set[TaskStatus]] = {
        TaskStatus.CLAIMED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.VERIFYING,
        TaskStatus.AWAITING_QA,
        TaskStatus.AWAITING_DOCUMENTATION,
    }

    # Statuses that count as "still assignable to the agent" for triage.
    _AGENT_NON_TERMINAL_STATUSES: ClassVar[set[TaskStatus]] = {
        TaskStatus.PENDING,
        TaskStatus.CLAIMED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.NEEDS_REVISION,
        TaskStatus.VERIFYING,
        TaskStatus.AWAITING_QA,
        TaskStatus.AWAITING_DOCUMENTATION,
        TaskStatus.AWAITING_PM_REVIEW,
        TaskStatus.PAUSED,
    }

    async def submit_verification(
        self, agent_id: UUID, task_id: UUID, notes: str
    ) -> TaskTable | None:
        """Submit task for self-verification (gateway alias of submit_for_verification).

        `notes` is currently advisory — recorded as a progress entry for
        the audit trail. The underlying transition does not require notes.
        """
        if notes:
            await self.add_progress(task_id, agent_id, notes)
        return await self.submit_for_verification(task_id, agent_role="developer")

    async def submit_qa(
        self, agent_id: UUID, task_id: UUID, notes: str
    ) -> TaskTable | None:
        """Submit task for QA review (gateway alias of submit_for_qa)."""
        if notes:
            await self.add_progress(task_id, agent_id, notes)
        return await self.submit_for_qa(task_id, agent_role="developer")

    async def list_blocked_for_team(self, team: Team) -> list[TaskTable]:
        """List blocked tasks for a single team."""
        return await self.list_blocked(team=team)

    async def list_blocked_all_teams(self) -> list[TaskTable]:
        """List blocked tasks across all teams."""
        return await self.list_blocked()

    async def list_awaiting_pm_review_for_team(self, team: Team) -> list[TaskTable]:
        """List awaiting-PM-review tasks for a single team."""
        return await self.list_awaiting_pm_review(team=team)

    async def list_assigned_for_agent(self, agent_id: UUID) -> list[TaskTable]:
        """Active (non-terminal) tasks currently assigned to an agent."""
        query = (
            select(TaskTable)
            .where(
                TaskTable.assigned_to == agent_id,
                TaskTable.status.in_(self._AGENT_NON_TERMINAL_STATUSES),
            )
            .order_by(TaskTable.priority, TaskTable.updated_at.desc())
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def agent_for(self, agent_id: UUID) -> GatewayAgentView | None:
        """Return a gateway-shaped view of the agent (DB + config derived).

        Combines AgentTable's role/team with `agents_config`'s
        escalation_target + skills so the Choreographer reads one object.
        """
        from roboco.agents_config import (
            get_agent_skills,
            get_escalation_target,
        )

        result = await self.session.execute(
            select(AgentTable).where(AgentTable.id == agent_id)
        )
        agent = result.scalar_one_or_none()
        if agent is None:
            return None
        slug = agent.slug
        role_value = (
            agent.role.value if hasattr(agent.role, "value") else str(agent.role)
        )
        team_value: str | None = None
        if agent.team is not None:
            team_value = (
                agent.team.value if hasattr(agent.team, "value") else str(agent.team)
            )
        return GatewayAgentView(
            id=UUID(str(agent.id)),
            role=role_value,
            team=team_value,
            escalation_target=get_escalation_target(slug),
            skills=get_agent_skills(slug),
        )

    async def _agent_with_role_and_team(
        self, role: AgentRole, team: Team
    ) -> AgentTable | None:
        """Find an agent matching role + team."""
        result = await self.session.execute(
            select(AgentTable).where(
                AgentTable.role == role,
                AgentTable.team == team,
            )
        )
        return result.scalars().first()

    async def qa_agent_for_team(self, team: Team) -> AgentTable | None:
        """Find the QA agent for a team."""
        return await self._agent_with_role_and_team(AgentRole.QA, team)

    async def documenter_for_team(self, team: Team) -> AgentTable | None:
        """Find the Documenter agent for a team."""
        return await self._agent_with_role_and_team(AgentRole.DOCUMENTER, team)

    async def cell_pm_for_team(self, team: Team) -> AgentTable | None:
        """Find the Cell PM for a team."""
        return await self._agent_with_role_and_team(AgentRole.CELL_PM, team)

    async def main_pm_agent(self) -> AgentTable | None:
        """Find the Main PM (org-wide; takes the earliest-created if many)."""
        result = await self.session.execute(
            select(AgentTable)
            .where(AgentTable.role == AgentRole.MAIN_PM)
            .order_by(AgentTable.created_at)
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_active_task_for_agent(self, agent_id: UUID) -> TaskTable | None:
        """Most-recently-updated task currently being worked by the agent."""
        query = (
            select(TaskTable)
            .where(
                TaskTable.assigned_to == agent_id,
                TaskTable.status.in_(self._DEV_ACTIVE_STATUSES),
            )
            .order_by(TaskTable.updated_at.desc().nullslast())
            .limit(1)
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def list_pending_for_agent(self, agent_id: UUID) -> list[TaskTable]:
        """Tasks assigned to this agent that are still in PENDING status.

        Pre-gateway parity (Wave B6, 2026-05-12): give_me_work missed the
        pre-assigned case before this. PMs whose root was seeded with
        assigned_to=<them> + status=pending got 'no work' until they
        triage()'d explicitly.

        Ordered by sequence asc, then priority asc, then created_at asc so
        earlier-sequence tasks win.
        """
        query = (
            select(TaskTable)
            .where(
                TaskTable.assigned_to == agent_id,
                TaskTable.status == TaskStatus.PENDING,
            )
            .order_by(
                TaskTable.sequence,
                TaskTable.priority,
                TaskTable.created_at,
            )
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def list_paused_for_agent(self, agent_id: UUID) -> list[TaskTable]:
        """Paused tasks assigned to the agent."""
        query = (
            select(TaskTable)
            .where(
                TaskTable.assigned_to == agent_id,
                TaskTable.status == TaskStatus.PAUSED,
            )
            .order_by(TaskTable.priority, TaskTable.updated_at.desc())
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def list_awaiting_main_pm_all(self) -> list[TaskTable]:
        """Root tasks (no parent) awaiting PM review across all teams.

        Used by Main PM triage — root tasks have escalated past their
        cell PMs and need final approval/escalation to CEO.
        """
        query = (
            select(TaskTable)
            .where(
                TaskTable.parent_task_id.is_(None),
                TaskTable.status == TaskStatus.AWAITING_PM_REVIEW,
            )
            .order_by(
                TaskTable.priority,
                TaskTable.sequence,
                TaskTable.created_at,
            )
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def all_subtasks_terminal(self, task_id: UUID) -> bool:
        """True iff every direct subtask is in a terminal status.

        Empty subtask list returns True (no children = vacuously terminal).
        """
        terminal = {TaskStatus.COMPLETED, TaskStatus.CANCELLED}
        result = await self.session.execute(
            select(TaskTable.status).where(TaskTable.parent_task_id == task_id)
        )
        statuses = result.scalars().all()
        return all(s in terminal for s in statuses)

    async def set_plan(
        self, task_id: UUID, plan: str | dict[str, Any]
    ) -> TaskTable | None:
        """Write the task's plan field. Strings are wrapped as {'text': plan}."""
        task = await self.get(task_id)
        if not task:
            return None
        task.plan = plan if isinstance(plan, dict) else {"text": plan}
        await self.session.flush()
        return task

    async def ensure_work_session(
        self,
        task_id: UUID,
        agent_id: UUID,
    ) -> None:
        """Create a WorkSession row if one does not already exist for this claim.

        Wave C4 (2026-05-12) — pre-gateway parity. The gateway's claim/plan/
        start path calls this after the task reaches in_progress so every
        (agent, task) claim cycle has a WorkSession row that downstream
        subsystems (panel, PR tracking, merge chain) can use. Delegates to
        _create_work_session_if_needed with role=None so both developers and
        PMs get a session (pre-gateway created sessions for all claimants).
        No-ops if work_session_id is already set (re-entry guard).
        """
        task = await self.get(task_id)
        if not task:
            return
        if task.work_session_id:
            return
        await self._create_work_session_if_needed(task, agent_id, agent_role=None)

    async def mark_evidence_inspected(self, task_id: UUID) -> None:
        """Set qa_evidence_inspected=True on the task."""
        task = await self.get(task_id)
        if task is None:
            return
        task.qa_evidence_inspected = True
        await self.session.flush()

    async def reassign(
        self, task_id: UUID, new_assignee: UUID | None
    ) -> TaskTable | None:
        """Set ``task.assigned_to`` (and ``claimed_by``) to ``new_assignee``.

        Used by the gateway choreographer to hand a task off to the agent
        that should drive the next lifecycle stage (e.g. dev → qa, qa →
        documenter, doc → cell_pm). Pass ``None`` to clear assignment so
        no agent gets respawned (e.g. after escalating to CEO, who acts
        via the UI).

        Returns the refreshed task, or None if the task no longer exists.
        """
        task = await self.get(task_id)
        if task is None:
            return None
        task.assigned_to = cast("Any", new_assignee) if new_assignee else None
        task.claimed_by = cast("Any", new_assignee) if new_assignee else None
        await self.session.flush()
        self.log.info(
            "Task reassigned",
            task_id=str(task_id),
            new_assignee=str(new_assignee) if new_assignee else None,
        )
        return task

    async def mark_agent_idle(self, agent_id: UUID) -> None:
        """Set agent.status = IDLE."""
        result = await self.session.execute(
            select(AgentTable).where(AgentTable.id == agent_id)
        )
        agent = result.scalar_one_or_none()
        if agent is None:
            return
        agent.status = AgentStatus.IDLE
        await self.session.flush()

    async def _qa_or_doc_claim(
        self,
        agent_id: UUID,
        task_id: UUID,
        expected_status: TaskStatus,
    ) -> TaskTable | None:
        """Claim-without-transition for QA / Documenter review states.

        Status stays at expected_status (it's a review state, not an
        active dev state). Sets assigned_to + claimed_by + claimed_at so
        the gateway can route subsequent verbs to the right agent.
        """
        task = await self.get(task_id)
        if task is None:
            return None
        if task.status != expected_status:
            return None
        now = datetime.now(UTC)
        task.assigned_to = cast("Any", agent_id)
        task.claimed_by = cast("Any", agent_id)
        task.claimed_at = now
        # Seed the heartbeat — same rationale as _finalize_claim line 959.
        # `claimant_lock.is_stale` and the reaper both treat
        # last_heartbeat_at IS NULL as stale; without this seed a QA/Doc
        # claim is "stale" the moment it's recorded and any code that
        # consults claimant_lock for awaiting_qa / awaiting_documentation
        # tasks will misclassify the live claim as abandoned.
        task.last_heartbeat_at = now
        # Single-claimant invariant — see _finalize_claim. Same column
        # used by claimant_lock + trigger_filter. Cleared by QA pass/fail
        # and doc-complete when the review hand-off finishes.
        task.active_claimant_id = cast("Any", agent_id)
        await self.session.flush()
        return task

    async def qa_claim(self, qa_agent_id: UUID, task_id: UUID) -> TaskTable | None:
        """QA claims a task in awaiting_qa (no state transition)."""
        return await self._qa_or_doc_claim(qa_agent_id, task_id, TaskStatus.AWAITING_QA)

    async def doc_claim(self, doc_agent_id: UUID, task_id: UUID) -> TaskTable | None:
        """Documenter claims a task in awaiting_documentation."""
        return await self._qa_or_doc_claim(
            doc_agent_id, task_id, TaskStatus.AWAITING_DOCUMENTATION
        )

    async def qa_pass(
        self, qa_agent_id: UUID, task_id: UUID, notes: str
    ) -> TaskTable | None:
        """QA passes the task (gateway-flavored wrapper of pass_qa).

        The audit row is attributed to QA via task.claimed_by (set by
        qa_claim). We assert qa_agent_id matches claimed_by so any future
        divergence surfaces loudly instead of silently mis-recording the
        actor (audit D-18). Clears the single-claimant lock so the
        documenter can claim cleanly.
        """
        task = await self.get(task_id)
        if task is not None:
            if (
                task.claimed_by is not None
                and to_python_uuid(task.claimed_by) != qa_agent_id
            ):
                self.log.warning(
                    "qa_pass actor mismatch",
                    task_id=str(task_id),
                    qa_agent_id=str(qa_agent_id),
                    claimed_by=str(task.claimed_by),
                )
            task.active_claimant_id = cast("Any", None)
            await self.session.flush()
        return await self.pass_qa(task_id, notes=notes, agent_role="qa")

    async def qa_fail(
        self,
        qa_agent_id: UUID,
        task_id: UUID,
        notes: str,
        issues: list[str],
    ) -> TaskTable | None:
        """QA fails the task with concrete issues.

        `notes` is the QA narrative (stored on `qa_notes`); `issues` is
        appended to `dev_notes` as a checklist for the dev's revision.
        Asserts the actor matches claimed_by (audit D-18).
        """
        task = await self.get(task_id)
        if task is None:
            return None
        if (
            task.claimed_by is not None
            and to_python_uuid(task.claimed_by) != qa_agent_id
        ):
            self.log.warning(
                "qa_fail actor mismatch",
                task_id=str(task_id),
                qa_agent_id=str(qa_agent_id),
                claimed_by=str(task.claimed_by),
            )
        if issues:
            issue_block = "[QA ISSUES]\n" + "\n".join(f"- {i}" for i in issues)
            task.dev_notes = _append_capped(task.dev_notes, issue_block)
        # Clear active_claimant_id — fail_qa transitions back to
        # needs_revision and reassigns to the original developer.
        task.active_claimant_id = cast("Any", None)
        await self.session.flush()
        return await self.fail_qa(task_id, notes=notes, agent_role="qa")

    async def unblock_with_restore(
        self,
        pm_agent_id: UUID,
        task_id: UUID,
        *,
        restore: bool,
    ) -> TaskTable | None:
        """PM unblocks a task; restore=True returns it to its pre-block state.

        Pre-block snapshot lives on `pre_block_state` /
        `pre_block_assignee` / `pre_block_metadata` (migration 006). When
        restore=True and a snapshot exists, the task returns to that exact
        state. Otherwise falls through to the legacy unblock() which
        moves the task to in_progress and hands it back to the original
        raiser.
        """
        del pm_agent_id  # gateway already validated PM authority
        task = await self.get(task_id)
        if task is None:
            return None
        if not restore or not task.pre_block_state:
            return await self.unblock(task_id, agent_role="cell_pm")

        if task.status != TaskStatus.BLOCKED:
            return None

        try:
            restored_status = TaskStatus(task.pre_block_state)
        except ValueError:
            return await self.unblock(task_id, agent_role="cell_pm")

        task.status = restored_status
        if task.pre_block_assignee:
            task.assigned_to = cast("Any", task.pre_block_assignee)
            task.claimed_by = cast("Any", task.pre_block_assignee)
        task.pre_block_state = None
        task.pre_block_assignee = None
        task.pre_block_metadata = None
        task.blocker_resolver_type = None
        task.blocker_raised_by = None
        await self.session.flush()
        return task

    async def cell_pm_complete(
        self,
        pm_agent_id: UUID,
        task_id: UUID,
        notes: str,
        merge_commit: str | None = None,
    ) -> TaskTable | None:
        """Cell PM completes a task; records the parent-branch merge commit.

        Wraps the canonical complete() to also annotate the task with the
        merge commit SHA on its parent branch. Merge SHA is appended to
        `task.commits` as a synthetic entry tagged 'merge' so downstream
        PR-tracking surfaces it without a separate column.
        """
        task = await self.get(task_id)
        if task is None:
            return None
        if notes:
            self._record_completion_notes(task, notes)
        if merge_commit:
            merge_entry = {
                "hash": merge_commit,
                "message": f"[merge] PR for task {task_id}",
                "timestamp": datetime.now(UTC).isoformat(),
                "author_agent_id": str(pm_agent_id),
                "kind": "merge",
            }
            task.commits = [*task.commits, merge_entry]
            await self.session.flush()
        return await self.complete(task_id, agent_id=pm_agent_id)

    async def escalate(
        self, agent_id: UUID, task_id: UUID, reason: str
    ) -> TaskTable | None:
        """Escalate a task one rung up the agent's escalation chain.

        Looks up the escalation target via `agents_config.ESCALATION_CHAIN`,
        then applies the same state mutations as a chain escalation:
        reassigns the task to the target, marks BLOCKED, and stashes the
        raiser so a future unblock can restore the workflow.
        """
        from roboco.agents_config import get_escalation_target

        task = await self.get(task_id)
        if task is None:
            return None

        agent_result = await self.session.execute(
            select(AgentTable).where(AgentTable.id == agent_id)
        )
        agent = agent_result.scalar_one_or_none()
        if agent is None:
            return None

        target_slug = get_escalation_target(agent.slug)
        if not target_slug:
            return None

        target_result = await self.session.execute(
            select(AgentTable).where(AgentTable.slug == target_slug)
        )
        target = target_result.scalar_one_or_none()
        if target is None:
            return None

        await self.apply_escalation(
            task=task,
            target_agent_id=UUID(str(target.id)),
            escalator_slug=agent.slug,
            target_slug=target_slug,
            reason=reason,
        )
        return task

    async def escalate_up_to_role(
        self,
        agent_id: UUID,
        task_id: UUID,
        target_role: str,
        reason: str,
    ) -> TaskTable | None:
        """Escalate a task to an agent holding `target_role`.

        Picks the first agent matching the role; ties broken by created_at.
        Used when the escalation target is known by role rather than slug
        (e.g., 'main_pm' resolves to whichever agent currently holds the
        Main PM role).
        """
        task = await self.get(task_id)
        if task is None:
            return None

        agent_result = await self.session.execute(
            select(AgentTable).where(AgentTable.id == agent_id)
        )
        agent = agent_result.scalar_one_or_none()
        if agent is None:
            return None

        try:
            role_enum = AgentRole(target_role)
        except ValueError:
            return None

        target_result = await self.session.execute(
            select(AgentTable)
            .where(AgentTable.role == role_enum)
            .order_by(AgentTable.created_at)
            .limit(1)
        )
        target = target_result.scalar_one_or_none()
        if target is None:
            return None

        await self.apply_escalation(
            task=task,
            target_agent_id=UUID(str(target.id)),
            escalator_slug=agent.slug,
            target_slug=target.slug,
            reason=reason,
        )
        return task

    async def list_in_progress_for_agent(self, agent_id: UUID) -> list[TaskTable]:
        """In-progress tasks currently assigned to the agent."""
        query = (
            select(TaskTable)
            .where(
                TaskTable.assigned_to == agent_id,
                TaskTable.status == TaskStatus.IN_PROGRESS,
            )
            .order_by(TaskTable.priority, TaskTable.updated_at.desc())
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def pause_for_agent(
        self, agent_id: UUID, task_id: UUID, agent_role: str | None = None
    ) -> TaskTable | None:
        """Gateway-flavored pause: only succeeds when caller owns the task."""
        task = await self.get(task_id)
        if task is None or task.assigned_to != agent_id:
            return None
        return await self.pause(task_id, agent_role=agent_role)

    async def submit_pm_review(
        self, agent_id: UUID, task_id: UUID, notes: str
    ) -> TaskTable | None:
        """Gateway alias of submit_for_pm_review for cell-PM submit_up.

        Flushes any progress note then transitions in_progress →
        awaiting_pm_review with the agent's role inferred from agent_id so
        the lifecycle validator allows the transition.
        """
        if notes:
            await self.add_progress(task_id, agent_id, notes)
        agent = await self.agent_for(agent_id)
        agent_role = agent.role if agent else "cell_pm"
        return await self.submit_for_pm_review(
            task_id, agent_role=agent_role, notes=notes or None
        )

    async def create_subtask(self, req: TaskCreateRequest) -> TaskTable:
        """PM-friendly subtask creation; sets status from assignee presence.

        When ``assigned_to`` is provided the task is created in ``pending`` so
        the orchestrator can spawn the assignee immediately. Without an
        assignee it stays in ``backlog`` and a PM must run ``activate`` later.
        Caller-supplied status takes precedence; otherwise we infer from the
        presence of an assignee.

        Foundation rule: no task without acceptance_criteria. The silent
        fallback that substituted ``["completed and reviewed by assignee"]``
        was deleted on 2026-05-10 (spec §5.2) — it was the proximate cause
        of every skeleton task in the same-day smoke run. Defense-in-depth:
        the gateway and route-layer schemas reject under-filled tasks
        earlier, but this service-layer guard remains as a hard backstop
        for non-gateway / non-route callers.
        """
        from roboco.foundation.policy.task_completeness import (
            TASK_AT_CREATE,
            TaskCompletenessError,
            check,
        )

        if req.parent_task_id is None:
            raise ValueError("create_subtask requires parent_task_id")

        result = check(TASK_AT_CREATE, req)
        if not result.passed:
            raise TaskCompletenessError(
                missing=result.missing,
                field_hints=result.field_hints,
                message=(
                    "create_subtask: task missing required fields: "
                    f"{result.missing}. The silent fallback at "
                    "services/task.py:5061 was removed 2026-05-10 (spec §5.2)."
                ),
            )

        inferred_status = TaskStatus.PENDING if req.assigned_to else TaskStatus.BACKLOG
        prepared = TaskCreateRequest(
            title=req.title,
            description=req.description,
            acceptance_criteria=req.acceptance_criteria,
            team=req.team,
            created_by=req.created_by,
            project_id=req.project_id,
            parent_task_id=req.parent_task_id,
            assigned_to=req.assigned_to,
            estimated_complexity=req.estimated_complexity,
            task_type=req.task_type,
            nature=req.nature,
            status=req.status or inferred_status,
        )
        return await self.create(prepared)


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
