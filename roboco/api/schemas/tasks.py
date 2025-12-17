"""
Tasks API Schemas

Request/response models for task endpoints.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from roboco.models.base import Complexity, TaskStatus, Team

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
# REQUEST MODELS
# =============================================================================


class TaskUpdate(BaseModel):
    """Request to update a task."""

    title: str | None = None
    description: str | None = None
    acceptance_criteria: list[str] | None = None
    priority: int | None = Field(default=None, ge=0, le=3)
    target_date: datetime | None = None
    estimated_complexity: Complexity | None = None
    dev_notes: str | None = None
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
