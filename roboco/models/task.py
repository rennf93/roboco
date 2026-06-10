"""
Task Model

The atomic unit of work in the RoboCo system. Every piece of work
follows the universal task lifecycle: SCAN → CLAIM → UNDERSTAND →
PLAN → EXECUTE → VERIFY → NOTES → CLOSE.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from roboco.models.base import (
    Complexity,
    RobocoBase,
    TaskNature,
    TaskStatus,
    TaskType,
    Team,
    TimestampMixin,
)

# =============================================================================
# SUPPORTING MODELS
# =============================================================================


class CommitRef(RobocoBase):
    """Reference to a git commit."""

    hash: str = Field(..., min_length=7, max_length=40, description="Git commit hash")
    message: str = Field(..., description="Commit message summary")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    author_agent_id: UUID | None = Field(
        default=None, description="Agent who made the commit"
    )


class DocRef(RobocoBase):
    """Reference to a document."""

    path: str = Field(..., description="Path to document")
    title: str = Field(..., description="Document title")
    doc_type: str = Field(
        ..., description="Type of document (api, readme, architecture, etc.)"
    )
    version: str | None = Field(default=None, description="Document version")
    created_by: str | None = Field(default=None, description="Agent slug who created")
    created_at: str | None = Field(
        default=None, description="ISO timestamp of creation"
    )
    updated_by: str | None = Field(
        default=None, description="Agent slug who last updated"
    )
    updated_at: str | None = Field(
        default=None, description="ISO timestamp of last update"
    )


class ProgressUpdate(RobocoBase):
    """A progress update on a task."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    agent_id: UUID = Field(..., description="Agent providing update")
    message: str = Field(..., description="Progress message")
    percentage: int | None = Field(
        default=None, ge=0, le=100, description="Completion percentage"
    )


class Checkpoint(RobocoBase):
    """A saved state checkpoint for task recovery."""

    id: UUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    agent_id: UUID = Field(..., description="Agent who created checkpoint")
    state_summary: str = Field(..., description="Summary of current state")
    remaining_work: list[str] = Field(
        default_factory=list, description="Remaining sub-tasks"
    )
    notes: str | None = Field(default=None, description="Additional notes")


class SubTask(RobocoBase):
    """A sub-task within a task plan."""

    id: UUID = Field(default_factory=uuid4)
    title: str = Field(..., description="Sub-task title")
    description: str | None = Field(default=None, description="Sub-task description")
    completed: bool = Field(default=False)
    order: int = Field(..., ge=0, description="Order in the plan")
    estimated_hours: float | None = Field(
        default=None, description="Estimated hours to complete"
    )
    notes: str | None = None


class TaskPlan(RobocoBase):
    """Implementation plan for a task."""

    approach: str = Field(..., description="High-level approach description")
    sub_tasks: list[SubTask] = Field(
        default_factory=list, description="Ordered list of sub-tasks"
    )
    technical_considerations: list[str] = Field(
        default_factory=list, description="Technical notes and considerations"
    )
    risks: list[dict[str, str]] = Field(
        default_factory=list, description="Identified risks and mitigations"
    )
    open_questions: list[dict[str, str | bool]] = Field(
        default_factory=list, description="Questions with optional answers"
    )


# =============================================================================
# MAIN TASK MODEL
# =============================================================================


