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
        /data/workspaces/roboco-api/backend/be-dev-1/
        /data/workspaces/roboco-api/backend/be-dev-2/

    This allows multiple agents to work on the same project in parallel,
    each on their own branch, without file conflicts.
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from roboco.api.deps import CurrentAgentContext, DbSession
from roboco.api.schemas.git import (
    BranchInfo,
    CommitInfo,
    GitBranchCleanupRequest,
    GitBranchCleanupResponse,
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
    GitFileContentResponse,
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
from roboco.api.utils.git import (
    _bounded_git_stage,
    _compute_file_range,
    _parse_branch_line,
    _resolve_project_slug,
    _translate_error,
)
from roboco.config import settings
from roboco.exceptions import GitError
from roboco.logging import get_logger
from roboco.models.base import AgentRole
from roboco.security import (
    guard_deco,
    prompt_injection_validator,
    secret_exfil_validator,
)
from roboco.services.base import ServiceError
from roboco.services.git import get_git_service
from roboco.services.task import get_task_service

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


# Roles permitted to rebase branches via the /rebase endpoint.
# Rebase is a history-rewriting operation that should be authorised only by
# PM-level or CEO-level callers. Developers are intentionally excluded:
# they commit to their feature branch and let PMs/CEO manage integration
# rebases. This gate prevents developers from accidentally force-rewriting
# shared branch history.
_REBASE_ALLOWED_ROLES: frozenset[AgentRole] = frozenset(
    {AgentRole.CEO, AgentRole.CELL_PM, AgentRole.MAIN_PM}
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

        # This is the CALLER's own clone, which is never the branch's own
        # author when inspecting another agent's task (QA/PM/documenter
        # reading a dev's branch) — a local ref left over from an earlier
        # inspection can be pinned stale (behind, or diverged after a
        # routine rebase force-push) while origin has since moved. Resolve
        # through _resolve_head_ref (fetch + prefer origin) instead of the
        # bare branch name so this reads the same authoritative tip diff()/
        # read_file_at_branch() do, not whatever this clone happened to
        # have on disk from the last time it looked.
        token = await git_service._token_for_branch(branch)
        head_ref = await git_service._resolve_head_ref(workspace, branch, token=token)

        # Get log with format. Don't raise if the branch doesn't exist in
        # this workspace yet — that's a normal race (branch created in a
        # different agent's clone, not yet fetched here). Return empty.
        # \x1f (ASCII Unit Separator) field delimiter, not "|": a commit
        # SUBJECT can contain "|" (e.g. "curl|sh"), which would shift the
        # split and land the author+date in one field. 0x1F never appears in
        # commit content.
        log_format = "%H%x1f%h%x1f%s%x1f%an%x1f%aI"
        log_result = await git_service._run_git(
            workspace,
            ["log", f"--format={log_format}", f"-n{limit}", head_ref],
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
        parts = line.split("\x1f")
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

        if include_remote:
            # Self-heal orphaned remote-tracking refs (branches deleted
            # upstream via the forge API) before listing them.
            await git_service.prune_remote_best_effort(workspace)

        args = ["branch", "--format=%(refname)|%(objectname:short)"]
        if include_remote:
            args.append("-a")

        branch_result = await git_service._run_git(workspace, args)
    except _TranslatableError as e:
        raise _translate_error(e) from e

    branches = []
    for line in branch_result.stdout.strip().split("\n"):
        parsed = _parse_branch_line(line)
        if parsed is None:
            continue
        name, is_remote, last_commit = parsed
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

    async def _run_diff_stage() -> tuple[Any, Any]:
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
        return diff_result, stat_result

    try:
        workspace = await _bounded_git_stage(
            git_service.get_workspace(project_slug, agent.agent_id),
            timeout=settings.workspace_clone_timeout,
            stage="workspace resolution",
        )
        diff_result, stat_result = await _bounded_git_stage(
            _run_diff_stage(),
            timeout=settings.git_diff_timeout_seconds,
            stage="diff",
        )
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


@router.get("/file", response_model=GitFileContentResponse)
async def get_git_file(
    *,
    db: DbSession,
    agent: CurrentAgentContext,
    branch: str = Query(..., description="Task branch holding the file"),
    path: str = Query(..., description="Repo-relative file path"),
    line: int | None = Query(default=None, ge=1, description="Target line"),
    context: int = Query(default=10, ge=0, le=100, description="Context around line"),
    start: int | None = Query(default=None, ge=1, description="Explicit start line"),
    end: int | None = Query(default=None, ge=1, description="Explicit end line"),
) -> GitFileContentResponse:
    """Return a file's content at a branch tip, optionally sliced to a range.

    Reads straight out of the branch with ``git show`` (``read_file_at_branch``)
    so a reviewer/CEO can read a finding's source lines without a workspace
    mount. A missing file or bad ref yields 404. When `line` is given the
    slice is centered on it (`line - context` .. `line + context`); explicit
    `start`/`end` override. With none of the three, the whole file returns
    (capped at 2000 lines to bound the payload — `truncated` flags the cut).
    """
    git_service = get_git_service(db)

    try:
        content = await git_service.read_file_at_branch(
            branch_name=branch, path=path, actor_agent_id=agent.agent_id
        )
    except _TranslatableError as e:
        raise _translate_error(e) from e

    if content is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File not found at {branch}:{path}",
        )

    all_lines = content.splitlines()
    total = len(all_lines)

    s, e_, truncated = _compute_file_range(
        total=total, line=line, context=context, start=start, end=end
    )

    sliced = all_lines[s - 1 : e_]
    return GitFileContentResponse(
        branch=branch,
        path=path,
        content="\n".join(sliced),
        start_line=s,
        total_lines=total,
        truncated=truncated,
    )


# =============================================================================
# WRITE ENDPOINTS
# =============================================================================


@router.post("/commit", response_model=GitCommitResponse)
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(secret_exfil_validator)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
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
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
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
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
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
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
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
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(prompt_injection_validator)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
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
@guard_deco.rate_limit(requests=10, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
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
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
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
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
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
@guard_deco.rate_limit(requests=10, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
async def rebase_branch(
    data: GitRebaseRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> GitRebaseResponse:
    """Rebase the current branch onto target_branch.

    Role-gated: only CEO and PM roles (cell_pm, main_pm) may rebase branches.
    Developers, QA, documenters, and other roles are rejected with 403.

    If task_id is provided and the caller is not CEO, the task's assigned_to
    is checked: if the task is not assigned to the calling agent, 403 is
    returned (or 404 if the task does not exist).

    On conflict: aborts the rebase and returns conflict=True with the
    list of conflicted files.  On success: returns conflict=False.
    """
    if agent.role not in _REBASE_ALLOWED_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"REBASE_ROLE_RESTRICTED: Role '{agent.role}' is not permitted "
                "to rebase. Only CEO and PM roles (cell_pm, main_pm) may use "
                "this endpoint."
            ),
        )
    # Task ownership check: if a task_id is supplied and the caller is not CEO,
    # ensure the task is assigned to the calling agent.
    if data.task_id is not None and agent.role != AgentRole.CEO:
        task_service = get_task_service(db)
        task = await task_service.get(data.task_id)
        if task is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task not found: {data.task_id}",
            )
        if task.assigned_to != agent.agent_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "REBASE_OWNERSHIP_RESTRICTED: This task is not assigned to "
                    "you. Only the task's assigned agent or CEO may rebase it."
                ),
            )
    project_slug = await _resolve_project_slug(data.project_slug, db)
    git_service = get_git_service(db)

    try:
        workspace = await git_service.get_workspace(project_slug, agent.agent_id)
        conflict, conflicted_files = await git_service.rebase(
            workspace, data.target_branch, project_slug
        )
    except _TranslatableError as e:
        raise _translate_error(e) from e

    return GitRebaseResponse(
        project_slug=project_slug,
        conflict=conflict,
        conflicted_files=conflicted_files,
    )


