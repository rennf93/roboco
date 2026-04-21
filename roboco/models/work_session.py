"""
WorkSession Model

Tracks an agent's working session on a task, including branch management,
commits, and PR tracking. Created when a developer claims a task.
"""

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field

from roboco.models.base import RobocoBase, TimestampMixin


class WorkSessionStatus(StrEnum):
    """Work session lifecycle states."""

    ACTIVE = "active"  # Agent is working
    COMPLETED = "completed"  # Work merged successfully
    ABANDONED = "abandoned"  # Work cancelled/discarded


class WorkSession(TimestampMixin):
    """
    A working session linking an agent to a task on a project.

    Created when a developer claims a task.
    Tracks branch, commits, and PR throughout the task lifecycle.
    """

    # Identity
    id: UUID = Field(default_factory=uuid4, description="Unique session identifier")
    project_id: UUID = Field(..., description="Project being worked on")
    task_id: UUID = Field(..., description="Task being worked on")
    agent_id: UUID = Field(..., description="Agent doing the work")

    # Branch Management
    branch_name: str = Field(
        ..., description="Full branch name (e.g., feature/backend/ABC123/XYZ)"
    )
    base_branch: str = Field(
        ..., description="Branch this was forked from (main or parent task branch)"
    )
    target_branch: str = Field(
        ..., description="Branch to merge into (main or parent task branch)"
    )

    # Lifecycle
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime | None = Field(default=None, description="When session ended")
    status: WorkSessionStatus = Field(default=WorkSessionStatus.ACTIVE)

    # Audit Trail
    commits: list[str] = Field(
        default_factory=list, description="Commit SHAs made in this session"
    )
    files_modified: list[str] = Field(
        default_factory=list, description="Files touched in this session"
    )

    # PR Tracking (set when dev creates PR)
    pr_number: int | None = Field(default=None, description="GitHub/GitLab PR number")
    pr_url: str | None = Field(default=None, description="Full URL to PR")
    pr_status: str | None = Field(
        default=None, description="PR status: open, merged, closed"
    )
    pr_created_at: datetime | None = Field(
        default=None, description="When PR was created"
    )
    pr_merged_at: datetime | None = Field(
        default=None, description="When PR was merged"
    )

    # Merge tracking
    merged_by: UUID | None = Field(
        default=None, description="Agent who approved/merged the PR"
    )


class WorkSessionCreate(RobocoBase):
    """Schema for creating a work session."""

    project_id: UUID
    task_id: UUID
    agent_id: UUID
    branch_name: str
    base_branch: str
    target_branch: str


class WorkSessionUpdate(RobocoBase):
    """Schema for updating a work session."""

    status: WorkSessionStatus | None = None
    ended_at: datetime | None = None
    commits: list[str] | None = None
    files_modified: list[str] | None = None
    pr_number: int | None = None
    pr_url: str | None = None
    pr_status: str | None = None
    pr_created_at: datetime | None = None
    pr_merged_at: datetime | None = None
    merged_by: UUID | None = None
