"""
Project API Schemas

Request/response models for project endpoints.
"""

from datetime import datetime
from typing import TYPE_CHECKING
from typing import cast as typing_cast
from uuid import UUID

from pydantic import BaseModel, Field

from roboco.models.base import Team

if TYPE_CHECKING:
    from roboco.db.tables import ProjectTable


# =============================================================================
# RESPONSE MODELS
# =============================================================================


class ProjectResponse(BaseModel):
    """Response model for project information."""

    id: UUID
    name: str
    slug: str
    git_url: str
    default_branch: str
    protected_branches: list[str]
    assigned_cell: Team

    # Optional commands
    test_command: str | None = None
    lint_command: str | None = None
    format_command: str | None = None
    typecheck_command: str | None = None
    build_command: str | None = None

    # Runtime state
    workspace_path: str | None = None
    last_synced_at: datetime | None = None
    head_commit: str | None = None

    # Metadata
    created_by: UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime | None = None

    class Config:
        """Pydantic config."""

        from_attributes = True


class ProjectSummaryResponse(BaseModel):
    """Compact project response for list views."""

    id: UUID
    name: str
    slug: str
    assigned_cell: Team
    is_active: bool
    has_workspace: bool = False

    class Config:
        """Pydantic config."""

        from_attributes = True


# =============================================================================
# REQUEST MODELS
# =============================================================================


class ProjectCreateRequest(BaseModel):
    """Request model for creating a project."""

    name: str = Field(..., min_length=1, max_length=100)
    slug: str = Field(..., min_length=1, max_length=50, pattern=r"^[a-z0-9-]+$")
    git_url: str
    default_branch: str = "main"
    protected_branches: list[str] = Field(default_factory=lambda: ["main", "master"])
    assigned_cell: Team

    # Optional commands
    test_command: str | None = None
    lint_command: str | None = None
    format_command: str | None = None
    typecheck_command: str | None = None
    build_command: str | None = None


class ProjectUpdateRequest(BaseModel):
    """Request model for updating a project."""

    name: str | None = None
    git_url: str | None = None
    default_branch: str | None = None
    protected_branches: list[str] | None = None
    assigned_cell: Team | None = None

    # Commands
    test_command: str | None = None
    lint_command: str | None = None
    format_command: str | None = None
    typecheck_command: str | None = None
    build_command: str | None = None

    # State
    is_active: bool | None = None


class SetWorkspaceRequest(BaseModel):
    """Request to set project workspace path."""

    workspace_path: str


class SyncStateRequest(BaseModel):
    """Request to update sync state."""

    head_commit: str


# =============================================================================
# CONVERTERS
# =============================================================================


def project_to_response(project: "ProjectTable") -> ProjectResponse:
    """Convert a ProjectTable to ProjectResponse."""
    default_branch = project.default_branch
    return ProjectResponse(
        id=typing_cast("UUID", project.id),
        name=str(project.name),
        slug=str(project.slug),
        git_url=str(project.git_url),
        default_branch=str(default_branch) if default_branch else "main",
        protected_branches=list(project.protected_branches or []),
        assigned_cell=project.assigned_cell,
        test_command=project.test_command,
        lint_command=project.lint_command,
        format_command=project.format_command,
        typecheck_command=project.typecheck_command,
        build_command=project.build_command,
        workspace_path=project.workspace_path,
        last_synced_at=project.last_synced_at,
        head_commit=project.head_commit,
        created_by=typing_cast("UUID", project.created_by),
        is_active=bool(project.is_active),
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def project_to_summary(project: "ProjectTable") -> ProjectSummaryResponse:
    """Convert a ProjectTable to ProjectSummaryResponse."""
    return ProjectSummaryResponse(
        id=typing_cast("UUID", project.id),
        name=str(project.name),
        slug=str(project.slug),
        assigned_cell=project.assigned_cell,
        is_active=bool(project.is_active),
        has_workspace=bool(project.workspace_path),
    )
