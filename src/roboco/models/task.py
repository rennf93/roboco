"""
Task Model

The atomic unit of work in the RoboCo system. Every piece of work
follows the universal task lifecycle: SCAN → CLAIM → UNDERSTAND →
PLAN → EXECUTE → VERIFY → NOTES → CLOSE.
"""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import Field

from roboco.models.base import (
    Complexity,
    RobocoBase,
    TaskStatus,
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
    timestamp: datetime = Field(default_factory=datetime.utcnow)
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


class FileRef(RobocoBase):
    """Reference to a file artifact."""

    path: str = Field(..., description="Path to file")
    description: str = Field(..., description="What this file is")
    file_type: str = Field(..., description="File type/extension")
    size_bytes: int | None = Field(default=None, description="File size in bytes")


class ProgressUpdate(RobocoBase):
    """A progress update on a task."""

    timestamp: datetime = Field(default_factory=datetime.utcnow)
    agent_id: UUID = Field(..., description="Agent providing update")
    message: str = Field(..., description="Progress message")
    percentage: int | None = Field(
        default=None, ge=0, le=100, description="Completion percentage"
    )


class Checkpoint(RobocoBase):
    """A saved state checkpoint for task recovery."""

    id: UUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
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

    def claim(self, agent_id: UUID) -> None:
        """Claim this task for an agent."""
        self.assigned_to = agent_id
        self.claimed_at = datetime.utcnow()
        self.status = TaskStatus.CLAIMED

    def start(self) -> None:
        """Start working on the task."""
        self.started_at = datetime.utcnow()
        self.status = TaskStatus.IN_PROGRESS

    def block(self, blocker_task_id: UUID) -> None:
        """Mark task as blocked by another task."""
        if blocker_task_id not in self.dependency_ids:
            self.dependency_ids.append(blocker_task_id)
        self.status = TaskStatus.BLOCKED

    def pause(self) -> None:
        """Pause the task."""
        self.status = TaskStatus.PAUSED

    def resume(self) -> None:
        """Resume the task."""
        self.status = TaskStatus.IN_PROGRESS

    def submit_for_verification(self) -> None:
        """Submit task for verification."""
        self.status = TaskStatus.VERIFYING

    def submit_for_qa(self) -> None:
        """Submit task for QA review."""
        self.self_verified = True
        self.status = TaskStatus.AWAITING_QA

    def fail_qa(self, notes: str) -> None:
        """Mark task as needing revision after QA."""
        self.qa_notes = notes
        self.qa_verified = False
        self.status = TaskStatus.NEEDS_REVISION

    def pass_qa(self, notes: str | None = None) -> None:
        """Mark task as passed QA."""
        if notes:
            self.qa_notes = notes
        self.qa_verified = True
        self.status = TaskStatus.AWAITING_DOCUMENTATION

    def complete(self) -> None:
        """Mark task as completed."""
        self.completed_at = datetime.utcnow()
        self.status = TaskStatus.COMPLETED

    def cancel(self) -> None:
        """Cancel the task."""
        self.status = TaskStatus.CANCELLED

    def add_checkpoint(
        self, agent_id: UUID, state_summary: str, remaining_work: list[str]
    ) -> None:
        """Add a checkpoint for state recovery."""
        checkpoint = Checkpoint(
            agent_id=agent_id,
            state_summary=state_summary,
            remaining_work=remaining_work,
        )
        self.checkpoints.append(checkpoint)

    def add_progress(
        self, agent_id: UUID, message: str, percentage: int | None = None
    ) -> None:
        """Add a progress update."""
        update = ProgressUpdate(
            agent_id=agent_id,
            message=message,
            percentage=percentage,
        )
        self.progress_updates.append(update)

    def add_commit(self, hash: str, message: str, agent_id: UUID | None = None) -> None:
        """Link a commit to this task."""
        commit = CommitRef(
            hash=hash,
            message=message,
            author_agent_id=agent_id,
        )
        self.commits.append(commit)


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
    target_date: datetime | None = None
    estimated_complexity: Complexity = Complexity.MEDIUM


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
