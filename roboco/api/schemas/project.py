"""
Project API Schemas

Request/response models for project endpoints.
"""

from datetime import datetime
from typing import TYPE_CHECKING
from typing import cast as typing_cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

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
    # Forge provider ("github"|"gitlab"|"gitea"); null = auto-detect from
    # git_url host (github.com -> github, stamped on create).
    git_provider: str | None = None
    # GitHub App installation covering this repo; null = PAT-only auth.
    github_installation_id: int | None = None
    default_branch: str
    protected_branches: list[str]
    environments: list[dict[str, str]] | None = None
    assigned_cell: Team

    # Git authentication status (token never exposed, only boolean)
    has_git_token: bool = False

    # Optional commands
    test_command: str | None = None
    lint_command: str | None = None
    format_command: str | None = None
    typecheck_command: str | None = None
    build_command: str | None = None
    quality_command: str | None = None
    codegen_command: str | None = None

    # Autonomous maintenance opt-in
    ci_watch_enabled: bool = False
    ci_watch_workflow: str | None = None
    video_engine_enabled: bool = False
    dep_update_command: str | None = None
    dep_update_paths: list[str] | None = None
    sandbox_services: list[str] | None = None
    sandbox_extensions: dict[str, list[str]] | None = None

    # Runtime state
    workspace_path: str | None = None
    last_synced_at: datetime | None = None
    head_commit: str | None = None

    # Metadata
    created_by: UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class ProjectTaskCounts(BaseModel):
    """Per-project task progress (done/active/blocked) for list views."""

    done: int = 0
    active: int = 0
    blocked: int = 0


class ProjectSummaryResponse(BaseModel):
    """Compact project response for list views.

    Returned by GET /api/projects; includes essential project metadata
    for list-view cards. The `video_engine_enabled` field indicates
    whether this project is opted in to the video engine subsystem.
    `task_counts` is a done/active/blocked breakdown from one grouped
    query over tasks; `ci_watch_enabled` signals CI-watch is armed (no
    live-conclusion fan-out — that's a deferred cached endpoint).
    """

    id: UUID
    name: str
    slug: str
    git_url: str
    default_branch: str
    assigned_cell: Team
    is_active: bool
    has_workspace: bool = False
    has_git_token: bool = False
    video_engine_enabled: bool = False
    ci_watch_enabled: bool = False
    task_counts: ProjectTaskCounts | None = None

    model_config = ConfigDict(from_attributes=True)


# =============================================================================
# REQUEST MODELS
# =============================================================================


class ProjectCreateRequest(BaseModel):
    """Request model for creating a project."""

    name: str = Field(..., min_length=1, max_length=100)
    slug: str = Field(..., min_length=1, max_length=50, pattern=r"^[a-z0-9-]+$")
    git_url: str
    git_provider: str | None = Field(
        default=None,
        description=(
            "Forge provider ('github'|'gitlab'|'gitea'). null = auto-detect "
            "from git_url host (github.com -> github)."
        ),
    )
    default_branch: str = "master"
    protected_branches: list[str] | None = Field(
        default=None,
        description="Branches to protect. Defaults to [default_branch].",
    )
    environments: list[dict[str, str]] | None = Field(
        default=None,
        description=(
            "Ordered environment ladder [{name, branch}]; first = head (PR "
            "target), last = prod (release target). null → inherits "
            "default_branch (head == prod)."
        ),
    )
    assigned_cell: Team

    # Git authentication (will be encrypted and stored securely)
    git_token: str | None = Field(
        default=None,
        description="GitHub PAT for clone/push/PR (stored encrypted, never returned)",
    )
    github_installation_id: int | None = Field(
        default=None,
        description=(
            "GitHub App installation id covering this repo (from the Select "
            "repo picker). When set with App credentials configured, git "
            "operations use a minted installation token instead of a PAT."
        ),
    )

    # Optional commands
    test_command: str | None = None
    lint_command: str | None = None
    format_command: str | None = None
    typecheck_command: str | None = None
    build_command: str | None = None
    quality_command: str | None = None
    codegen_command: str | None = None


