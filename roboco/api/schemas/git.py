"""
Git API Schemas

Request/response models for git operation endpoints.
"""

from datetime import datetime

from pydantic import BaseModel, Field

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
    task_id: str
    branch_type: str = Field(..., pattern=r"^(feature|bug|chore|docs|hotfix)$")
    agent_id: str
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
    agent_id: str


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
    task_id: str
    agent_id: str
    # Commit message fields
    message: str = Field(..., description="Short description of changes")
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
    task_id: str
    agent_id: str
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
    task_id: str
    agent_id: str
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
    task_id: str
    merge_method: str = Field(default="squash", pattern=r"^(merge|squash|rebase)$")
    agent_id: str


class GitMergePRResponse(BaseModel):
    """Response from PR merge."""

    pr_number: int
    merged: bool
    merge_commit: str | None = None
    target_branch: str
