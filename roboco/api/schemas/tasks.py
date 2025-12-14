"""
Tasks API Schemas

Request/response models for task endpoints.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from roboco.models.base import Complexity, TaskStatus, Team


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


class TaskResponse(BaseModel):
    """Task response model."""

    id: UUID
    title: str
    description: str
    acceptance_criteria: list[str]
    status: TaskStatus
    priority: int
    team: Team
    created_by: UUID
    assigned_to: UUID | None
    parent_task_id: UUID | None
    dependency_ids: list[UUID]
    blocker_ids: list[UUID]
    created_at: datetime
    updated_at: datetime | None
    claimed_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    target_date: datetime | None
    estimated_complexity: Complexity
    self_verified: bool
    qa_verified: bool | None
    dev_notes: str | None
    qa_notes: str | None
    quick_context: str | None

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