class ProjectUpdateRequest(BaseModel):
    """Request model for updating a project."""

    name: str | None = None
    git_url: str | None = None
    git_provider: str | None = Field(
        default=None,
        description=(
            "Forge provider ('github'|'gitlab'|'gitea'). Re-validated against "
            "the (possibly also-updated) git_url whenever either is set."
        ),
    )
    default_branch: str | None = None
    protected_branches: list[str] | None = None
    environments: list[dict[str, str]] | None = None
    assigned_cell: Team | None = None

    # Git authentication (empty string clears token, None leaves unchanged)
    git_token: str | None = Field(
        default=None,
        description="GitHub PAT (empty string clears, None leaves unchanged)",
    )
    github_installation_id: int | None = Field(
        default=None,
        description=(
            "GitHub App installation id covering this repo. Explicit null "
            "clears the binding (falls back to PAT-only auth)."
        ),
    )

    # Commands
    test_command: str | None = None
    lint_command: str | None = None
    format_command: str | None = None
    typecheck_command: str | None = None
    build_command: str | None = None
    quality_command: str | None = None
    codegen_command: str | None = None

    # Autonomous maintenance opt-in
    ci_watch_enabled: bool | None = None
    ci_watch_workflow: str | None = None
    video_engine_enabled: bool | None = None
    dep_update_command: str | None = None
    dep_update_paths: list[str] | None = None
    sandbox_services: list[str] | None = None
    sandbox_extensions: dict[str, list[str]] | None = None

    # State
    is_active: bool | None = None


class SetWorkspaceRequest(BaseModel):
    """Request to set project workspace path."""

    workspace_path: str


class SyncStateRequest(BaseModel):
    """Request to update sync state."""

    head_commit: str


# =============================================================================
# CONVENTIONS
# =============================================================================


class ConventionsHealthResponse(BaseModel):
    """Health of a project's architectural-conventions standard."""

    status: str
    head_sha: str
    last_ok_sha: str | None


class ConventionsResponse(BaseModel):
    """The project's effective conventions map + its current health."""

    standard: dict[str, object]
    health: ConventionsHealthResponse


class ConventionsActionResponse(BaseModel):
    """Result of a scaffold / restore / save — the branch + PR (if opened)."""

    pr_number: int | None
    branch: str
    created: bool


class ConventionFinding(BaseModel):
    """One recorded architectural-conventions violation (for the feed)."""

    file: str
    line: int
    rule: str
    level: str
    kind: str | None
    message: str
    task_id: str | None
    detected_at: str


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
        git_provider=project.git_provider,
        github_installation_id=project.github_installation_id,
        default_branch=str(default_branch) if default_branch else "master",
        protected_branches=list(project.protected_branches or []),
        environments=list(project.environments) if project.environments else None,
        assigned_cell=project.assigned_cell,
        has_git_token=bool(project.git_token_encrypted),
        test_command=project.test_command,
        lint_command=project.lint_command,
        format_command=project.format_command,
        typecheck_command=project.typecheck_command,
        build_command=project.build_command,
        quality_command=project.quality_command,
        codegen_command=project.codegen_command,
        ci_watch_enabled=bool(project.ci_watch_enabled),
        ci_watch_workflow=project.ci_watch_workflow,
        video_engine_enabled=bool(project.video_engine_enabled),
        dep_update_command=project.dep_update_command,
        dep_update_paths=project.dep_update_paths,
        sandbox_services=project.sandbox_services,
        sandbox_extensions=project.sandbox_extensions,
        workspace_path=project.workspace_path,
        last_synced_at=project.last_synced_at,
        head_commit=project.head_commit,
        created_by=typing_cast("UUID", project.created_by),
        is_active=bool(project.is_active),
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def project_to_summary(
    project: "ProjectTable",
    task_counts: "ProjectTaskCounts | None" = None,
) -> ProjectSummaryResponse:
    """Convert a ProjectTable to ProjectSummaryResponse."""
    default_branch = project.default_branch
    return ProjectSummaryResponse(
        id=typing_cast("UUID", project.id),
        name=str(project.name),
        slug=str(project.slug),
        git_url=str(project.git_url),
        default_branch=str(default_branch) if default_branch else "master",
        assigned_cell=project.assigned_cell,
        is_active=bool(project.is_active),
        has_workspace=bool(project.workspace_path),
        has_git_token=bool(project.git_token_encrypted),
        video_engine_enabled=bool(project.video_engine_enabled),
        ci_watch_enabled=bool(project.ci_watch_enabled),
        task_counts=task_counts,
    )
