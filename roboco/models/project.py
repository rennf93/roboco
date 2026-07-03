"""
Project Model

A git repository that agents work on. Projects are registered by PMs
and contain configuration for test commands, branch policies, and
cell assignments.
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field, field_validator

from roboco.models.base import RobocoBase, Team, TimestampMixin


class BranchReason(StrEnum):
    """Reason/type prefix for branch naming."""

    FEATURE = "feature"
    BUG = "bug"
    CHORE = "chore"
    DOCS = "docs"
    HOTFIX = "hotfix"


# Sandboxed per-agent-spawn DB/Redis opt-in — the services a project may
# request. Single source of truth for both the pydantic validators below and
# the orchestrator-side provisioner (roboco/runtime/sandbox.py).
VALID_SANDBOX_SERVICES: frozenset[str] = frozenset({"postgres", "redis"})


def _normalize_sandbox_services(value: list[str] | None) -> list[str] | None:
    """Reject an unknown service; normalize order + de-dupe a valid list."""
    if value is None:
        return None
    unknown = sorted(set(value) - VALID_SANDBOX_SERVICES)
    if unknown:
        raise ValueError(
            f"unknown sandbox service(s) {unknown}; valid: "
            f"{sorted(VALID_SANDBOX_SERVICES)}"
        )
    return [s for s in ("postgres", "redis") if s in value]


class Project(TimestampMixin):
    """
    A git repository that agents work on.

    Projects are registered by Main PM or Cell PM and contain
    configuration for the development workflow.
    """

    # Identity
    id: UUID = Field(default_factory=uuid4, description="Unique project identifier")
    name: str = Field(..., min_length=1, max_length=100, description="Project name")
    slug: str = Field(
        ...,
        min_length=1,
        max_length=50,
        pattern=r"^[a-z0-9-]+$",
        description="URL-safe identifier (e.g., 'roboco', 'roboco-panel')",
    )

    # Git Configuration
    git_url: str = Field(..., description="Git repository URL")
    default_branch: str = Field(default="master", description="Default branch name")
    protected_branches: list[str] = Field(
        default_factory=lambda: ["main", "master"],
        description="Branches that cannot be pushed to directly",
    )

    # CI/CD Commands (optional - project may not have all)
    test_command: str | None = Field(
        default=None, description="Command to run tests (e.g., 'uv run pytest')"
    )
    lint_command: str | None = Field(
        default=None, description="Command to run linter (e.g., 'uv run ruff check .')"
    )
    format_command: str | None = Field(
        default=None,
        description="Command to format code (e.g., 'uv run ruff format .')",
    )
    typecheck_command: str | None = Field(
        default=None,
        description="Command to run type checker (e.g., 'uv run mypy src/')",
    )
    build_command: str | None = Field(
        default=None, description="Command to build (e.g., 'pnpm build')"
    )
    quality_command: str | None = Field(
        default=None,
        description=(
            "Fast pre-submit gate command run in the dev's workspace at "
            "i_am_done (lint+types+complexity, no tests; e.g. 'make gate'). "
            "When set it replaces the lint/typecheck pair in the gate."
        ),
    )

    # Access Control
    assigned_cell: Team = Field(..., description="Which cell owns this project")
    allowed_agents: list[UUID] | None = Field(
        default=None, description="Specific agents allowed (None = all in cell)"
    )

    # Git Authentication (token stored encrypted, never exposed)
    has_git_token: bool = Field(
        default=False,
        description="Whether a git token is configured (token never exposed)",
    )

    # Runtime State
    workspace_path: str | None = Field(
        default=None,
        description="Local path to workspace (e.g., /data/workspaces/{slug})",
    )
    last_synced_at: datetime | None = Field(
        default=None, description="Last time project was synced from remote"
    )
    head_commit: str | None = Field(default=None, description="Current HEAD commit SHA")

    # Autonomous maintenance opt-in (multi-repo CI-watch)
    ci_watch_enabled: bool = Field(
        default=False, description="Watch this project's CI and auto-open fix tasks"
    )
    ci_watch_workflow: str | None = Field(
        default=None, description="Workflow file to scope the CI-watch signal to"
    )

    # Dependency-update bot opt-in
    dep_update_command: str | None = Field(
        default=None,
        description="Command to refresh lockfiles, e.g. 'uv lock --upgrade'",
    )
    dep_update_paths: list[str] | None = Field(
        default=None,
        description="Lockfile globs to inspect (null → infer uv.lock/pnpm-lock.yaml)",
    )

    # Sandboxed per-agent-spawn DB/Redis opt-in
    sandbox_services: list[str] | None = Field(
        default=None,
        description=(
            "Throwaway sandbox services this project's agent spawns provision "
            "(subset of 'postgres', 'redis'); null/empty = no sandbox."
        ),
    )

    @field_validator("sandbox_services")
    @classmethod
    def _check_sandbox_services(cls, v: list[str] | None) -> list[str] | None:
        return _normalize_sandbox_services(v)

    # Metadata
    created_by: UUID = Field(..., description="PM who registered the project")
    is_active: bool = Field(default=True, description="Whether project is active")


class ProjectCreate(RobocoBase):
    """Schema for creating/registering a new project."""

    name: str = Field(..., min_length=1, max_length=100)
    slug: str = Field(..., min_length=1, max_length=50, pattern=r"^[a-z0-9-]+$")
    git_url: str
    default_branch: str = "master"
    protected_branches: list[str] = Field(default_factory=lambda: ["main", "master"])
    assigned_cell: Team

    # Git authentication (will be encrypted and stored securely)
    git_token: str | None = Field(
        default=None,
        description="GitHub PAT for clone/push/PR operations (stored encrypted)",
    )

    # Optional commands
    test_command: str | None = None
    lint_command: str | None = None
    format_command: str | None = None
    typecheck_command: str | None = None
    build_command: str | None = None
    quality_command: str | None = None


class ProjectUpdate(RobocoBase):
    """Schema for updating a project."""

    name: str | None = None
    git_url: str | None = None
    default_branch: str | None = None
    protected_branches: list[str] | None = None

    # Git authentication (empty string clears token, None leaves unchanged)
    git_token: str | None = Field(
        default=None,
        description="GitHub PAT (empty string clears, None leaves unchanged)",
    )

    test_command: str | None = None
    lint_command: str | None = None
    format_command: str | None = None
    typecheck_command: str | None = None
    build_command: str | None = None
    quality_command: str | None = None
    assigned_cell: Team | None = None
    allowed_agents: list[UUID] | None = None
    is_active: bool | None = None
    ci_watch_enabled: bool | None = None
    ci_watch_workflow: str | None = None
    dep_update_command: str | None = None
    dep_update_paths: list[str] | None = None
    sandbox_services: list[str] | None = None

    @field_validator("sandbox_services")
    @classmethod
    def _check_sandbox_services(cls, v: list[str] | None) -> list[str] | None:
        return _normalize_sandbox_services(v)