class Task(TimestampMixin):
    """
    The atomic unit of work in RoboCo.

    Tasks follow the universal lifecycle and persist across sessions.
    Every task must have acceptance criteria.
    """

    # Identity
    id: UUID = Field(default_factory=uuid4, description="Unique task identifier")
    title: str = Field(..., min_length=1, max_length=200, description="Task title")
    description: str = Field(..., description="Detailed task description")
    acceptance_criteria: list[str] = Field(
        ..., min_length=1, description="How do we know it's done?"
    )

    # Status
    status: TaskStatus = Field(default=TaskStatus.PENDING)
    priority: int = Field(
        default=2, ge=0, le=3, description="0=P0(highest), 3=P3(lowest)"
    )

    # Task Type & Git Configuration (all tasks follow git workflow)
    task_type: TaskType = Field(
        default=TaskType.CODE, description="Type of task (code, research, etc.)"
    )
    nature: TaskNature = Field(
        default=TaskNature.TECHNICAL, description="Technical or non-technical work"
    )

    # Project & Branch (branch auto-created on claim)
    project_id: UUID | None = Field(
        default=None,
        description="Target repo; None for a fan-out task that carries product_id",
    )
    product_id: UUID | None = Field(
        default=None,
        description="Product this task belongs to (additive; drives subtask routing)",
    )
    branch_name: str | None = Field(
        default=None, description="Branch created for this task"
    )
    work_session_id: UUID | None = Field(
        default=None, description="Active work session"
    )

    # PR Tracking (set during AWAITING_DOCUMENTATION parallel phase)
    pr_number: int | None = Field(default=None, description="GitHub/GitLab PR number")
    pr_url: str | None = Field(default=None, description="Full URL to PR")

    # Parallel Execution Tracking (for AWAITING_DOCUMENTATION phase)
    docs_complete: bool = Field(default=False, description="Documenter has finished")
    pr_created: bool = Field(default=False, description="Developer has created PR")

    # Ownership
    created_by: UUID = Field(..., description="Agent who created the task")
    assigned_to: UUID | None = Field(
        default=None, description="Currently assigned agent"
    )
    team: Team = Field(..., description="Which cell owns this task")

    # Relationships
    parent_task_id: UUID | None = Field(
        default=None, description="Parent task for sub-tasks"
    )
    dependency_ids: list[UUID] = Field(
        default_factory=list, description="Task IDs this is blocked by"
    )
    blocker_ids: list[UUID] = Field(
        default_factory=list, description="Task IDs this is blocking"
    )
    completed_dependency_ids: list[UUID] = Field(
        default_factory=list,
        description="Dependency task IDs that have since completed and cleared",
    )

    # Timestamps
    claimed_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    target_date: datetime | None = Field(
        default=None, description="Target completion date"
    )

    # Planning
    plan: TaskPlan | None = None
    estimated_complexity: Complexity = Field(default=Complexity.MEDIUM)

    # Execution
    checkpoints: list[Checkpoint] = Field(default_factory=list)
    progress_updates: list[ProgressUpdate] = Field(default_factory=list)

    # Artifacts
    commits: list[CommitRef] = Field(default_factory=list)
    documents: list[DocRef] = Field(default_factory=list)

    # Documentation
    dev_notes: str | None = Field(
        default=None, description="Journey notes from developer"
    )
    qa_notes: str | None = Field(default=None, description="QA feedback")
    auditor_notes: str | None = Field(default=None, description="Auditor observations")

    # Review Status
    self_verified: bool = Field(default=False)
    qa_verified: bool | None = None

    # Quick Context
    quick_context: str | None = Field(
        default=None,
        description="2-3 sentences for quick context restoration",
    )

    # Proactive Knowledge Context (injected when task is claimed)
    proactive_context: dict | None = Field(
        default=None,
        description="RAG context: similar tasks, learnings, patterns, standards",
    )

    # Gateway coordination (added in migration 006_gateway_columns).
    active_claimant_id: UUID | None = Field(
        default=None,
        description="Single-claimant lock; only one agent holds a task.",
    )
    last_heartbeat_at: datetime | None = Field(
        default=None,
        description="Last claim heartbeat; older than threshold = stale.",
    )
    pre_block_state: str | None = Field(
        default=None,
        description="Status snapshot at the moment of block.",
    )
    pre_block_assignee: UUID | None = Field(
        default=None,
        description="Assignee snapshot at the moment of block.",
    )
    pre_block_metadata: dict | None = Field(
        default=None,
        description="Snapshot used by unblock(restore=True).",
    )
    acceptance_criteria_status: list[dict] = Field(
        default_factory=list,
        description=("Per-criterion records: {criterion, referencing_artifact_id}."),
    )
    qa_evidence_inspected: bool = Field(
        default=False,
        description="True after QA inspects inline diff via claim_review.",
    )

    # Prompter origin tracking
    source: str = Field(
        default="manual",
        description="Origin of the task: 'manual', 'prompter', etc.",
    )
    confirmed_by_human: bool = Field(
        default=False,
        description="Whether a human has confirmed this prompter-originated task.",
    )

    # NOTE: Task state mutations should be performed through TaskService,
    # not directly on the model. See roboco/services/task.py for:
    # - claim(), start(), block(), pause(), resume()
    # - submit_for_verification(), submit_for_qa()
    # - fail_qa(), pass_qa(), complete(), cancel()
    # - add_checkpoint(), add_progress(), add_commit()


# =============================================================================
# CREATE/UPDATE SCHEMAS
# =============================================================================


