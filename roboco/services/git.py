"""
Git Service

Handles git operations for agents working on code tasks.
All business logic for git commands, commit templates, PR generation.
"""

import asyncio
import os
import subprocess
from pathlib import Path
from typing import ClassVar
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from roboco.api.schemas.git import (
    GitCommitRequest,
    GitCreateBranchRequest,
    GitCreatePRRequest,
)
from roboco.config import settings
from roboco.db.tables import TaskTable
from roboco.exceptions import GitCommandError, GitError, GitTimeoutError
from roboco.services.base import (
    BaseService,
    NotFoundError,
    ValidationError,
)
from roboco.services.project import get_project_service
from roboco.services.task import TaskService, get_task_service
from roboco.services.workspace import WorkspaceError, get_workspace_service
from roboco.templates.git import (
    BranchNameError,
    CommitContext,
    InternalPRContext,
    RootPRContext,
    SubtaskInfo,
    build_branch_name,
    build_commit_message,
    build_pr_body_internal,
    build_pr_body_root,
    build_pr_title_internal,
    build_pr_title_root,
    get_root_task_id,
)
from roboco.templates.git.pr_internal import InternalCommitInfo
from roboco.templates.git.pr_root import CommitInfo as PRCommitInfo

# Git command timeout in seconds
_GIT_TIMEOUT = 30


def _get_gh_env(token: str | None = None) -> dict[str, str]:
    """
    Get environment variables for gh CLI commands.

    Args:
        token: Project-specific GitHub PAT (required for gh operations)

    Returns:
        Environment dict with GITHUB_TOKEN set
    """
    env = os.environ.copy()
    if token:
        env["GITHUB_TOKEN"] = token
        env["GH_TOKEN"] = token
    return env


# Expected number of parts in various git outputs
_REV_LIST_PARTS = 2


