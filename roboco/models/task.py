"""
Task Model

The atomic unit of work in the RoboCo system. Every piece of work
follows the universal task lifecycle: SCAN → CLAIM → UNDERSTAND →
PLAN → EXECUTE → VERIFY → NOTES → CLOSE.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import Field

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


class FileRef(RobocoBase):
    """Reference to a file artifact."""

    path: str = Field(..., description="Path to file")
    description: str = Field(..., description="What this file is")
    file_type: str = Field(..., description="File type/extension")
    size_bytes: int | None = Field(default=None, description="File size in bytes")


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


class ExecutionLog(RobocoBase):
    """Log of task execution events."""

    events: list[dict] = Field(
        default_factory=list, description="List of execution events"
    )
    errors: list[dict] = Field(
        default_factory=list, description="List of errors encountered"
    )
    total_duration_seconds: float | None = Field(
        default=None, description="Total execution time"
    )


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
    project_id: UUID = Field(..., description="Project this task works on")
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

    # PM Approval Tracking (for AWAITING_PM_REVIEW phase)
    pm_approvals: dict[str, bool] = Field(
        default_factory=dict,
        description="PM approvals: {'main_pm': True, 'cell_pm': True}",
    )

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
    execution_log: ExecutionLog = Field(default_factory=ExecutionLog)
    checkpoints: list[Checkpoint] = Field(default_factory=list)
    progress_updates: list[ProgressUpdate] = Field(default_factory=list)

    # Artifacts
    commits: list[CommitRef] = Field(default_factory=list)
    documents: list[DocRef] = Field(default_factory=list)
    outputs: list[FileRef] = Field(default_factory=list)

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
    """Schema for creating a new task."""

    title: str = Field(..., min_length=1, max_length=200)
    description: str
    acceptance_criteria: list[str] = Field(..., min_length=1)
    team: Team
    priority: int = Field(default=2, ge=0, le=3)
    parent_task_id: UUID | None = None
    assigned_to: UUID | None = None  # Optional: assign on creation
    target_date: datetime | None = None
    estimated_complexity: Complexity = Complexity.MEDIUM
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
    task_type: TaskType = TaskType.CODE
    nature: TaskNature = TaskNature.TECHNICAL
    project_id: UUID  # Required - all tasks need a project


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
    pm_approvals: dict[str, bool] | None = None


# =============================================================================
# SERVICE PARAMETERS
# =============================================================================


@dataclass
class TaskCreateRequest:
    """Request data for creating a task via TaskService."""

    # Required fields (no defaults)
    title: str
    description: str
    acceptance_criteria: list[str]
    team: Team
    created_by: UUID
    project_id: UUID  # Required - all tasks need a project for git workflow

    # Optional fields (with defaults)
    priority: int = 2
    parent_task_id: UUID | None = None
    assigned_to: UUID | None = None
    target_date: datetime | None = None
    estimated_complexity: Complexity = field(default=Complexity.MEDIUM)
    status: TaskStatus | None = None  # PM can set BACKLOG for subtasks

    # Ordering and dependencies
    sequence: int = 0  # Order within siblings (lower = first)
    dependency_ids: list[UUID] = field(default_factory=list)

    # Git configuration (all tasks follow git workflow)
    task_type: TaskType = field(default=TaskType.CODE)
    nature: TaskNature = field(default=TaskNature.TECHNICAL)
