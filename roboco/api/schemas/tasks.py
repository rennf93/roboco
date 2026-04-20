"""
Tasks API Schemas

Request/response models for task endpoints, plus the conversion helpers
that build responses from ORM rows and normalize update payloads.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select

from roboco.db.tables import ProjectTable, TaskTable, WorkSessionTable
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType, Team
from roboco.models.session import SessionScope
from roboco.utils.converters import require_uuid, to_python_uuid, to_python_uuid_list

# =============================================================================
# NESTED RESPONSE MODELS
# =============================================================================


class ProgressUpdateResponse(BaseModel):
    """A progress update on a task."""

    timestamp: datetime
    agent_id: UUID
    message: str
    percentage: int | None = None


class CheckpointResponse(BaseModel):
    """A saved state checkpoint for task recovery."""

    id: UUID
    timestamp: datetime
    agent_id: UUID
    state_summary: str
    remaining_work: list[str] = []
    notes: str | None = None


class CommitRefResponse(BaseModel):
    """Reference to a git commit."""

    hash: str
    message: str
    timestamp: datetime
    author_agent_id: UUID | None = None


class TaskSessionLinkResponse(BaseModel):
    """A session linked to this task."""

    session_id: UUID
    channel_slug: str
    scope: SessionScope
    is_primary: bool
    relationship_type: str


class WorkSessionSummaryInTask(BaseModel):
    """Work session info embedded in task response."""

    id: UUID
    branch_name: str
    status: str
    commits: list[str] = []
    files_modified: list[str] = []
    pr_number: int | None = None
    pr_url: str | None = None
    pr_status: str | None = None


class ProjectSummaryInTask(BaseModel):
    """Project info embedded in task response."""

    id: UUID
    name: str
    slug: str
    git_url: str
    default_branch: str


class SubTaskResponse(BaseModel):
    """A sub-task within a task plan."""

    id: UUID
    title: str
    description: str | None = None
    completed: bool = False
    order: int
    estimated_hours: float | None = None
    notes: str | None = None


class TaskPlanResponse(BaseModel):
    """Implementation plan for a task."""

    approach: str
    sub_tasks: list[SubTaskResponse] = []
    technical_considerations: list[str] = []
    risks: list[dict[str, str]] = []
    open_questions: list[dict[str, Any]] = []


# =============================================================================
# INPUT MODELS (for creating/updating nested data)
# =============================================================================


class SubTaskInput(BaseModel):
    """Input for creating/updating a sub-task."""

    id: str  # Client-generated ID
    title: str
    description: str | None = None
    completed: bool = False
    order: int
    estimated_hours: float | None = None
    notes: str | None = None


class TaskPlanInput(BaseModel):
    """Input for creating/updating a task plan."""

    approach: str
    sub_tasks: list[SubTaskInput] = []
    technical_considerations: list[str] = []
    risks: list[dict[str, Any]] = []
    open_questions: list[dict[str, Any]] = []


class ProgressUpdateInput(BaseModel):
    """Input for adding a progress update."""

    timestamp: datetime
    agent_id: str  # Can be agent slug or "CEO"
    message: str
    percentage: int | None = None


class CheckpointInput(BaseModel):
    """Input for adding a checkpoint."""

    id: str  # Client-generated ID
    timestamp: datetime
    agent_id: str  # Can be agent slug or "CEO"
    state_summary: str
    remaining_work: list[str] = []
    notes: str | None = None


class CommitRefInput(BaseModel):
    """Input for linking a commit."""

    hash: str
    message: str
    timestamp: datetime
    author_agent_id: str | None = None  # Can be agent slug or "CEO"


# =============================================================================
# REQUEST MODELS
# =============================================================================


class TaskUpdate(BaseModel):
    """Request to update a task.

    CEO can update any field. All fields are optional for partial updates.
    """

    # Basic info
    title: str | None = None
    description: str | None = None
    acceptance_criteria: list[str] | None = None
    priority: int | None = Field(default=None, ge=0, le=3)
    target_date: datetime | None = None
    estimated_complexity: Complexity | None = None

    # Ownership & assignment
    team: Team | None = None
    assigned_to: str | None = None  # UUID string or null to unassign

    # Relationships
    parent_task_id: str | None = None  # UUID string or null
    dependency_ids: list[str] | None = None  # List of UUID strings
    blocker_ids: list[str] | None = None  # List of UUID strings

    # Planning
    plan: TaskPlanInput | None = None

    # Execution tracking
    progress_updates: list[ProgressUpdateInput] | None = None
    checkpoints: list[CheckpointInput] | None = None

    # Artifacts
    commits: list[CommitRefInput] | None = None

    # Notes
    dev_notes: str | None = None
    qa_notes: str | None = None
    auditor_notes: str | None = None
    quick_context: str | None = None


# =============================================================================
# RESPONSE MODELS
# =============================================================================


class TaskResponse(BaseModel):
    """Task response model with full detail."""

    # Identity
    id: UUID
    title: str
    description: str
    acceptance_criteria: list[str]

    # Status
    status: TaskStatus
    priority: int
    sequence: int  # Order number within siblings
    nature: TaskNature  # Technical or non-technical work

    # Task Type & Git Configuration (all tasks follow git workflow)
    task_type: TaskType  # code, documentation, research, etc.
    project_id: UUID  # Project this task works on (required)
    project_slug: str | None = None  # Project slug for MCP/git tool calls

    # Parallel Execution Tracking (for AWAITING_DOCUMENTATION phase)
    docs_complete: bool = False  # Documenter has finished
    pr_created: bool = False  # Developer has created PR

    # PM Approval Tracking (for AWAITING_PM_REVIEW phase)
    pm_approvals: dict[str, bool] = {}  # e.g. {'main_pm': True, 'cell_pm': True}

    # Ownership
    team: Team
    created_by: UUID
    assigned_to: UUID | None

    # Relationships
    parent_task_id: UUID | None
    dependency_ids: list[UUID]
    blocker_ids: list[UUID]

    # Timestamps
    created_at: datetime
    updated_at: datetime | None
    claimed_at: datetime | None
    claimed_by: UUID | None
    started_at: datetime | None
    completed_at: datetime | None
    target_date: datetime | None

    # Planning
    estimated_complexity: Complexity
    plan: TaskPlanResponse | None = None

    # Execution
    checkpoints: list[CheckpointResponse] = []
    progress_updates: list[ProgressUpdateResponse] = []

    # Artifacts
    commits: list[CommitRefResponse] = []

    # Documentation
    dev_notes: str | None
    qa_notes: str | None
    auditor_notes: str | None = None
    quick_context: str | None

    # Review Status
    self_verified: bool
    qa_verified: bool | None

    # Linked Sessions (for agent context)
    sessions: list[TaskSessionLinkResponse] = []

    # Git/Development Context (for full traceability)
    project: ProjectSummaryInTask | None = None
    work_session: WorkSessionSummaryInTask | None = None
    branch_name: str | None = None
    pr_number: int | None = None
    pr_url: str | None = None

    class Config:
        from_attributes = True


class TaskSummaryResponse(BaseModel):
    """Lightweight task response for list views."""

    id: UUID
    title: str
    status: TaskStatus
    priority: int
    team: Team
    assigned_to: UUID | None
    created_at: datetime
    updated_at: datetime | None
    estimated_complexity: Complexity
    nature: TaskNature

    class Config:
        from_attributes = True


class ProgressRequest(BaseModel):
    """Request to add progress update."""

    message: str
    percentage: int | None = Field(default=None, ge=0, le=100)


class CheckpointRequest(BaseModel):
    """Request to add checkpoint."""

    state_summary: str
    remaining_work: list[str]
    notes: str | None = None


class CommitRequest(BaseModel):
    """Request to link a commit."""

    hash: str = Field(..., min_length=7, max_length=40)
    message: str


class ClaimRequest(BaseModel):
    """Request to claim a task on behalf of an agent.

    Used by privileged roles (system, PM) to claim tasks for other agents.
    Accepts either a UUID or agent slug (e.g., "be-dev-1").
    """

    agent_id: str = Field(..., description="The agent ID (UUID) or slug to claim for")


class QANotes(BaseModel):
    """QA review notes."""

    notes: str


class CompleteTaskRequest(BaseModel):
    """Request to complete a task with optional force flag."""

    force_with_cancelled: bool = Field(
        default=False,
        description="Force complete even if some subtasks are cancelled. "
        "PM takes responsibility for judging work is done. "
        "Only applies to cancelled subtasks, not pending/in_progress.",
    )
    justification: str | None = Field(
        default=None,
        description="Required when force_with_cancelled=True. "
        "PM's justification for completing despite cancelled subtasks.",
    )


class SoftBlockRequest(BaseModel):
    """Request to soft-block a task due to an external factor."""

    reason: str = Field(..., description="Why the task is blocked")
    blocker_type: str = Field(
        ..., description="Type of blocker: external, internal, question, dependency"
    )
    what_needed: str = Field(..., description="What is needed to unblock the task")
    # Who can resolve: another agent ("agent") or only a human ("human").
    # Default is "agent" for back-compat with existing clients. When "human",
    # the dispatcher will NOT respawn agents on this task and only a HITL
    # unblock will move it forward.
    resolver_type: str = Field(
        default="agent",
        description=(
            "Who resolves: 'agent' (another agent can fix it — dispatcher "
            "keeps working) or 'human' (HITL/CEO only — dispatcher stops)"
        ),
    )


class EscalateRequest(BaseModel):
    """Request to escalate a task to PM/management.

    Escalation is available to ALL agents (devs, QA, documenters) when blocked.
    This bypasses normal notification permissions because escalation is a critical
    workflow tool for getting help when stuck.
    """

    reason: str = Field(..., description="Why the task is being escalated")
    escalate_to: str | None = Field(
        None, description="Target agent ID (defaults to cell PM)"
    )


class EscalateResponse(BaseModel):
    """Response from an escalation request."""

    status: str
    task_id: UUID
    escalated_to: str
    reason: str
    message: str


class SubstituteRequest(BaseModel):
    """Request to substitute out of a task.

    Allows agents to gracefully release tasks when they can't continue.
    This BYPASSES the "can't claim while in_progress" rule.
    """

    reason: str = Field(
        ...,
        description=(
            "Substitution reason: low_context, out_of_scope_team, "
            "out_of_scope_role, task_complete, max_retries, blocked_external"
        ),
    )
    details: str = Field(..., description="Human-readable explanation")
    suggested_role: str | None = Field(
        None, description="Hint for reassignment (developer, qa, pm, documenter)"
    )
    suggested_team: str | None = Field(
        None, description="Hint for reassignment (backend, frontend, ux_ui)"
    )


class TaskCountResponse(BaseModel):
    """Task count by category."""

    counts: dict[str, int]


class ListTasksQuery(BaseModel):
    """Query params for listing tasks."""

    team: Team | None = None
    status: TaskStatus | None = None
    limit: int = Field(100, ge=1, le=500)
    offset: int = Field(0, ge=0)


class TeamTasksQuery(BaseModel):
    """Query params for team tasks."""

    task_status: TaskStatus | None = None
    limit: int = Field(100, ge=1, le=500)


def convert_plan(plan_data: dict | None) -> TaskPlanResponse | None:
    """Convert plan JSON dict to TaskPlanResponse."""
    if not plan_data:
        return None

    sub_tasks = [
        SubTaskResponse(
            id=st.get("id"),
            title=st.get("title", ""),
            description=st.get("description"),
            completed=st.get("completed", False),
            order=st.get("order", 0),
            estimated_hours=st.get("estimated_hours"),
            notes=st.get("notes"),
        )
        for st in plan_data.get("sub_tasks", [])
    ]

    return TaskPlanResponse(
        approach=plan_data.get("approach", ""),
        sub_tasks=sub_tasks,
        technical_considerations=plan_data.get("technical_considerations", []),
        risks=plan_data.get("risks", []),
        open_questions=plan_data.get("open_questions", []),
    )


def convert_checkpoints(checkpoints_data: list | None) -> list[CheckpointResponse]:
    """Convert checkpoints JSON list to CheckpointResponse list."""
    if not checkpoints_data:
        return []
    return [
        CheckpointResponse(
            id=cp.get("id"),
            timestamp=cp.get("timestamp"),
            agent_id=cp.get("agent_id"),
            state_summary=cp.get("state_summary", ""),
            remaining_work=cp.get("remaining_work", []),
            notes=cp.get("notes"),
        )
        for cp in checkpoints_data
    ]


def convert_progress_updates(
    updates_data: list | None,
) -> list[ProgressUpdateResponse]:
    """Convert progress_updates JSON list to ProgressUpdateResponse list."""
    if not updates_data:
        return []
    return [
        ProgressUpdateResponse(
            timestamp=pu.get("timestamp"),
            agent_id=pu.get("agent_id"),
            message=pu.get("message", ""),
            percentage=pu.get("percentage"),
        )
        for pu in updates_data
    ]


def convert_commits(commits_data: list | None) -> list[CommitRefResponse]:
    """Convert commits JSON list to CommitRefResponse list."""
    if not commits_data:
        return []
    return [
        CommitRefResponse(
            hash=cm.get("hash", ""),
            message=cm.get("message", ""),
            timestamp=cm.get("timestamp"),
            author_agent_id=cm.get("author_agent_id"),
        )
        for cm in commits_data
    ]


def task_to_response(task: "TaskTable") -> TaskResponse:
    """Convert TaskTable to TaskResponse with proper UUID conversion."""
    return TaskResponse(
        id=require_uuid(task.id),
        title=task.title,
        description=task.description,
        acceptance_criteria=task.acceptance_criteria or [],
        status=task.status,
        priority=task.priority,
        sequence=task.sequence,
        nature=task.nature,
        task_type=task.task_type,
        project_id=require_uuid(task.project_id),
        # Don't trigger a lazy load — on freshly-created tasks `project` is
        # unloaded, and a sync attribute access here would raise
        # MissingGreenlet. Omit the slug rather than force an async round-trip.
        project_slug=(
            task.project.slug
            if "project" not in sa_inspect(task).unloaded
            and task.project is not None
            else None
        ),
        docs_complete=task.docs_complete,
        pr_created=task.pr_created,
        pm_approvals=task.pm_approvals or {},
        team=task.team,
        created_by=require_uuid(task.created_by),
        assigned_to=to_python_uuid(task.assigned_to),
        parent_task_id=to_python_uuid(task.parent_task_id),
        dependency_ids=to_python_uuid_list(task.dependency_ids),
        blocker_ids=to_python_uuid_list(task.blocker_ids),
        created_at=task.created_at,
        updated_at=task.updated_at,
        claimed_at=task.claimed_at,
        claimed_by=to_python_uuid(task.claimed_by),
        started_at=task.started_at,
        completed_at=task.completed_at,
        target_date=task.target_date,
        estimated_complexity=task.estimated_complexity,
        plan=convert_plan(task.plan),
        checkpoints=convert_checkpoints(task.checkpoints),
        progress_updates=convert_progress_updates(task.progress_updates),
        commits=convert_commits(task.commits),
        dev_notes=task.dev_notes,
        qa_notes=task.qa_notes,
        auditor_notes=task.auditor_notes,
        quick_context=task.quick_context,
        self_verified=task.self_verified,
        qa_verified=task.qa_verified,
        branch_name=getattr(task, "branch_name", None),
        pr_number=getattr(task, "pr_number", None),
        pr_url=getattr(task, "pr_url", None),
    )


def task_list_to_response(tasks: list["TaskTable"]) -> list[TaskResponse]:
    """Convert list of TaskTable to list of TaskResponse."""
    return [task_to_response(t) for t in tasks]


async def enrich_task_with_context(
    task_response: TaskResponse,
    db: Any,
    include_project: bool = True,
    include_work_session: bool = True,
) -> TaskResponse:
    """Enrich a TaskResponse with related context (project, work session)."""
    task_dict = task_response.model_dump()

    if include_work_session and hasattr(task_response, "id"):
        query = select(WorkSessionTable).where(
            WorkSessionTable.task_id == task_response.id
        )
        result = await db.execute(query)
        work_session = result.scalar_one_or_none()

        if work_session:
            task_dict["work_session"] = WorkSessionSummaryInTask(
                id=work_session.id,
                branch_name=work_session.branch_name,
                status=work_session.status.value if work_session.status else "unknown",
                commits=list(work_session.commits or []),
                files_modified=list(work_session.files_modified or []),
                pr_number=work_session.pr_number,
                pr_url=work_session.pr_url,
                pr_status=work_session.pr_status,
            )

            if include_project and work_session.project_id:
                proj_query = select(ProjectTable).where(
                    ProjectTable.id == work_session.project_id
                )
                proj_result = await db.execute(proj_query)
                project = proj_result.scalar_one_or_none()

                if project:
                    task_dict["project"] = ProjectSummaryInTask(
                        id=project.id,
                        name=project.name,
                        slug=project.slug,
                        git_url=project.git_url,
                        default_branch=project.default_branch,
                    )

    return TaskResponse(**task_dict)


def parse_uuid_or_none(value: str | None) -> UUID | None:
    """Parse a string to UUID, returning None if empty or None."""
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _parse_uuid_list(id_strings: list[str] | None) -> list[UUID]:
    """Parse a list of UUID strings to UUID objects, filtering empty values."""
    if not id_strings:
        return []
    return [UUID(id_str) for id_str in id_strings if id_str]


_SINGLE_UUID_FIELDS = ("assigned_to", "parent_task_id")
_UUID_LIST_FIELDS = ("dependency_ids", "blocker_ids")


def transform_update_data(data: TaskUpdate) -> dict:
    """Transform TaskUpdate input to format suitable for database storage."""
    updates = data.model_dump(exclude_unset=True)

    for field in _SINGLE_UUID_FIELDS:
        if field in updates:
            updates[field] = parse_uuid_or_none(updates[field])

    for field in _UUID_LIST_FIELDS:
        if field in updates:
            updates[field] = _parse_uuid_list(updates[field])

    return updates