class GitService(BaseService):
    """
    Service for git operations on agent workspaces.

    Handles:
    - Git command execution
    - Commit creation with templates
    - PR creation with templates
    - Branch management
    """

    service_name: ClassVar[str] = "git"

    async def _run_git(
        self,
        workspace: Path,
        args: list[str],
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Run a git command in the workspace (non-blocking)."""

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
            raise GitTimeoutError(" ".join(args), _GIT_TIMEOUT) from e
        except subprocess.CalledProcessError as e:
            raise GitCommandError(
                " ".join(args), e.stderr or e.stdout or "Unknown error"
            ) from e

    async def get_workspace(
        self, project_slug: str, agent_id: UUID | None = None
    ) -> Path:
        """Get the workspace path for an agent on a project."""
        project_service = get_project_service(self.session)
        project = await project_service.get_by_slug(project_slug)
        if not project:
            raise NotFoundError("Project", project_slug)

        if agent_id is None:
            if not project.workspace_path:
                raise ValidationError(
                    f"Project '{project_slug}' has no workspace configured "
                    "and no agent_id provided for dynamic workspace resolution"
                )
            workspace = Path(project.workspace_path)
            if not workspace.exists():
                raise ValidationError(f"Workspace path does not exist: {workspace}")
            return workspace

        workspace_service = get_workspace_service(self.session)

        try:
            if settings.workspace_auto_clone:
                workspace = await workspace_service.ensure_workspace(
                    project_slug=project_slug,
                    agent_id=agent_id,
                    git_url=project.git_url,
                    default_branch=project.default_branch or "main",
                )
            else:
                workspace = await workspace_service.resolve_workspace(
                    project_slug=project_slug,
                    agent_id=agent_id,
                )
                if not workspace.exists():
                    raise ValidationError(
                        f"Workspace does not exist: {workspace}. "
                        "Clone the repository first or enable auto_clone."
                    )
        except WorkspaceError as e:
            raise ValidationError(str(e)) from e

        return workspace

    # =========================================================================
    # STATUS / INFO METHODS
    # =========================================================================

    async def get_status(
        self, workspace: Path
    ) -> tuple[str, bool, list[str], list[str], list[str], int, int]:
        """Get git status for a workspace.

        Returns tuple of:
            (current_branch, has_changes, staged, unstaged, untracked, ahead, behind)
        """
        branch_result = await self._run_git(workspace, ["branch", "--show-current"])
        current_branch = branch_result.stdout.strip()

        status_result = await self._run_git(workspace, ["status", "--porcelain"])
        lines = status_result.stdout.strip().split("\n") if status_result.stdout else []

        staged_files: list[str] = []
        unstaged_files: list[str] = []
        untracked_files: list[str] = []

        for line in lines:
            if not line:
                continue
            status_code = line[:2]
            file_path = line[3:]

            if status_code[0] in "MADRC":
                staged_files.append(file_path)
            if status_code[1] in "MADRC":
                unstaged_files.append(file_path)
            if status_code == "??":
                untracked_files.append(file_path)

        ahead, behind = 0, 0
        try:
            rev_cmd = f"{current_branch}...origin/{current_branch}"
            rev_result = await self._run_git(
                workspace, ["rev-list", "--left-right", "--count", rev_cmd], check=False
            )
            if rev_result.returncode == 0:
                parts = rev_result.stdout.strip().split()
                if len(parts) == _REV_LIST_PARTS:
                    ahead, behind = int(parts[0]), int(parts[1])
        except GitError:
            pass

        has_changes = bool(staged_files or unstaged_files or untracked_files)
        return (
            current_branch,
            has_changes,
            staged_files,
            unstaged_files,
            untracked_files,
            ahead,
            behind,
        )

    async def get_current_branch(self, workspace: Path) -> str:
        """Get the current branch name."""
        result = await self._run_git(workspace, ["branch", "--show-current"])
        return result.stdout.strip()

    # =========================================================================
    # COMMIT METHODS
    # =========================================================================

    def _get_primary_session_id(self, task: TaskTable | None) -> str | None:
        """Get primary session ID from task's session links."""
        if not task or not task.session_links:
            return None
        for link in task.session_links:
            if link.is_primary:
                return str(link.session_id)
        return None

    def _parse_commit_stats(self, stat_output: str) -> tuple[int, int, int]:
        """Parse git diff --stat output for insertions, deletions, files_changed."""
        insertions, deletions, files_changed = 0, 0, 0
        for line in stat_output.split("\n"):
            if "insertion" not in line and "deletion" not in line:
                continue
            parts = line.split(",")
            for part in parts:
                if "insertion" in part:
                    insertions = int(part.strip().split()[0])
                elif "deletion" in part:
                    deletions = int(part.strip().split()[0])
                elif "file" in part:
                    files_changed = int(part.strip().split()[0])
        return insertions, deletions, files_changed

    async def create_commit(
        self,
        workspace: Path,
        agent_id: UUID,
        request: GitCommitRequest,
    ) -> tuple[str, str, int, int, int]:
        """Create a git commit with template-based message.

        Returns: (commit_hash, full_message, files_changed, insertions, deletions)
        """
        task_id = UUID(request.task_id)

        # Stage files
        if request.files:
            for file in request.files:
                await self._run_git(workspace, ["add", file])
        else:
            await self._run_git(workspace, ["add", "-A"])

        # Get task info for commit template
        task_service = get_task_service(self.session)
        task = await task_service.get(task_id)

        # Get root task ID (walk up hierarchy)
        root_task_id = await get_root_task_id(task_id, task_service)

        # Get session ID
        session_id = self._get_primary_session_id(task)

        # Build commit message using template
        commit_ctx = CommitContext(
            task_id=str(task_id),
            root_task_id=str(root_task_id),
            agent_slug=str(agent_id),
            session_id=session_id,
            commit_type=request.commit_type,
            scope=request.scope,
            description=request.message,
            body=request.body,
        )
        full_message = build_commit_message(commit_ctx, settings.internal_api_url)

        # Create commit with agent attribution
        author = f"{agent_id} <{agent_id}@roboco.ai>"
        await self._run_git(
            workspace, ["commit", "-m", full_message, "--author", author]
        )

        # Get commit info
        log_result = await self._run_git(workspace, ["log", "-1", "--format=%H|%s"])
        parts = log_result.stdout.strip().split("|")
        commit_hash = parts[0] if parts else "unknown"

        # Get stats
        stat_result = await self._run_git(workspace, ["diff", "--stat", "HEAD~1..HEAD"])
        insertions, deletions, files_changed = self._parse_commit_stats(
            stat_result.stdout
        )

        return commit_hash, full_message, files_changed, insertions, deletions

    # =========================================================================
    # BRANCH METHODS
    # =========================================================================

    async def create_branch(
        self,
        workspace: Path,
        team: str,
        request: GitCreateBranchRequest,
    ) -> tuple[str, str]:
        """Create a task branch with hierarchical naming.

        Returns: (branch_name, created_from)
        """
        task_id = UUID(request.task_id)
        task_service = get_task_service(self.session)

        try:
            branch_name = await build_branch_name(
                task_id=task_id,
                branch_type=request.branch_type,
                team=team,
                task_service=task_service,
            )
        except BranchNameError as e:
            raise ValidationError(str(e)) from e

        # Get base branch
        base_branch = request.parent_branch
        if not base_branch:
            task = await task_service.get(task_id)
            if task and task.parent_task_id:
                parent = await task_service.get(UUID(str(task.parent_task_id)))
                if parent and parent.branch_name:
                    base_branch = str(parent.branch_name)

            if not base_branch:
                project_service = get_project_service(self.session)
                project = await project_service.get_by_slug(request.project_slug)
                base_branch = str(project.default_branch) if project else "main"

        # Validate parent branch exists on remote (unless it's the default branch)
        project_service = get_project_service(self.session)
        project = await project_service.get_by_slug(request.project_slug)
        default_branch = str(project.default_branch) if project else "main"

        if base_branch != default_branch:
            # Parent is not the default branch - verify it exists on remote
            result = await self._run_git(
                workspace,
                ["ls-remote", "--heads", "origin", base_branch],
                check=False,
            )
            if not result.stdout.strip():
                raise ValidationError(
                    f"Parent branch '{base_branch}' does not exist on remote. "
                    f"The parent task must be claimed first (claim creates branch)."
                )

        # Create and push branch
        await self._run_git(workspace, ["checkout", base_branch])
        await self._run_git(workspace, ["pull", "origin", base_branch])
        await self._run_git(workspace, ["checkout", "-b", branch_name])
        await self._run_git(workspace, ["push", "-u", "origin", branch_name])

        # Store branch name on task
        await task_service.update(task_id, branch_name=branch_name)

        return branch_name, base_branch

    async def checkout(self, workspace: Path, branch: str) -> None:
        """Checkout a branch."""
        await self._run_git(workspace, ["checkout", branch])

    async def push(self, workspace: Path, force: bool = False) -> tuple[str, int]:
        """Push commits to remote.

        Returns: (branch, commits_pushed)
        """
        branch = await self.get_current_branch(workspace)

        count_result = await self._run_git(
            workspace,
            ["rev-list", "--count", f"origin/{branch}..{branch}"],
            check=False,
        )
        commits_to_push = (
            int(count_result.stdout.strip()) if count_result.returncode == 0 else 0
        )

        args = ["push", "-u", "origin", branch]
        if force:
            args.insert(1, "--force")

        await self._run_git(workspace, args)

        return branch, commits_to_push

    # =========================================================================
    # PR METHODS
    # =========================================================================

    async def _build_root_pr_context(
        self,
        task: TaskTable,
        task_service: TaskService,
        task_uuid: UUID,
        source_branch: str,
    ) -> RootPRContext:
        """Build context for root task PR template."""
        descendants = await task_service.get_all_descendants(task_uuid)

        subtask_infos = [
            SubtaskInfo(
                id=str(d.id),
                title=str(d.title),
                status=str(d.status.value) if d.status else "unknown",
                assigned_to=str(d.assigned_to) if d.assigned_to else None,
                branch_name=str(d.branch_name) if d.branch_name else None,
                commit_count=len(d.commits) if d.commits else 0,
            )
            for d in descendants
        ]

        all_commits: list[PRCommitInfo] = []
        for d in [task, *descendants]:
            if d.commits:
                for c in d.commits:
                    all_commits.append(
                        PRCommitInfo(
                            hash=str(c.get("hash", "")),
                            message=str(c.get("message", "")),
                            agent_slug=str(c.get("agent_id", "unknown")),
                        )
                    )

        agent_slugs = [str(d.assigned_to) for d in descendants if d.assigned_to]
        if task.assigned_to:
            agent_slugs.append(str(task.assigned_to))
        agent_slugs = list(set(agent_slugs))

        primary_session_id = None
        if task.session_links:
            for link in task.session_links:
                if link.is_primary:
                    primary_session_id = str(link.session_id)
                    break

        task_type = "feature"
        if "/" in source_branch:
            task_type = source_branch.split("/")[0]

        criteria = list(task.acceptance_criteria) if task.acceptance_criteria else []

        return RootPRContext(
            root_task_id=str(task.id),
            root_task_title=str(task.title),
            root_task_description=str(task.description) if task.description else "",
            root_task_assigned_to=str(task.assigned_to) if task.assigned_to else None,
            root_task_type=task_type,
            subtasks=subtask_infos,
            commits=all_commits,
            primary_session_id=primary_session_id,
            agent_slugs=agent_slugs,
            acceptance_criteria=criteria,
        )

    async def _build_internal_pr_context(
        self,
        task: TaskTable,
        task_service: TaskService,
        source_branch: str,
        target_branch: str,
    ) -> InternalPRContext:
        """Build context for internal PR template."""
        parent_task = None
        if task.parent_task_id:
            parent_task = await task_service.get(UUID(str(task.parent_task_id)))

        task_commits = [
            InternalCommitInfo(
                hash=str(c.get("hash", "")),
                message=str(c.get("message", "")),
            )
            for c in (task.commits or [])
        ]

        qa_statuses = ("awaiting_documentation", "awaiting_pm_review")
        qa_passed = bool(task.status and str(task.status.value) in qa_statuses)

        return InternalPRContext(
            task_id=str(task.id),
            task_title=str(task.title),
            task_description=str(task.description) if task.description else "",
            task_status=str(task.status.value) if task.status else "unknown",
            task_assigned_to=str(task.assigned_to) if task.assigned_to else None,
            parent_task_id=str(parent_task.id) if parent_task else None,
            parent_task_title=str(parent_task.title) if parent_task else None,
            source_branch=source_branch,
            target_branch=target_branch,
            commits=task_commits,
            session_id=None,
            qa_notes=str(task.qa_notes) if task.qa_notes else None,
            qa_passed=qa_passed,
        )

    async def create_pull_request(
        self, workspace: Path, request: GitCreatePRRequest
    ) -> tuple[int, str, str, str, str]:
        """Create a pull request using GitHub CLI.

        Returns: (pr_number, pr_url, title, source_branch, target_branch)
        """
        task_id = UUID(request.task_id)
        task_service = get_task_service(self.session)
        task = await task_service.get(task_id)

        if not task:
            raise NotFoundError("Task", str(task_id))

        source_branch = await self.get_current_branch(workspace)

        # Determine target branch and get project token
        project_service = get_project_service(self.session)
        project = await project_service.get_by_slug(request.project_slug)
        default_branch = str(project.default_branch) if project else "main"

        # Get decrypted token from project (required for PR creation)
        git_token = await project_service.get_decrypted_token_by_slug(
            request.project_slug
        )
        if not git_token:
            raise GitError(
                f"Project '{request.project_slug}' has no git token configured. "
                "Configure a GitHub PAT in the project settings to create PRs."
            )

        if request.is_root_pr:
            target_branch = default_branch
        elif task.parent_task_id:
            parent = await task_service.get(UUID(str(task.parent_task_id)))
            branch = parent.branch_name if parent else None
            target_branch = str(branch) if branch else default_branch
        else:
            target_branch = default_branch

        # Auto-generate title/body using templates if not provided
        pr_title = request.title
        pr_body = request.body
        api_base = settings.internal_api_url

        if not pr_title or not pr_body:
            if request.is_root_pr:
                root_ctx = await self._build_root_pr_context(
                    task, task_service, task_id, source_branch
                )
                pr_title = pr_title or build_pr_title_root(root_ctx)
                pr_body = pr_body or build_pr_body_root(root_ctx, api_base)
            else:
                internal_ctx = await self._build_internal_pr_context(
                    task, task_service, source_branch, target_branch
                )
                pr_title = pr_title or build_pr_title_internal(internal_ctx)
                pr_body = pr_body or build_pr_body_internal(internal_ctx, api_base)

        # Create PR using gh CLI with project token
        def _create_pr() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [
                    "gh",
                    "pr",
                    "create",
                    "--title",
                    pr_title,
                    "--body",
                    pr_body,
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
                env=_get_gh_env(git_token),
            )

        try:
            result = await asyncio.to_thread(_create_pr)
            pr_url = result.stdout.strip()
            pr_number = int(pr_url.split("/")[-1]) if pr_url else 0
        except subprocess.CalledProcessError as e:
            raise GitCommandError("gh pr create", e.stderr or e.stdout or "") from e
        except FileNotFoundError as e:
            raise GitError(
                "GitHub CLI (gh) not found. Please install it.",
                {"command": "gh pr create"},
            ) from e

        return pr_number, pr_url, pr_title or "", source_branch, target_branch

    async def merge_pull_request(
        self, workspace: Path, pr_number: int, merge_method: str, project_slug: str
    ) -> tuple[str, str]:
        """Merge a PR using GitHub CLI.

        Returns: (target_branch, merge_commit)
        """
        # Get project token for gh CLI
        project_service = get_project_service(self.session)
        git_token = await project_service.get_decrypted_token_by_slug(project_slug)
        if not git_token:
            raise GitError(
                f"Project '{project_slug}' has no git token configured. "
                "Configure a GitHub PAT in the project settings to merge PRs."
            )

        def _merge_pr() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [
                    "gh",
                    "pr",
                    "merge",
                    str(pr_number),
                    f"--{merge_method}",
                    "--delete-branch",
                ],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=_GIT_TIMEOUT,
                check=True,
                env=_get_gh_env(git_token),
            )

        try:
            await asyncio.to_thread(_merge_pr)
        except subprocess.CalledProcessError as e:
            raise GitCommandError("gh pr merge", e.stderr or e.stdout or "") from e

        # Get target branch
        project = await project_service.get_by_slug(project_slug)
        target_branch = str(project.default_branch) if project else "main"

        # Get merge commit
        await self._run_git(workspace, ["checkout", target_branch])
        await self._run_git(workspace, ["pull"])
        log_result = await self._run_git(workspace, ["log", "-1", "--format=%H"])
        merge_commit = log_result.stdout.strip()

        return target_branch, merge_commit


def get_git_service(session: AsyncSession) -> GitService:
    """Factory function to get git service."""
    return GitService(session)
