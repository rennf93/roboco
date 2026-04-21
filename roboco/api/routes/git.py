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
from roboco.logging import get_logger
from roboco.models.base import AgentRole, TaskStatus
from roboco.services.base import NotFoundError, ServiceError, ValidationError
from roboco.services.git import get_git_service
from roboco.services.project import get_project_service
from roboco.services.task import get_task_service
from roboco.services.work_session import get_work_session_service
from roboco.utils.converters import require_uuid

logger = get_logger(__name__)

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
        (
            current_branch,
            has_changes,
            staged,
            unstaged,
            untracked,
            ahead,
            behind,
        ) = await git_service.get_status(workspace)
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

        (
            commit_hash,
            message,
            files_changed,
            insertions,
            deletions,
        ) = await git_service.create_commit(workspace, agent.agent_id, data)
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
    project_service = get_project_service(db)

    try:
        workspace = await git_service.get_workspace(data.project_slug, agent.agent_id)

        task_uuid = UUID(data.task_id)
        task = await task_service.get(task_uuid)
        project = await project_service.get_by_slug(data.project_slug)

        project_cell: str | None = None
        if project and project.assigned_cell:
            project_cell = (
                project.assigned_cell.value
                if hasattr(project.assigned_cell, "value")
                else str(project.assigned_cell)
            )

        task_team: str | None = None
        if task and task.team:
            task_team = (
                task.team.value if hasattr(task.team, "value") else str(task.team)
            )

        if project_cell == "fullstack":
            effective_team = task_team or "cross"
            team_for_branch = f"{data.project_slug}/{effective_team}"
        elif project_cell:
            team_for_branch = project_cell
        elif task_team:
            team_for_branch = task_team
        else:
            team_for_branch = "cross"

        branch_name, created_from = await git_service.create_branch(
            workspace, team_for_branch, data
        )

        await task_service.update(task_uuid, branch_name=branch_name)

        # NOTE: Do NOT propagate branch_name to children.
        # Each task creates its OWN branch when claimed, forking from parent's branch.
        # Children's branches follow hierarchy: parent--child--grandchild

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

    After PR creation, marks pr_created=True on the task and records
    PR info on the associated work session.
    Uses templates to auto-generate PR title/body if not provided.
    """
    git_service = get_git_service(db)
    task_service = get_task_service(db)
    work_session_service = get_work_session_service(db)

    try:
        workspace = await git_service.get_workspace(data.project_slug, agent.agent_id)

        (
            pr_number,
            pr_url,
            title,
            source_branch,
            target_branch,
        ) = await git_service.create_pull_request(workspace, data)
    except ServiceError as e:
        raise _translate_error(e) from e

    # Mark pr_created=True on the task
    task_uuid = UUID(data.task_id)
    task = await task_service.mark_pr_created(
        task_id=task_uuid,
        pr_number=pr_number,
        pr_url=pr_url,
    )

    # Record PR on the work session so CEO approval guard can pass
    if task and task.work_session_id:
        await work_session_service.create_pr(
            require_uuid(task.work_session_id),
            pr_number,
            pr_url,
        )

    await db.commit()

    return GitCreatePRResponse(
        pr_number=pr_number,
        pr_url=pr_url,
        title=title,
        source_branch=source_branch,
        target_branch=target_branch,
    )


async def _auto_complete_on_merge(
    db: DbSession,
    task_uuid: UUID,
    agent: CurrentAgentContext,
) -> None:
    """Auto-transition task after PR merge based on current status + merger role.

    - awaiting_ceo_approval + CEO merger → ceo_approve (→ completed)
    - awaiting_pm_review + PM merger → complete (may escalate or finish)
    Otherwise leave the task alone.
    """
    task_service = get_task_service(db)
    task = await task_service.get(task_uuid)
    if not task:
        return

    status_value = task.status.value if hasattr(task.status, "value") else task.status
    pm_roles = {AgentRole.CELL_PM, AgentRole.MAIN_PM}

    if (
        status_value == TaskStatus.AWAITING_CEO_APPROVAL.value
        and agent.role == AgentRole.CEO
    ):
        await task_service.ceo_approve(task_uuid)
    elif status_value == TaskStatus.AWAITING_PM_REVIEW.value and agent.role in pm_roles:
        await task_service.complete(task_uuid, agent.agent_id)


@router.post("/pr/merge", response_model=GitMergePRResponse)
async def merge_pull_request(
    data: GitMergePRRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> GitMergePRResponse:
    """Merge a pull request using GitHub CLI (PM/CEO).

    After merge, records merge on the work session and auto-completes the
    task when the merger holds the role required for the current state
    (PM for awaiting_pm_review, CEO for awaiting_ceo_approval).
    """
    git_service = get_git_service(db)
    task_service = get_task_service(db)
    work_session_service = get_work_session_service(db)

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

    task_uuid = UUID(data.task_id)
    task = await task_service.get(task_uuid)

    # Record merge on the work session (satisfies ceo_approve's pr_status guard)
    if task and task.work_session_id:
        await work_session_service.merge_pr(
            require_uuid(task.work_session_id),
            agent.agent_id,
        )

    # Auto-transition task to completed based on merger role + current state
    await _auto_complete_on_merge(db, task_uuid, agent)

    await db.commit()

    return GitMergePRResponse(
        pr_number=data.pr_number,
        merged=True,
        merge_commit=merge_commit,
        target_branch=target_branch,
    )
