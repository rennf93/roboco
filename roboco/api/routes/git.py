"""
Git API Routes

Git operations for agents working on code tasks.
These endpoints are called by the Git MCP Server.

Workspace Structure:
    Each agent gets their own workspace (git clone) for a project:

    {workspaces_root}/
    └── {project-slug}/
        └── {team}/
            └── {agent-slug}/
                └── [git repo files]

    Example:
        /data/workspaces/roboco/backend/be-dev-1/
        /data/workspaces/roboco/backend/be-dev-2/

    This allows multiple agents to work on the same project in parallel,
    each on their own branch, without file conflicts.
"""

import subprocess
from pathlib import Path
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
from roboco.config import settings
from roboco.services.project import get_project_service
from roboco.services.workspace import WorkspaceError, get_workspace_service
from roboco.utils.converters import require_uuid

router = APIRouter()

# Git command timeout in seconds
_GIT_TIMEOUT = 30

# Expected number of parts in rev-list output (ahead/behind count)
_REV_LIST_PARTS = 2

# Expected number of parts in log format output
_LOG_FORMAT_PARTS = 5


async def _run_git(
    workspace: Path,
    args: list[str],
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a git command in the workspace (non-blocking)."""
    import asyncio

    def _run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            check=check,
        )

    try:
        return await asyncio.to_thread(_run)
    except subprocess.TimeoutExpired as e:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Git command timed out: {' '.join(args)}",
        ) from e
    except subprocess.CalledProcessError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Git command failed: {e.stderr or e.stdout}",
        ) from e


async def _get_workspace(
    db: DbSession,
    project_slug: str,
    agent_id: UUID | None = None,
) -> Path:
    """
    Get the workspace path for an agent on a project.

    Uses multi-agent workspace structure:
        {workspaces_root}/{project_slug}/{team}/{agent_slug}/

    If workspace doesn't exist and auto_clone is enabled, clones the repo.

    Args:
        db: Database session
        project_slug: Project identifier
        agent_id: Agent UUID (uses workspace service to resolve path)

    Returns:
        Path to the workspace directory

    Raises:
        HTTPException: If project not found or workspace setup fails
    """
    # Get project info
    project_service = get_project_service(db)
    project = await project_service.get_by_slug(project_slug)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project '{project_slug}' not found",
        )

    # If no agent_id, fall back to legacy workspace_path (for backwards compat)
    if agent_id is None:
        if not project.workspace_path:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Project '{project_slug}' has no workspace configured "
                "and no agent_id provided for dynamic workspace resolution",
            )
        workspace = Path(project.workspace_path)
        if not workspace.exists():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Workspace path does not exist: {workspace}",
            )
        return workspace

    # Use workspace service for multi-agent workspace resolution
    workspace_service = get_workspace_service(db)

    try:
        if settings.workspace_auto_clone:
            # Ensure workspace exists (clone if needed)
            workspace = await workspace_service.ensure_workspace(
                project_slug=project_slug,
                agent_id=agent_id,
                git_url=project.git_url,
                default_branch=project.default_branch or "main",
            )
        else:
            # Just resolve path, don't auto-clone
            workspace = await workspace_service.resolve_workspace(
                project_slug=project_slug,
                agent_id=agent_id,
            )
            if not workspace.exists():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Workspace does not exist: {workspace}. "
                    "Clone the repository first or enable auto_clone.",
                )
    except WorkspaceError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    return workspace


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
    workspace = await _get_workspace(db, project_slug, agent.agent_id)

    # Get current branch
    branch_result = await _run_git(workspace, ["branch", "--show-current"])
    current_branch = branch_result.stdout.strip()

    # Get status
    status_result = await _run_git(workspace, ["status", "--porcelain"])
    lines = status_result.stdout.strip().split("\n") if status_result.stdout else []

    staged_files = []
    unstaged_files = []
    untracked_files = []

    for line in lines:
        if not line:
            continue
        status_code = line[:2]
        file_path = line[3:]

        if status_code[0] in "MADRC":  # Staged
            staged_files.append(file_path)
        if status_code[1] in "MADRC":  # Unstaged
            unstaged_files.append(file_path)
        if status_code == "??":  # Untracked
            untracked_files.append(file_path)

    # Get ahead/behind
    ahead, behind = 0, 0
    try:
        rev_cmd = f"{current_branch}...origin/{current_branch}"
        rev_result = await _run_git(
            workspace,
            ["rev-list", "--left-right", "--count", rev_cmd],
            check=False,
        )
        if rev_result.returncode == 0:
            parts = rev_result.stdout.strip().split()
            if len(parts) == _REV_LIST_PARTS:
                ahead, behind = int(parts[0]), int(parts[1])
    except Exception:
        pass

    return GitStatusResponse(
        project_slug=project_slug,
        current_branch=current_branch,
        has_changes=bool(staged_files or unstaged_files or untracked_files),
        staged_files=staged_files,
        unstaged_files=unstaged_files,
        untracked_files=untracked_files,
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
    workspace = await _get_workspace(db, project_slug, agent.agent_id)

    # Get current branch if not specified
    if not branch:
        branch_result = await _run_git(workspace, ["branch", "--show-current"])
        branch = branch_result.stdout.strip()

    # Get log with format
    log_format = "%H|%h|%s|%an|%aI"
    log_result = await _run_git(
        workspace,
        ["log", f"--format={log_format}", f"-n{limit}", branch],
    )

    commits = []
    for line in log_result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("|", 4)
        if len(parts) == _LOG_FORMAT_PARTS:
            from datetime import datetime

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
    workspace = await _get_workspace(db, project_slug, agent.agent_id)

    # Get current branch
    current_result = await _run_git(workspace, ["branch", "--show-current"])
    current_branch = current_result.stdout.strip()

    # Get branches
    args = ["branch", "--format=%(refname:short)|%(objectname:short)"]
    if include_remote:
        args.append("-a")

    branch_result = await _run_git(workspace, args)
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
    workspace = await _get_workspace(db, project_slug, agent.agent_id)

    args = ["diff"]
    if staged:
        args.append("--staged")
    if file_path:
        args.extend(["--", file_path])

    diff_result = await _run_git(workspace, args)

    # Count files changed
    stat_args = ["diff", "--stat"]
    if staged:
        stat_args.append("--staged")
    stat_result = await _run_git(workspace, stat_args)
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
    workspace = await _get_workspace(db, data.project_slug, agent.agent_id)

    # Stage files
    if data.files:
        for file in data.files:
            await _run_git(workspace, ["add", file])
    else:
        await _run_git(workspace, ["add", "-A"])

    # Create commit with task ID prefix
    message = f"[{data.task_id[:8]}] {data.message}"
    await _run_git(workspace, ["commit", "-m", message])

    # Get commit info
    log_result = await _run_git(workspace, ["log", "-1", "--format=%H|%s"])
    parts = log_result.stdout.strip().split("|")
    commit_hash = parts[0] if parts else "unknown"

    # Get stats
    stat_result = await _run_git(workspace, ["diff", "--stat", "HEAD~1..HEAD"])
    insertions, deletions, files_changed = 0, 0, 0
    for line in stat_result.stdout.split("\n"):
        if "insertion" in line or "deletion" in line:
            parts = line.split(",")
            for part in parts:
                if "insertion" in part:
                    insertions = int(part.strip().split()[0])
                if "deletion" in part:
                    deletions = int(part.strip().split()[0])
                if "file" in part:
                    files_changed = int(part.strip().split()[0])

    # Link commit to task (ensures traceability)
    from roboco.services.task import get_task_service
    from roboco.services.work_session import get_work_session_service

    try:
        task_uuid = UUID(data.task_id)

        # Add commit to task record
        task_service = get_task_service(db)
        await task_service.add_commit(
            task_id=task_uuid,
            hash=commit_hash,
            message=data.message,  # Store original message without prefix
            agent_id=agent.agent_id,
        )

        # If task has a work session, add commit there too
        task = await task_service.get(task_uuid)
        if task and task.work_session_id:
            work_session_service = get_work_session_service(db)
            await work_session_service.add_commit(
                require_uuid(task.work_session_id), commit_hash
            )

        await db.commit()
    except Exception:
        # Don't fail the commit response if linking fails
        # The commit was still made successfully
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
    workspace = await _get_workspace(db, data.project_slug, agent.agent_id)

    # Get current branch
    branch_result = await _run_git(workspace, ["branch", "--show-current"])
    branch = branch_result.stdout.strip()

    # Get commits to push
    count_result = await _run_git(
        workspace,
        ["rev-list", "--count", f"origin/{branch}..{branch}"],
        check=False,
    )
    commits_to_push = (
        int(count_result.stdout.strip()) if count_result.returncode == 0 else 0
    )

    # Push
    args = ["push", "-u", "origin", branch]
    if data.force:
        args.insert(1, "--force")

    await _run_git(workspace, args)

    return GitPushResponse(
        branch=branch,
        commits_pushed=commits_to_push,
        remote="origin",
        ready_for_pr=commits_to_push > 0,
    )


@router.post("/branch/create", response_model=GitCreateBranchResponse)
async def create_branch(
    data: GitCreateBranchRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> GitCreateBranchResponse:
    """Create a task branch (PM only)."""
    workspace = await _get_workspace(db, data.project_slug, agent.agent_id)

    # Get team from agent context
    team = agent.team or "unknown"

    # Generate branch name
    branch_name = f"{data.branch_type}/{team}/{data.task_id[:8]}"

    # Get base branch
    base_branch = data.parent_branch
    if not base_branch:
        project_service = get_project_service(db)
        project = await project_service.get_by_slug(data.project_slug)
        base_branch = str(project.default_branch) if project else "main"

    # Create and checkout branch
    await _run_git(workspace, ["checkout", base_branch])
    await _run_git(workspace, ["pull", "origin", base_branch])
    await _run_git(workspace, ["checkout", "-b", branch_name])
    await _run_git(workspace, ["push", "-u", "origin", branch_name])

    return GitCreateBranchResponse(
        branch_name=branch_name,
        created_from=base_branch,
        project_slug=data.project_slug,
    )


@router.post("/checkout", response_model=GitCheckoutResponse)
async def checkout_branch(
    data: GitCheckoutRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> GitCheckoutResponse:
    """Checkout a branch."""
    workspace = await _get_workspace(db, data.project_slug, agent.agent_id)

    await _run_git(workspace, ["checkout", data.branch])

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
    This is part of the parallel execution in awaiting_documentation:
    - Documenter sets docs_complete=True
    - Developer sets pr_created=True (this endpoint)
    - When BOTH are true, task transitions to awaiting_pm_review
    """
    workspace = await _get_workspace(db, data.project_slug, agent.agent_id)

    # Get current branch
    branch_result = await _run_git(workspace, ["branch", "--show-current"])
    source_branch = branch_result.stdout.strip()

    # Get target branch (from project default or parent task)
    project_service = get_project_service(db)
    project = await project_service.get_by_slug(data.project_slug)
    target_branch = str(project.default_branch) if project else "main"

    # Create PR using gh CLI (wrapped in thread to avoid blocking)
    import asyncio

    def _create_pr() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "gh",
                "pr",
                "create",
                "--title",
                data.title,
                "--body",
                data.body,
                "--base",
                target_branch,
                "--head",
                source_branch,
            ],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            check=True,
        )

    try:
        result = await asyncio.to_thread(_create_pr)
        # Parse PR URL from output
        pr_url = result.stdout.strip()
        pr_number = int(pr_url.split("/")[-1]) if pr_url else 0
    except subprocess.CalledProcessError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create PR: {e.stderr or e.stdout}",
        ) from e
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="GitHub CLI (gh) not found. Please install it.",
        ) from e

    # Mark pr_created=True on the task (parallel execution with documenter)
    # This triggers transition to awaiting_pm_review if docs are also complete
    from uuid import UUID

    from roboco.services.task import TaskService

    task_service = TaskService(db)
    await task_service.mark_pr_created(
        task_id=UUID(data.task_id),
        pr_number=pr_number,
        pr_url=pr_url,
    )

    return GitCreatePRResponse(
        pr_number=pr_number,
        pr_url=pr_url,
        title=data.title,
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
    workspace = await _get_workspace(db, data.project_slug, agent.agent_id)

    # Merge PR using gh CLI (wrapped in thread to avoid blocking)
    import asyncio

    def _merge_pr() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "gh",
                "pr",
                "merge",
                str(data.pr_number),
                f"--{data.merge_method}",
                "--delete-branch",
            ],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            check=True,
        )

    try:
        await asyncio.to_thread(_merge_pr)
    except subprocess.CalledProcessError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to merge PR: {e.stderr or e.stdout}",
        ) from e

    # Get target branch
    project_service = get_project_service(db)
    project = await project_service.get_by_slug(data.project_slug)
    target_branch = str(project.default_branch) if project else "main"

    # Get merge commit
    await _run_git(workspace, ["checkout", target_branch])
    await _run_git(workspace, ["pull"])
    log_result = await _run_git(workspace, ["log", "-1", "--format=%H"])
    merge_commit = log_result.stdout.strip()

    return GitMergePRResponse(
        pr_number=data.pr_number,
        merged=True,
        merge_commit=merge_commit,
        target_branch=target_branch,
    )
