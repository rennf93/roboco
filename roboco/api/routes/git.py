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
    GitLogResponse,
    GitMergePRRequest,
    GitMergePRResponse,
    GitPushRequest,
    GitPushResponse,
    GitStatusResponse,
)
from roboco.exceptions import GitCommandError, GitTimeoutError
from roboco.services.base import NotFoundError, ServiceError, ValidationError
from roboco.services.git import get_git_service
from roboco.services.task import get_task_service
from roboco.services.work_session import get_work_session_service
from roboco.utils.converters import require_uuid

router = APIRouter()

# Expected number of parts in log format output
_LOG_FORMAT_PARTS = 5


def _translate_error(e: ServiceError) -> HTTPException:
    """Translate service errors to HTTP exceptions."""
    if isinstance(e, NotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=e.message)
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
    git_service = get_git_service(db)

    try:
        workspace = await git_service.get_workspace(project_slug, agent.agent_id)
        current_branch, has_changes, staged, unstaged, untracked, ahead, behind = (
            await git_service.get_status(workspace)
        )
    except ServiceError as e:
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
    git_service = get_git_service(db)

    try:
        workspace = await git_service.get_workspace(project_slug, agent.agent_id)

        # Get current branch if not specified
        if not branch:
            branch = await git_service.get_current_branch(workspace)

        # Get log with format
        log_format = "%H|%h|%s|%an|%aI"
        log_result = await git_service._run_git(
            workspace, ["log", f"--format={log_format}", f"-n{limit}", branch]
        )
    except ServiceError as e:
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
    git_service = get_git_service(db)

    try:
        workspace = await git_service.get_workspace(project_slug, agent.agent_id)
        current_branch = await git_service.get_current_branch(workspace)

        # Get branches
        args = ["branch", "--format=%(refname:short)|%(objectname:short)"]
        if include_remote:
            args.append("-a")

        branch_result = await git_service._run_git(workspace, args)
    except ServiceError as e:
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
    except ServiceError as e:
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
    task_service = get_task_service(db)

    try:
        workspace = await git_service.get_workspace(data.project_slug, agent.agent_id)

        commit_hash, message, files_changed, insertions, deletions = (
            await git_service.create_commit(workspace, agent.agent_id, data)
        )
    except ServiceError as e:
        raise _translate_error(e) from e

    # Link commit to task (ensures traceability)
    task_uuid = UUID(data.task_id)
    try:
        task = await task_service.get(task_uuid)
        await task_service.add_commit(
            task_id=task_uuid,
            hash=commit_hash,
            message=data.message,
            agent_id=agent.agent_id,
        )

        # If task has a work session, add commit there too
        if task and task.work_session_id:
            work_session_service = get_work_session_service(db)
            await work_session_service.add_commit(
                require_uuid(task.work_session_id), commit_hash
            )

        await db.commit()
    except Exception:
        # Don't fail the commit response if linking fails
        pass

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
        workspace = await git_service.get_workspace(data.project_slug, agent.agent_id)
        branch, commits_pushed = await git_service.push(workspace, data.force)
    except ServiceError as e:
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
    task_service = get_task_service(db)

    try:
        workspace = await git_service.get_workspace(data.project_slug, agent.agent_id)

        branch_name, created_from = await git_service.create_branch(
            workspace, agent.team or "unknown", data
        )

        # Store branch name on task
        task_uuid = UUID(data.task_id)
        await task_service.update(task_uuid, branch_name=branch_name)
        await db.commit()
    except ServiceError as e:
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
    """Checkout a branch."""
    git_service = get_git_service(db)

    try:
        workspace = await git_service.get_workspace(data.project_slug, agent.agent_id)
        await git_service.checkout(workspace, data.branch)
    except ServiceError as e:
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
    """Create a pull request using GitHub CLI.

    After PR creation, marks pr_created=True on the task.
    Uses templates to auto-generate PR title/body if not provided.
    """
    git_service = get_git_service(db)
    task_service = get_task_service(db)

    try:
        workspace = await git_service.get_workspace(data.project_slug, agent.agent_id)

        pr_number, pr_url, title, source_branch, target_branch = (
            await git_service.create_pull_request(workspace, data)
        )
    except ServiceError as e:
        raise _translate_error(e) from e

    # Mark pr_created=True on the task
    task_uuid = UUID(data.task_id)
    await task_service.mark_pr_created(
        task_id=task_uuid,
        pr_number=pr_number,
        pr_url=pr_url,
    )

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
    """Merge a pull request using GitHub CLI (PM only)."""
    git_service = get_git_service(db)

    try:
        workspace = await git_service.get_workspace(data.project_slug, agent.agent_id)
        target_branch, merge_commit = await git_service.merge_pull_request(
            workspace=workspace,
            pr_number=data.pr_number,
            merge_method=data.merge_method,
            project_slug=data.project_slug,
        )
    except ServiceError as e:
        raise _translate_error(e) from e

    return GitMergePRResponse(
        pr_number=data.pr_number,
        merged=True,
        merge_commit=merge_commit,
        target_branch=target_branch,
    )
