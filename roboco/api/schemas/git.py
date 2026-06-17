"""
Git API Schemas

Request/response models for git operation endpoints.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

# =============================================================================
# STATUS
# =============================================================================


class GitStatusResponse(BaseModel):
    """Git status response."""

    project_slug: str
    current_branch: str
    has_changes: bool
    staged_files: list[str] = []
    unstaged_files: list[str] = []
    untracked_files: list[str] = []
    ahead: int = 0
    behind: int = 0


# =============================================================================
# LOG
# =============================================================================


class CommitInfo(BaseModel):
    """Information about a git commit."""

    hash: str
    short_hash: str
    message: str
    author: str
    date: datetime


class GitLogResponse(BaseModel):
    """Git log response."""

    project_slug: str
    branch: str
    commits: list[CommitInfo] = []


# =============================================================================
# BRANCHES
# =============================================================================


class BranchInfo(BaseModel):
    """Information about a git branch."""

    name: str
    is_current: bool = False
    is_remote: bool = False
    last_commit: str | None = None


class GitBranchListResponse(BaseModel):
    """Git branch list response."""

    project_slug: str
    current_branch: str
    branches: list[BranchInfo] = []


class GitCreateBranchRequest(BaseModel):
    """Request to create a branch."""

    project_slug: str
    task_id: UUID
    branch_type: str = Field(..., pattern=r"^(feature|bug|chore|docs|hotfix)$")
    parent_branch: str | None = None


class GitCreateBranchResponse(BaseModel):
    """Response from branch creation."""

    branch_name: str
    created_from: str
    project_slug: str


class GitCheckoutRequest(BaseModel):
    """Request to checkout a branch."""

    project_slug: str
    branch: str


class GitCheckoutResponse(BaseModel):
    """Response from checkout."""

    branch: str
    project_slug: str


# =============================================================================
# DIFF
# =============================================================================


class GitDiffResponse(BaseModel):
    """Git diff response."""

    project_slug: str
    staged: bool
    file_path: str | None = None
    diff: str
    files_changed: int = 0


# =============================================================================
# COMMIT
# =============================================================================


class GitCommitRequest(BaseModel):
    """Request to create a commit."""

    project_slug: str
    task_id: UUID
    # Commit message fields
    message: str = Field(
        ...,
        min_length=10,
        max_length=200,
        description="Short description of changes (10-200 chars)",
    )
    commit_type: str = Field(
        ...,
        pattern=r"^(feat|fix|chore|docs|refactor|test|style|perf|ci|build)$",
        description="Conventional commit type",
    )
    scope: str | None = Field(None, description="Module/component affected")
    body: str | None = Field(None, description="Detailed explanation of changes")
    # Files to stage (None = stage all)
    files: list[str] | None = None


class GitCommitResponse(BaseModel):
    """Response from commit creation."""

    commit_hash: str
    message: str
    files_changed: int
    insertions: int = 0
    deletions: int = 0


# =============================================================================
# PUSH
# =============================================================================


class GitPushRequest(BaseModel):
    """Request to push commits."""

    project_slug: str
    task_id: UUID
    force: bool = False


class GitPushResponse(BaseModel):
    """Response from push."""

    branch: str
    commits_pushed: int
    remote: str
    ready_for_pr: bool = False


# =============================================================================
# PULL REQUEST
# =============================================================================


class GitCreatePRRequest(BaseModel):
    """Request to create a pull request."""

    project_slug: str
    task_id: UUID
    # PR content (auto-generated from templates if not provided)
    title: str | None = Field(None, description="PR title (auto-generated if not set)")
    body: str | None = Field(None, description="PR body (auto-generated if not set)")
    # PR type determines template used
    is_root_pr: bool = Field(
        False,
        description="Root task PR (CEO level) vs internal merge (PM level)",
    )


class GitCreatePRResponse(BaseModel):
    """Response from PR creation."""

    pr_number: int
    pr_url: str
    title: str
    source_branch: str
    target_branch: str


class GitMergePRRequest(BaseModel):
    """Request to merge a pull request."""

    project_slug: str
    pr_number: int
    task_id: UUID
    merge_method: str = Field(default="squash", pattern=r"^(merge|squash|rebase)$")


class GitMergePRResponse(BaseModel):
    """Response from PR merge."""

    pr_number: int
    merged: bool
    merge_commit: str | None = None
    target_branch: str


# =============================================================================
# PULL
# =============================================================================


class GitPullRequest(BaseModel):
    """Request to pull latest changes from origin."""

    project_slug: str
    task_id: UUID | None = None


class GitPullResponse(BaseModel):
    """Response from git pull — branch status after the pull."""

    project_slug: str
    current_branch: str
    has_changes: bool
    staged_files: list[str] = []
    unstaged_files: list[str] = []
    untracked_files: list[str] = []
    ahead: int = 0
    behind: int = 0


# =============================================================================
# FETCH
# =============================================================================


class GitFetchRequest(BaseModel):
    """Request to fetch changes from origin without merging."""

    project_slug: str
    task_id: UUID | None = None


class GitFetchResponse(BaseModel):
    """Response from git fetch — branch status after the fetch."""

    project_slug: str
    current_branch: str
    has_changes: bool
    staged_files: list[str] = []
    unstaged_files: list[str] = []
    untracked_files: list[str] = []
    ahead: int = 0
    behind: int = 0


# =============================================================================
# REBASE
# =============================================================================


class GitRebaseRequest(BaseModel):
    """Request to rebase the current branch onto a target branch."""

    project_slug: str
    task_id: UUID | None = None
    target_branch: str

    @field_validator("target_branch")
    @classmethod
    def _validate_target_branch(cls, v: str) -> str:
        if v.startswith("-"):
            raise ValueError(
                "INVALID_TARGET_BRANCH: target_branch must not start with '-'"
            )
        if v in ("master", "main"):
            raise ValueError(
                f"PROTECTED_BRANCH: Cannot rebase onto '{v}'; "
                "target_branch must not be 'master' or 'main'"
            )
        return v


class GitRebaseResponse(BaseModel):
    """Response from git rebase.

    On success: conflict=False, conflicted_files=[].
    On conflict: conflict=True, conflicted_files lists the unmerged paths;
    the rebase has been aborted so the workspace is clean.
    """

    project_slug: str
    conflict: bool = False
    conflicted_files: list[str] = []


# =============================================================================
# GATEWAY-LAYER LIGHTWEIGHT SCHEMAS
#
# These simpler schemas are used by the MCP gateway layer and services that
# don't need the full Git* request payload. All fields beyond project_slug
# are optional to allow callers that don't yet carry task context.
# =============================================================================


class PullRequest(BaseModel):
    """Lightweight pull request used by the gateway / MCP layer."""

    project_slug: str
    task_id: UUID | None = None


class FetchRequest(BaseModel):
    """Lightweight fetch request used by the gateway / MCP layer."""

    project_slug: str
    task_id: UUID | None = None


class RebaseRequest(BaseModel):
    """Lightweight rebase request used by the gateway / MCP layer.

    Validates ``target_branch`` to prevent accidental rebases onto
    protected branches or shell-injection via leading ``-``.
    """

    project_slug: str
    task_id: UUID | None = None
    target_branch: str

    @field_validator("target_branch")
    @classmethod
    def _validate_target_branch(cls, v: str) -> str:
        if v.startswith("-"):
            raise ValueError(
                "INVALID_TARGET_BRANCH: target_branch must not start with '-'"
            )
        if v in ("master", "main"):
            raise ValueError(
                f"PROTECTED_BRANCH: Cannot rebase onto '{v}'; "
                "target_branch must not be 'master' or 'main'"
            )
        return v