class TaskCreate(RobocoBase):
    """Schema for creating a new task.

    Mirrors :data:`roboco.foundation.policy.task_completeness.TASK_AT_CREATE`
    so under-filled payloads fail at the request boundary — no silent
    defaults, no "code"/"technical"/"medium" fallbacks. The 2026-05-08 trace
    showed agents omitting task_type and the old default of "code"
    deadlocking the lifecycle; the same silent-default trap existed for
    nature ("technical") and complexity ("medium"). Force callers to
    declare intent.
    """

    title: str = Field(..., min_length=1, max_length=200)
    # 20-char minimum mirrors TASK_AT_CREATE.description (MIN_LENGTH=20).
    # Forces a real one-line summary instead of "x" or "see title".
    description: str = Field(..., min_length=20)
    acceptance_criteria: list[str] = Field(..., min_length=1)
    team: Team = Field(...)
    priority: int = Field(default=2, ge=0, le=3)
    parent_task_id: UUID | None = None
    # Accepts an agent UUID or an agent slug (e.g. "main-pm", "be-dev-1").
    # The route handler resolves slugs to UUIDs before persisting.
    assigned_to: str | None = None
    target_date: datetime | None = None
    # task_type, nature, estimated_complexity are EXPLICITLY_DECLARED in
    # TASK_AT_CREATE — no defaults.
    estimated_complexity: Complexity = Field(...)
    status: TaskStatus | None = None  # PM can set 'backlog' for subtasks needing setup

    # Ordering and dependencies
    sequence: int = Field(
        default=0, description="Order within siblings (lower = first)"
    )
    dependency_ids: list[UUID] = Field(
        default_factory=list,
        description="Task IDs that must complete before this task can be claimed",
    )

    # Git configuration (all tasks follow git workflow)
    task_type: TaskType = Field(...)
    nature: TaskNature = Field(...)
    # A task targets a single repo (project_id) OR fans out across cells via a
    # product (product_id, a cell->project map). Exactly one is needed; a
    # board/coordination task uses product_id and has no project of its own.
    project_id: UUID | None = None
    product_id: UUID | None = None

    # Prompter origin tracking
    source: str = Field(default="manual")
    confirmed_by_human: bool = Field(default=False)

    @model_validator(mode="after")
    def _project_or_product(self) -> "TaskCreate":
        if self.project_id is None and self.product_id is None:
            raise ValueError(
                "a task needs either a project_id (the repo it targets) or a "
                "product_id (a cell->project map for a fan-out task)"
            )
        return self


class TaskUpdate(RobocoBase):
    """Schema for updating a task."""

    title: str | None = None
    description: str | None = None
    acceptance_criteria: list[str] | None = None
    priority: int | None = Field(default=None, ge=0, le=3)
    status: TaskStatus | None = None
    assigned_to: UUID | None = None
    target_date: datetime | None = None
    estimated_complexity: Complexity | None = None
    dev_notes: str | None = None
    qa_notes: str | None = None
    quick_context: str | None = None

    # Git fields
    task_type: TaskType | None = None
    nature: TaskNature | None = None
    project_id: UUID | None = None
    branch_name: str | None = None
    pr_number: int | None = None
    pr_url: str | None = None
    docs_complete: bool | None = None
    pr_created: bool | None = None


# =============================================================================
# SERVICE PARAMETERS
# =============================================================================


@dataclass
class TaskCreateRequest:
    """Request data for creating a task via TaskService.

    Mirrors :data:`roboco.foundation.policy.task_completeness.TASK_AT_CREATE`.
    `task_type`, `nature`, and `estimated_complexity` are required —
    no silent "code"/"technical"/"medium" fallbacks. The 2026-05-08 trace
    showed those defaults deadlocking the lifecycle.
    """

    # Required fields (no defaults) — all of TASK_AT_CREATE plus owner/project.
    title: str
    description: str
    acceptance_criteria: list[str]
    team: Team
    created_by: UUID
    task_type: TaskType
    nature: TaskNature
    estimated_complexity: Complexity

    # Optional fields (with defaults)
    priority: int = 2
    parent_task_id: UUID | None = None
    assigned_to: UUID | None = None
    target_date: datetime | None = None
    status: TaskStatus | None = None  # PM can set BACKLOG for subtasks
    # A single-repo task sets project_id; a fan-out task sets product_id and the
    # cells' subtasks resolve their own project from it.
    project_id: UUID | None = None
    product_id: UUID | None = None

    # Ordering and dependencies
    sequence: int = 0  # Order within siblings (lower = first)
    dependency_ids: list[UUID] = field(default_factory=list)

    # Prompter origin tracking
    source: str = "manual"
    confirmed_by_human: bool = False
