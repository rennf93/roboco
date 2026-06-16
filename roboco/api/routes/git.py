"""
Git API Routes

Git operations for agents working on code tasks.
These endpoints are called by the Git MCP Server.

Workspace Structure:
    Each agent gets their own workspace (git clone) for a project:

    {workspaces_root}/
    +-- {project-slug}/
        +-- {team}/
            +-- {agent-slug}/
                +-- [git repo files]

    Example:
        /data/workspaces/roboco/backend/be-dev-1/
        /data/workspaces/roboco/backend/be-dev-2/

    This allows multiple agents to work on the same project in parallel,
    each on their own branch, without file conflicts.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.api.deps import CurrentAgentContext, DbSession
from roboco.api.schemas.git import (
    BranchInfo,
    CommitInfo,
    GitBranchListResponse,
    GitCheckoutRequest,
    GitCheckoutResponse,
    GitCommitRequest,
    GitCommitResponse,
    GitCreateBranchRequest,
    GitCreateBranchResponse,
    GitCreatePRRequest,
    GitCreatePRResponse,
    GitDiffResponse,
    GitFetchRequest,
    GitFetchResponse,
    GitLogResponse,
    GitMergePRRequest,
    GitMergePRResponse,
    GitPullRequest,
    GitPullResponse,
    GitPushRequest,
    GitPushResponse,
    GitRebaseRequest,
    GitRebaseResponse,
    GitStatusResponse,
)
from roboco.exceptions import GitCommandError, GitError, GitTimeoutError
from roboco.logging import get_logger
from roboco.services.base import (
    NotFoundError,
    ServiceError,
    UnauthorizedError,
    ValidationError,
)
from roboco.services.git import get_git_service
from roboco.services.project import get_project_service

logger = get_logger(__name__)

router = APIRouter()

# Expected number of parts in log format output
_LOG_FORMAT_PARTS = 5

# Catch tuple for service-layer errors. `roboco.exceptions.GitError` is a
# distinct class from `roboco.services.base.ServiceError` (it extends the
# `roboco.exceptions.ServiceError` class), so listing both is required for
# git timeouts/command failures to be translated to 504/500 instead of
# bubbling as 500 Internal Server Errors with no `detail`.
_TranslatableError = (ServiceError, GitError)


def _translate_error(e: ServiceError | GitError) -> HTTPException:
    """Translate service errors to HTTP exceptions."""
    if isinstance(e, NotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=e.message)
    if isinstance(e, UnauthorizedError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=e.message)
    if isinstance(e, ValidationError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=e.message)
    if isinstance(e, GitTimeoutError):
        return HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=e.message
        )
    if isinstance(e, GitCommandError):
        return HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=e.message
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=e.message
    )


async def _resolve_project_slug(identifier: str, db: AsyncSession) -> str:
    """Resolve a project identifier (UUID string or slug) to its slug.

    Callers pass whatever string they have — a human-readable slug like
    "roboco" or a UUID like "3fa85f64-5717-4562-b3fc-2c963f66afa6".
    We try UUID first; if the string is not a valid UUID we treat it as
    a slug directly.  In both cases we verify the project exists and
    return the canonical slug so downstream git-service calls work.
    """
    service = get_project_service(db)
    try:
        uuid = UUID(identifier)
        project = await service.get(uuid)
    except ValueError:
        project = await service.get_by_slug(identifier)

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project not found: {identifier}",
        )
    return str(project.slug)


# =============================================================================
# READ-ONLY ENDPOINTS
# =============================================================================


@router.get("/status", response_model=GitStatusResponse)
async def get_git_status(
    db: DbSession,
    agent: CurrentAgentContext,
    project_slug: str = Query(...),
    _task_id: str | None = Query(default=None),
) -> GitStatusResponse:
    """Get git status for a project."""
    project_slug = await _resolve_project_slug(project_slug, db)
    git_service = get_git_service(db)

    try:
        workspace = await git_service.get_workspace(project_slug, agent.agent_id)
        (
            current_branch,
            has_changes,
            staged,
            unstaged,
            untracked,
            ahead,
            behind,
        ) = await git_service.get_status(workspace)
    except _TranslatableError as e:
        raise _translate_error(e) from e

    return GitStatusResponse(
        project_slug=project_slug,
        current_branch=current_branch,
        has_changes=has_changes,
        staged_files=staged,
        unstaged_files=unstaged,
        untracked_files=untracked,
        ahead=ahead,
        behind=behind,
    )


@router.get("/log", response_model=GitLogResponse)
async def get_git_log(
    db: DbSession,
    agent: CurrentAgentContext,
    project_slug: str = Query(...),
    limit: int = Query(default=10, le=50),
    branch: str | None = Query(default=None),
) -> GitLogResponse:
    """Get git log for a project."""
    project_slug = await _resolve_project_slug(project_slug, db)
    git_service = get_git_service(db)

    try:
        workspace = await git_service.get_workspace(project_slug, agent.agent_id)

        # Get current branch if not specified
        if not branch:
            branch = await git_service.get_current_branch(workspace)

        # Get log with format. Don't raise if the branch doesn't exist in
        # this workspace yet — that's a normal race (branch created in a
        # different agent's clone, not yet fetched here). Return empty.
        log_format = "%H|%h|%s|%an|%aI"
        log_result = await git_service._run_git(
            workspace,
            ["log", f"--format={log_format}", f"-n{limit}", branch],
            check=False,
        )
        if log_result.returncode != 0:
            logger.info(
                "git log on missing/unknown ref; returning empty",
                project_slug=project_slug,
                branch=branch,
                stderr=log_result.stderr[:200] if log_result.stderr else "",
            )
            return GitLogResponse(project_slug=project_slug, branch=branch, commits=[])
    except _TranslatableError as e:
        raise _translate_error(e) from e

    commits = []
    for line in log_result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("|", 4)
        if len(parts) == _LOG_FORMAT_PARTS:
            commits.append(
                CommitInfo(
                    hash=parts[0],
                    short_hash=parts[1],
                    message=parts[2],
                    author=parts[3],
                    date=datetime.fromisoformat(parts[4]),
                )
            )

    return GitLogResponse(
        project_slug=project_slug,
        branch=branch,
        commits=commits,
    )


@router.get("/branches", response_model=GitBranchListResponse)
async def list_branches(
    db: DbSession,
    agent: CurrentAgentContext,
    project_slug: str = Query(...),
    include_remote: bool = Query(default=False),
) -> GitBranchListResponse:
    """List git branches for a project."""
    project_slug = await _resolve_project_slug(project_slug, db)
    git_service = get_git_service(db)

    try:
        workspace = await git_service.get_workspace(project_slug, agent.agent_id)
        current_branch = await git_service.get_current_branch(workspace)

        # Get branches
        args = ["branch", "--format=%(refname:short)|%(objectname:short)"]
        if include_remote:
            args.append("-a")

        branch_result = await git_service._run_git(workspace, args)
    except _TranslatableError as e:
        raise _translate_error(e) from e

    branches = []
    for line in branch_result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("|")
        name = parts[0]
        last_commit = parts[1] if len(parts) > 1 else None

        is_remote = name.startswith("remotes/")
        if is_remote:
            name = name.replace("remotes/origin/", "")

        branches.append(
            BranchInfo(
                name=name,
                is_current=name == current_branch,
                is_remote=is_remote,
                last_commit=last_commit,
            )
        )

    return GitBranchListResponse(
        project_slug=project_slug,
        current_branch=current_branch,
        branches=branches,
    )


@router.get("/diff", response_model=GitDiffResponse)
async def get_git_diff(
    db: DbSession,
    agent: CurrentAgentContext,
    project_slug: str = Query(...),
    staged: bool = Query(default=False),
    file_path: str | None = Query(default=None),
) -> GitDiffResponse:
    """Get git diff for a project."""
    project_slug = await _resolve_project_slug(project_slug, db)
    git_service = get_git_service(db)

    try:
        workspace = await git_service.get_workspace(project_slug, agent.agent_id)

        args = ["diff"]
        if staged:
            args.append("--staged")
        if file_path:
            args.extend(["--", file_path])

        diff_result = await git_service._run_git(workspace, args)

        # Count files changed
        stat_args = ["diff", "--stat"]
        if staged:
            stat_args.append("--staged")
        stat_result = await git_service._run_git(workspace, stat_args)
    except _TranslatableError as e:
        raise _translate_error(e) from e

    files_changed = stat_result.stdout.count("\n") - 1 if stat_result.stdout else 0

    return GitDiffResponse(
        project_slug=project_slug,
        staged=staged,
        file_path=file_path,
        diff=diff_result.stdout,
        files_changed=max(0, files_changed),
    )


# =============================================================================
# WRITE ENDPOINTS
# =============================================================================


@router.post("/commit", response_model=GitCommitResponse)
async def create_commit(
    data: GitCommitRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> GitCommitResponse:
    """Create a git commit and link it to the task."""
    git_service = get_git_service(db)
    try:
        (
            commit_hash,
            message,
            files_changed,
            insertions,
            deletions,
        ) = await git_service.commit_for_task(agent.agent_id, data)
    except _TranslatableError as e:
        raise _translate_error(e) from e

    return GitCommitResponse(
        commit_hash=commit_hash,
        message=message,
        files_changed=files_changed,
        insertions=insertions,
        deletions=deletions,
    )


@router.post("/push", response_model=GitPushResponse)
async def push_commits(
    data: GitPushRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> GitPushResponse:
    """Push commits to remote."""
    git_service = get_git_service(db)
    try:
        branch, commits_pushed = await git_service.push_for_task(
            agent.agent_id, agent.role, data
        )
    except _TranslatableError as e:
        raise _translate_error(e) from e

    return GitPushResponse(
        branch=branch,
        commits_pushed=commits_pushed,
        remote="origin",
        ready_for_pr=commits_pushed > 0,
    )


@router.post("/branch/create", response_model=GitCreateBranchResponse)
async def create_branch(
    data: GitCreateBranchRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> GitCreateBranchResponse:
    """Create a task branch (PM only).

    Uses hierarchical branch naming: {type}/{team}/{root}/{sub}/{subsub}
    """
    git_service = get_git_service(db)
    try:
        branch_name, created_from = await git_service.create_branch_for_task(
            agent.agent_id, data
        )
    except _TranslatableError as e:
        raise _translate_error(e) from e

    return GitCreateBranchResponse(
        branch_name=branch_name,
        created_from=created_from,
        project_slug=data.project_slug,
    )


@router.post("/checkout", response_model=GitCheckoutResponse)
async def checkout_branch(
    data: GitCheckoutRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> GitCheckoutResponse:
    """Checkout a branch.

    Restricted to branches the agent has a legitimate reason to be on:
    any of their own assigned tasks' branches, or the project's default
    base branch for read-only inspection. Prevents agents from jumping
    to `master` / sibling branches and committing there by accident.
    """
    git_service = get_git_service(db)
    try:
        await git_service.checkout_branch_for_agent(agent.agent_id, data)
    except _TranslatableError as e:
        raise _translate_error(e) from e

    return GitCheckoutResponse(
        branch=data.branch,
        project_slug=data.project_slug,
    )


@router.post("/pr/create", response_model=GitCreatePRResponse)
async def create_pull_request(
    data: GitCreatePRRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> GitCreatePRResponse:
    """Create a pull request and sync task/work-session state atomically."""
    git_service = get_git_service(db)
    try:
        (
            pr_number,
            pr_url,
            title,
            source_branch,
            target_branch,
        ) = await git_service.create_pr_for_task(agent.agent_id, data)
    except _TranslatableError as e:
        raise _translate_error(e) from e

    return GitCreatePRResponse(
        pr_number=pr_number,
        pr_url=pr_url,
        title=title,
        source_branch=source_branch,
        target_branch=target_branch,
    )


@router.post("/pr/merge", response_model=GitMergePRResponse)
async def merge_pull_request(
    data: GitMergePRRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> GitMergePRResponse:
    """Merge a PR (PM/CEO). Auto-completes the task on role match."""
    git_service = get_git_service(db)
    try:
        target_branch, merge_commit = await git_service.merge_pr_for_task(
            agent.agent_id, agent.role, data
        )
    except _TranslatableError as e:
        raise _translate_error(e) from e

    return GitMergePRResponse(
        pr_number=data.pr_number,
        merged=True,
        merge_commit=merge_commit,
        target_branch=target_branch,
    )


@router.post("/pull", response_model=GitPullResponse)
async def pull_commits(
    data: GitPullRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> GitPullResponse:
    """Pull latest changes from origin into the agent workspace."""
    project_slug = await _resolve_project_slug(data.project_slug, db)
    git_service = get_git_service(db)

    try:
        workspace = await git_service.get_workspace(project_slug, agent.agent_id)
        (
            current_branch,
            has_changes,
            staged,
            unstaged,
            untracked,
            ahead,
            behind,
        ) = await git_service.pull(workspace)
    except _TranslatableError as e:
        raise _translate_error(e) from e

    return GitPullResponse(
        project_slug=project_slug,
        current_branch=current_branch,
        has_changes=has_changes,
        staged_files=staged,
        unstaged_files=unstaged,
        untracked_files=untracked,
        ahead=ahead,
        behind=behind,
    )


@router.post("/fetch", response_model=GitFetchResponse)
async def fetch_commits(
    data: GitFetchRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> GitFetchResponse:
    """Fetch changes from origin without merging."""
    project_slug = await _resolve_project_slug(data.project_slug, db)
    git_service = get_git_service(db)

    try:
        workspace = await git_service.get_workspace(project_slug, agent.agent_id)
        (
            current_branch,
            has_changes,
            staged,
            unstaged,
            untracked,
            ahead,
            behind,
        ) = await git_service.fetch(workspace)
    except _TranslatableError as e:
        raise _translate_error(e) from e

    return GitFetchResponse(
        project_slug=project_slug,
        current_branch=current_branch,
        has_changes=has_changes,
        staged_files=staged,
        unstaged_files=unstaged,
        untracked_files=untracked,
        ahead=ahead,
        behind=behind,
    )


@router.post("/rebase", response_model=GitRebaseResponse)
async def rebase_branch(
    data: GitRebaseRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> GitRebaseResponse:
    """Rebase the current branch onto target_branch.

    On conflict: aborts the rebase and returns conflict=True with the
    list of conflicted files.  On success: returns conflict=False.
    """
    project_slug = await _resolve_project_slug(data.project_slug, db)
    git_service = get_git_service(db)

    try:
        workspace = await git_service.get_workspace(project_slug, agent.agent_id)
        conflict, conflicted_files = await git_service.rebase(
            workspace, data.target_branch
        )
    except _TranslatableError as e:
        raise _translate_error(e) from e

    return GitRebaseResponse(
        project_slug=project_slug,
        conflict=conflict,
        conflicted_files=conflicted_files,
    )
