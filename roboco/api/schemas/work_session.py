"""
WorkSession API Schemas

Request/response models for work session endpoints.
"""

from datetime import datetime
from typing import TYPE_CHECKING
from typing import cast as typing_cast
from uuid import UUID

from pydantic import BaseModel

from roboco.models.work_session import WorkSessionStatus

if TYPE_CHECKING:
    from roboco.db.tables import WorkSessionTable


# =============================================================================
# RESPONSE MODELS
# =============================================================================


class WorkSessionResponse(BaseModel):
    """Response model for work session information."""

    id: UUID
    project_id: UUID
    task_id: UUID
    agent_id: UUID

    # Branch management
    branch_name: str
    base_branch: str
    target_branch: str

    # Lifecycle
    started_at: datetime
    ended_at: datetime | None = None
    status: WorkSessionStatus

    # Audit trail
    commits: list[str] = []
    files_modified: list[str] = []

    # PR tracking
    pr_number: int | None = None
    pr_url: str | None = None
    pr_status: str | None = None
    pr_created_at: datetime | None = None
    pr_merged_at: datetime | None = None
    merged_by: UUID | None = None

    # Timestamps
    created_at: datetime
    updated_at: datetime | None = None

    class Config:
        """Pydantic config."""

        from_attributes = True


class WorkSessionSummaryResponse(BaseModel):
    """Compact work session response for list views."""

    id: UUID
    task_id: UUID
    branch_name: str
    status: WorkSessionStatus
    started_at: datetime
    has_pr: bool = False

    class Config:
        """Pydantic config."""

        from_attributes = True


# =============================================================================
# REQUEST MODELS
# =============================================================================


class WorkSessionCreateRequest(BaseModel):
    """Request model for creating a work session."""

    project_id: UUID
    task_id: UUID
    branch_name: str
    base_branch: str
    target_branch: str


class AddCommitRequest(BaseModel):
    """Request to add a commit to a session."""

    commit_sha: str


class AddFilesRequest(BaseModel):
    """Request to add modified files to a session."""

    file_paths: list[str]


class CreatePRRequest(BaseModel):
    """Request to record PR creation."""

    pr_number: int
    pr_url: str


class UpdatePRStatusRequest(BaseModel):
    """Request to update PR status."""

    pr_status: str  # open, merged, closed


class MergePRRequest(BaseModel):
    """Request to record PR merge."""

    merged_by: UUID


# =============================================================================
# CONVERTERS
# =============================================================================


def session_to_response(session: "WorkSessionTable") -> WorkSessionResponse:
    """Convert a WorkSessionTable to WorkSessionResponse."""
    return WorkSessionResponse(
        id=typing_cast("UUID", session.id),
        project_id=typing_cast("UUID", session.project_id),
        task_id=typing_cast("UUID", session.task_id),
        agent_id=typing_cast("UUID", session.agent_id),
        branch_name=str(session.branch_name),
        base_branch=str(session.base_branch),
        target_branch=str(session.target_branch),
        started_at=session.started_at,
        ended_at=session.ended_at,
        status=session.status,
        commits=list(session.commits or []),
        files_modified=list(session.files_modified or []),
        pr_number=session.pr_number,
        pr_url=session.pr_url,
        pr_status=session.pr_status,
        pr_created_at=session.pr_created_at,
        pr_merged_at=session.pr_merged_at,
        merged_by=typing_cast("UUID", session.merged_by) if session.merged_by else None,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


def session_to_summary(session: "WorkSessionTable") -> WorkSessionSummaryResponse:
    """Convert a WorkSessionTable to WorkSessionSummaryResponse."""
    return WorkSessionSummaryResponse(
        id=typing_cast("UUID", session.id),
        task_id=typing_cast("UUID", session.task_id),
        branch_name=str(session.branch_name),
        status=session.status,
        started_at=session.started_at,
        has_pr=bool(session.pr_number),
    )