@router.post("/branches/cleanup", response_model=GitBranchCleanupResponse)
@guard_deco.rate_limit(requests=5, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
async def cleanup_stale_branches(
    data: GitBranchCleanupRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> GitBranchCleanupResponse:
    """Sweep a project's terminal-task branches (PM/CEO only).

    Deletes the remote + local branch of every completed/cancelled task in
    the project (capped per call — see ``GitService.cleanup_stale_branches``),
    skipping the default branch and any environment-ladder rung so a live
    integration/prod branch is never touched. Role-gated identically to
    ``/rebase`` — a history-affecting bulk operation shouldn't be open to
    developers either. Purely a read + external-git-op endpoint: no task rows
    are mutated, so there's nothing for this request to commit.
    """
    if agent.role not in _REBASE_ALLOWED_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"BRANCH_CLEANUP_ROLE_RESTRICTED: Role '{agent.role}' is not "
                "permitted to sweep branches. Only CEO and PM roles (cell_pm, "
                "main_pm) may use this endpoint."
            ),
        )
    project_slug = await _resolve_project_slug(data.project_slug, db)
    git_service = get_git_service(db)

    try:
        (
            remote_deleted,
            local_deleted,
            skipped,
            errors,
            truncated,
            next_cursor,
        ) = await git_service.cleanup_stale_branches(
            project_slug, after_task_id=data.after_cursor
        )
    except _TranslatableError as e:
        raise _translate_error(e) from e

    return GitBranchCleanupResponse(
        project_slug=project_slug,
        remote_deleted=remote_deleted,
        local_deleted=local_deleted,
        skipped=skipped,
        errors=errors,
        truncated=truncated,
        next_cursor=next_cursor,
    )
