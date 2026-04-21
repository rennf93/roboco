"""
Git Service

Handles git operations for agents working on code tasks.
All business logic for git commands, commit templates, PR generation.
"""

import asyncio
import base64
import re
import subprocess
from pathlib import Path
from typing import Any, ClassVar, cast
from uuid import UUID

import httpx
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


# `_get_gh_env` and the gh-CLI code paths were removed in favor of direct
# GitHub REST API calls — no CLI dependency, and the PAT no longer touches
# subprocess argv / environ.

# Expected number of parts in various git outputs
_REV_LIST_PARTS = 2

# GitHub REST API status codes
_GH_UNPROCESSABLE = 422


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
        token: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run a git command in the workspace (non-blocking).

        If `token` is given, injects an Authorization header via `-c
        http.extraheader=...` for this one invocation. The token never
        lands in argv (it goes in a config flag that git consumes and
        then forgets) and never touches `.git/config` on disk. Use this
        for push / fetch / ls-remote — any op that talks to origin.

        After every orchestrator-side git op, hand ownership back to the
        agent user. Git commands here run as root and create root-owned
        files under .git/ (refs, logs/refs, packed-refs, index, objects).
        If we don't re-chown, the agent container (uid 1000) can't append
        to those files on its next commit and fails with
        "unable to append to .git/logs/refs/heads/...".
        """
        from roboco.services.workspace import _ensure_agent_owned

        prefix: list[str] = []
        if token:
            # GitHub's git-over-HTTPS (smart HTTP) authenticates with HTTP
            # Basic, NOT Bearer. Using Bearer here causes git to fall
            # through to the credential prompt and fail with
            # "could not read Username for 'https://github.com'".
            # Bearer is only correct for the REST API (PR create/merge,
            # via httpx) — keep them separate.
            basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
            prefix = [
                "-c",
                f"http.extraheader=Authorization: Basic {basic}",
            ]

        def _run() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["git", *prefix, *args],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=_GIT_TIMEOUT,
                check=check,
            )

        try:
            result = await asyncio.to_thread(_run)
        except subprocess.TimeoutExpired as e:
            raise GitTimeoutError(" ".join(args), _GIT_TIMEOUT) from e
        except subprocess.CalledProcessError as e:
            raise GitCommandError(
                " ".join(args), e.stderr or e.stdout or "Unknown error"
            ) from e

        # Chown after every command — cheap (a stat-check fast-path returns
        # immediately if ownership is already correct, so repeated ops in
        # a single flow don't re-walk the tree unnecessarily).
        await asyncio.to_thread(_ensure_agent_owned, workspace)
        return result

    async def _token_for_project(self, project_slug: str) -> str | None:
        """Decrypted project token for orchestrator-side remote git ops."""
        from roboco.utils.crypto import EncryptionError

        project_service = get_project_service(self.session)
        try:
            return await project_service.get_decrypted_token_by_slug(project_slug)
        except EncryptionError:
            return None

    async def _token_for_workspace(self, workspace: Path) -> str | None:
        """Derive project_slug from workspace path, then load its token.

        Workspace layout is `/data/workspaces/{project}/{team}/{agent}/`, so
        the project slug is the first component after `/data/workspaces/`.
        Returns None if it can't be derived or the project has no token.
        """
        try:
            parts = (
                workspace.resolve().relative_to(Path(settings.workspaces_root)).parts
            )
        except (ValueError, OSError):
            return None
        if not parts:
            return None
        return await self._token_for_project(parts[0])

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

    @staticmethod
    def _classify_porcelain(
        lines: list[str],
    ) -> tuple[list[str], list[str], list[str]]:
        """Split `git status --porcelain` lines into staged/unstaged/untracked."""
        staged: list[str] = []
        unstaged: list[str] = []
        untracked: list[str] = []
        for line in lines:
            if not line:
                continue
            status_code = line[:2]
            file_path = line[3:]
            if status_code[0] in "MADRC":
                staged.append(file_path)
            if status_code[1] in "MADRC":
                unstaged.append(file_path)
            if status_code == "??":
                untracked.append(file_path)
        return staged, unstaged, untracked

    async def _ahead_behind(self, workspace: Path, branch: str) -> tuple[int, int]:
        """Return (ahead, behind) vs origin/<branch>; 0,0 on any error."""
        try:
            rev_cmd = f"{branch}...origin/{branch}"
            rev_result = await self._run_git(
                workspace, ["rev-list", "--left-right", "--count", rev_cmd], check=False
            )
            if rev_result.returncode != 0:
                return 0, 0
            parts = rev_result.stdout.strip().split()
            if len(parts) != _REV_LIST_PARTS:
                return 0, 0
            return int(parts[0]), int(parts[1])
        except GitError:
            return 0, 0

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

        staged_files, unstaged_files, untracked_files = self._classify_porcelain(lines)
        ahead, behind = await self._ahead_behind(workspace, current_branch)
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
        """Get the current branch name.

        `git branch --show-current` returns empty on detached HEAD. Raise
        a clear error in that case — previously we returned the empty
        string, and callers forwarded that to downstream git commands or
        fell back to parsing `git branch` (plain), which produces the
        literal "(HEAD detached at ...)" that then leaked into
        `checkout -b` as a would-be branch name. Fail loud instead.
        """
        result = await self._run_git(workspace, ["branch", "--show-current"])
        branch = result.stdout.strip()
        if not branch:
            raise GitError(
                "Workspace is on a detached HEAD (no current branch). "
                "Checkout an actual branch before running this operation.",
                {"workspace": str(workspace)},
            )
        return branch

    # =========================================================================
    # COMMIT METHODS
    # =========================================================================

    @staticmethod
    def _parse_git_url(url: str) -> tuple[str, str]:
        """Extract (owner, repo) from any accepted GitHub URL form.

        Handles tokened, plain-https, and SSH forms:
            https://x-access-token:TOKEN@github.com/owner/repo.git
            https://github.com/owner/repo.git
            git@github.com:owner/repo.git
        """
        path_match = re.search(
            r"github\.com[:/]+(?P<owner>[^/]+)/(?P<repo>[^/\s]+?)(?:\.git)?$",
            url,
        )
        if not path_match:
            raise GitError(
                "Could not parse GitHub owner/repo from remote URL",
                {"url_host": url.rsplit("@", maxsplit=1)[-1].split("/", maxsplit=1)[0]},
            )
        return path_match.group("owner"), path_match.group("repo")

    def _parse_github_remote(self, workspace: Path) -> tuple[str, str]:
        """Read the origin remote URL from a workspace and parse owner/repo."""
        cfg = workspace / ".git" / "config"
        try:
            text = cfg.read_text()
        except OSError as e:
            raise GitError(
                f"Could not read git config: {e}",
                {"workspace": str(workspace)},
            ) from e

        match = re.search(
            r"^\s*url\s*=\s*(?P<url>\S+)",
            text,
            flags=re.MULTILINE,
        )
        if not match:
            raise GitError(
                "No remote URL in git config",
                {"workspace": str(workspace)},
            )
        return self._parse_git_url(match.group("url"))

    def _get_primary_session_id(self, task: TaskTable | None) -> str | None:
        """Get primary session ID from task's session links.

        Guarded against MissingGreenlet: `task.session_links` is a lazy
        relationship. If it hasn't been eager-loaded, touching it from this
        sync helper inside an async request triggers an async IO call with
        no greenlet context → `MissingGreenlet`, which breaks
        POST /api/v1/git/commit. Inspect loaded-state first and return
        None when not loaded (callers treat None as "no primary session").
        """
        if not task:
            return None

        from sqlalchemy import inspect as sa_inspect

        if "session_links" in sa_inspect(task).unloaded:
            return None

        if not task.session_links:
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

    async def _resolve_base_branch(
        self,
        task_id: UUID,
        parent_branch_override: str | None,
        project_slug: str,
        task_service: Any,
    ) -> str:
        """Work out which branch the new task branch should be cut from.

        Priority: explicit override → parent task's branch → project default
        branch → "main".
        """
        if parent_branch_override:
            return parent_branch_override
        task = await task_service.get(task_id)
        if task and task.parent_task_id:
            parent = await task_service.get(UUID(str(task.parent_task_id)))
            if parent and parent.branch_name:
                return str(parent.branch_name)
        return await self._project_default_branch(project_slug)

    async def _project_default_branch(self, project_slug: str) -> str:
        """Return the project's configured default branch, or 'main'."""
        project_service = get_project_service(self.session)
        project = await project_service.get_by_slug(project_slug)
        return (
            str(project.default_branch)
            if project and project.default_branch
            else "main"
        )

    async def _checkout_base_with_fallback(
        self,
        workspace: Path,
        base_branch: str,
        default_branch: str,
        task_id: UUID,
    ) -> str:
        """Checkout `base_branch`, falling back to default if it's missing.

        Returns the branch actually checked out.
        """
        result = await self._run_git(workspace, ["checkout", base_branch], check=False)
        if result.returncode == 0:
            return base_branch
        # Local branch missing — try a tracking branch from remote
        tracking = await self._run_git(
            workspace,
            ["checkout", "-b", base_branch, f"origin/{base_branch}"],
            check=False,
        )
        if tracking.returncode == 0:
            return base_branch
        # Neither local nor remote has this branch — fall back to default
        self.log.warning(
            "Parent branch unavailable locally and on origin; "
            "falling back to default branch",
            base_branch=base_branch,
            default_branch=default_branch,
            task_id=str(task_id),
        )
        await self._run_git(workspace, ["checkout", default_branch])
        return default_branch

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

        base_branch = await self._resolve_base_branch(
            task_id, request.parent_branch, request.project_slug, task_service
        )
        default_branch = await self._project_default_branch(request.project_slug)

        # Token for any remote-touching git command below (fetch, ls-remote,
        # pull, push). Injected into a single `http.extraheader` config for
        # the subprocess — never stored in .git/config on disk.
        project_token = await self._token_for_project(request.project_slug)

        if base_branch != default_branch:
            # Parent is not the default branch - check it exists on remote.
            # If it doesn't (ancestor task was claimed but never pushed —
            # e.g. PM paused before any commit), we fall back to the default
            # branch below rather than hard-failing. The checkout logic has
            # a matching fallback and logs a warning so this doesn't go
            # unnoticed.
            result = await self._run_git(
                workspace,
                ["ls-remote", "--heads", "origin", base_branch],
                check=False,
                token=project_token,
            )
            if not result.stdout.strip():
                self.log.warning(
                    "Parent branch not on remote; will fall back to default "
                    "branch when creating child branch",
                    base_branch=base_branch,
                    default_branch=default_branch,
                    task_id=str(task_id),
                )
                base_branch = default_branch

        # Fetch to ensure we have the latest refs (critical for parent branches)
        await self._run_git(workspace, ["fetch", "origin"], token=project_token)

        base_branch = await self._checkout_base_with_fallback(
            workspace, base_branch, default_branch, task_id
        )

        await self._run_git(
            workspace, ["pull", "origin", base_branch], token=project_token
        )
        await self._run_git(workspace, ["checkout", "-b", branch_name])
        await self._run_git(
            workspace,
            ["push", "-u", "origin", branch_name],
            token=project_token,
        )
        # Ownership re-chown happens automatically inside `_run_git` now.

        # Store branch name on task
        await task_service.update(task_id, branch_name=branch_name)

        return branch_name, base_branch

    async def checkout(self, workspace: Path, branch: str) -> None:
        """Checkout a branch.

        Fetches from origin first to ensure remote branches are available.
        If the branch doesn't exist locally, creates a tracking branch from remote.
        """
        token = await self._token_for_workspace(workspace)
        # Fetch to ensure we have the latest refs
        await self._run_git(workspace, ["fetch", "origin"], token=token)

        # Try direct checkout first (works if local branch exists)
        result = await self._run_git(workspace, ["checkout", branch], check=False)
        if result.returncode != 0:
            # Branch doesn't exist locally - create tracking branch from remote
            await self._run_git(
                workspace, ["checkout", "-b", branch, f"origin/{branch}"]
            )

    async def push(self, workspace: Path, force: bool = False) -> tuple[str, int]:
        """Push commits to remote.

        Returns: (branch, commits_pushed)
        """
        branch = await self.get_current_branch(workspace)
        token = await self._token_for_workspace(workspace)

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

        await self._run_git(workspace, args, token=token)

        return branch, commits_to_push

    # =========================================================================
    # PR METHODS
    # =========================================================================

    @staticmethod
    def _collect_root_commits(
        root: TaskTable, descendants: list[TaskTable]
    ) -> list[PRCommitInfo]:
        """Flatten commits across root + every descendant."""
        out: list[PRCommitInfo] = []
        for d in [root, *descendants]:
            for c in d.commits or []:
                out.append(
                    PRCommitInfo(
                        hash=str(c.get("hash", "")),
                        message=str(c.get("message", "")),
                        agent_slug=str(c.get("agent_id", "unknown")),
                    )
                )
        return out

    @staticmethod
    def _collect_agent_slugs(
        root: TaskTable, descendants: list[TaskTable]
    ) -> list[str]:
        """Unique agent slugs involved in root + descendants."""
        slugs = [str(d.assigned_to) for d in descendants if d.assigned_to]
        if root.assigned_to:
            slugs.append(str(root.assigned_to))
        return list(set(slugs))

    @staticmethod
    def _primary_session_id(task: TaskTable) -> str | None:
        """Session flagged is_primary on the root task's session_links."""
        for link in task.session_links or []:
            if link.is_primary:
                return str(link.session_id)
        return None

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

        task_type = (
            source_branch.split("/", maxsplit=1)[0]
            if "/" in source_branch
            else "feature"
        )

        return RootPRContext(
            root_task_id=str(task.id),
            root_task_title=str(task.title),
            root_task_description=str(task.description) if task.description else "",
            root_task_assigned_to=str(task.assigned_to) if task.assigned_to else None,
            root_task_type=task_type,
            subtasks=subtask_infos,
            commits=self._collect_root_commits(task, descendants),
            primary_session_id=self._primary_session_id(task),
            agent_slugs=self._collect_agent_slugs(task, descendants),
            acceptance_criteria=list(task.acceptance_criteria)
            if task.acceptance_criteria
            else [],
        )

    @staticmethod
    def _str_or(value: Any, default: str = "") -> str:
        """Helper: str(value) when truthy, else default."""
        return str(value) if value else default

    @staticmethod
    def _str_or_none(value: Any) -> str | None:
        """Helper: str(value) when truthy, else None."""
        return str(value) if value else None

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

        status_value = self._str_or(
            task.status.value if task.status else None, "unknown"
        )
        qa_passed = status_value in ("awaiting_documentation", "awaiting_pm_review")

        return InternalPRContext(
            task_id=str(task.id),
            task_title=str(task.title),
            task_description=self._str_or(task.description),
            task_status=status_value,
            task_assigned_to=self._str_or_none(task.assigned_to),
            parent_task_id=self._str_or_none(parent_task.id if parent_task else None),
            parent_task_title=self._str_or_none(
                parent_task.title if parent_task else None
            ),
            source_branch=source_branch,
            target_branch=target_branch,
            commits=task_commits,
            session_id=None,
            qa_notes=self._str_or_none(task.qa_notes),
            qa_passed=qa_passed,
        )

    async def _get_project_token_or_raise(self, project_slug: str) -> str:
        """Fetch + decrypt the project's GitHub PAT, raising GitError on problem."""
        from roboco.utils.crypto import EncryptionError

        project_service = get_project_service(self.session)
        try:
            git_token = await project_service.get_decrypted_token_by_slug(project_slug)
        except EncryptionError as e:
            raise GitError(
                f"Failed to decrypt git token for project '{project_slug}'. "
                "The encryption key may have been rotated; re-set the project token."
            ) from e
        if not git_token:
            raise GitError(
                f"Project '{project_slug}' has no git token configured. "
                "Configure a GitHub PAT in the project settings to create PRs."
            )
        return git_token

    async def _resolve_pr_target_branch(
        self, request: GitCreatePRRequest, task: Any, default_branch: str
    ) -> str:
        """Pick the PR target branch: parent's branch (non-root) or default."""
        if request.is_root_pr:
            return default_branch
        if task.parent_task_id:
            task_service = get_task_service(self.session)
            parent = await task_service.get(UUID(str(task.parent_task_id)))
            branch = parent.branch_name if parent else None
            return str(branch) if branch else default_branch
        return default_branch

    async def _generate_pr_title_body(
        self,
        request: GitCreatePRRequest,
        task: Any,
        source_branch: str,
        target_branch: str,
        task_id: UUID,
    ) -> tuple[str | None, str | None]:
        """Auto-generate title/body from templates when either is missing."""
        pr_title = request.title
        pr_body = request.body
        if pr_title and pr_body:
            return pr_title, pr_body
        task_service = get_task_service(self.session)
        api_base = settings.internal_api_url
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
        return pr_title, pr_body

    async def _find_existing_pr(
        self,
        owner: str,
        repo: str,
        source_branch: str,
        target_branch: str,
        git_token: str,
    ) -> dict[str, Any] | None:
        """Return the first open PR for head→base, or None."""
        async with httpx.AsyncClient(timeout=_GIT_TIMEOUT) as client:
            existing = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/pulls",
                headers={
                    "Authorization": f"Bearer {git_token}",
                    "Accept": "application/vnd.github+json",
                },
                params={
                    "head": f"{owner}:{source_branch}",
                    "base": target_branch,
                    "state": "open",
                },
            )
        if existing.is_success and existing.json():
            return cast("dict[str, Any]", existing.json()[0])
        return None

    async def _post_pr(
        self,
        owner: str,
        repo: str,
        git_token: str,
        payload: dict[str, Any],
    ) -> httpx.Response:
        """POST the PR payload to GitHub; translate HTTP errors to GitError."""
        try:
            async with httpx.AsyncClient(timeout=_GIT_TIMEOUT) as client:
                return await client.post(
                    f"https://api.github.com/repos/{owner}/{repo}/pulls",
                    headers={
                        "Authorization": f"Bearer {git_token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                    json=payload,
                )
        except httpx.HTTPError as e:
            raise GitError(
                f"GitHub API error while creating PR: {e}",
                {"owner": owner, "repo": repo, "head": payload.get("head")},
            ) from e

    async def create_pull_request(
        self, workspace: Path, request: GitCreatePRRequest
    ) -> tuple[int, str, str, str, str]:
        """Create a pull request via the GitHub REST API.

        Returns: (pr_number, pr_url, title, source_branch, target_branch)
        """
        task_id = UUID(request.task_id)
        task_service = get_task_service(self.session)
        task = await task_service.get(task_id)
        if not task:
            raise NotFoundError("Task", str(task_id))

        source_branch = await self.get_current_branch(workspace)
        default_branch = await self._project_default_branch(request.project_slug)
        git_token = await self._get_project_token_or_raise(request.project_slug)

        target_branch = await self._resolve_pr_target_branch(
            request, task, default_branch
        )
        pr_title, pr_body = await self._generate_pr_title_body(
            request, task, source_branch, target_branch, task_id
        )

        owner, repo = self._parse_github_remote(workspace)
        resp = await self._post_pr(
            owner,
            repo,
            git_token,
            {
                "title": pr_title or "",
                "body": pr_body or "",
                "head": source_branch,
                "base": target_branch,
            },
        )

        # Idempotency: PR already exists for this head→base.
        if resp.status_code == _GH_UNPROCESSABLE and "already exists" in resp.text:
            found = await self._find_existing_pr(
                owner, repo, source_branch, target_branch, git_token
            )
            if found:
                return (
                    int(found["number"]),
                    found["html_url"],
                    found.get("title", pr_title or ""),
                    source_branch,
                    target_branch,
                )

        if not resp.is_success:
            raise GitError(
                f"GitHub API refused PR creation ({resp.status_code}): "
                f"{resp.text[:200]}",
                {"owner": owner, "repo": repo, "head": source_branch},
            )

        pr_data = resp.json()
        return (
            int(pr_data["number"]),
            str(pr_data["html_url"]),
            pr_title or "",
            source_branch,
            target_branch,
        )

    async def _call_merge_api(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        git_token: str,
        merge_method: str,
    ) -> httpx.Response:
        """PUT the merge request to GitHub; HTTP errors → GitError."""
        try:
            async with httpx.AsyncClient(timeout=_GIT_TIMEOUT) as client:
                return await client.put(
                    f"https://api.github.com/repos/{owner}/{repo}/pulls/"
                    f"{pr_number}/merge",
                    headers={
                        "Authorization": f"Bearer {git_token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                    json={"merge_method": merge_method},
                )
        except httpx.HTTPError as e:
            raise GitError(
                f"GitHub API error while merging PR #{pr_number}: {e}",
                {"owner": owner, "repo": repo, "pr": pr_number},
            ) from e

    async def _sync_target_branch(
        self, workspace: Path, target_branch: str, git_token: str
    ) -> str:
        """Checkout + pull the target branch, return the tip commit hash."""
        await self._run_git(workspace, ["checkout", target_branch])
        await self._run_git(workspace, ["pull"], token=git_token)
        log_result = await self._run_git(workspace, ["log", "-1", "--format=%H"])
        return log_result.stdout.strip()

    async def _delete_remote_branch_best_effort(
        self, owner: str, repo: str, branch: str, git_token: str
    ) -> None:
        """Best-effort: delete a remote branch by name.

        Silently swallows errors — cleanup is not critical. Skips
        branches that look like project defaults (main / master /
        develop) as a last-chance safety net against bad input.
        """
        if branch in ("main", "master", "develop", ""):
            return
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.delete(
                    f"https://api.github.com/repos/{owner}/{repo}/git/refs/heads/{branch}",
                    headers={
                        "Authorization": f"Bearer {git_token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
        except httpx.HTTPError:
            return

    async def _delete_pr_branch_best_effort(
        self, owner: str, repo: str, pr_number: int, git_token: str
    ) -> None:
        """Best-effort: delete the PR's source branch on the remote after merge.

        Silently swallows errors — branch cleanup is not critical.
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                pr_resp = await client.get(
                    f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}",
                    headers={
                        "Authorization": f"Bearer {git_token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
                if not pr_resp.is_success:
                    return
                branch = (pr_resp.json().get("head") or {}).get("ref")
                if not branch:
                    return
            await self._delete_remote_branch_best_effort(owner, repo, branch, git_token)
        except httpx.HTTPError:
            return

    async def delete_task_branch(self, project_slug: str, branch_name: str) -> None:
        """Delete a remote task branch after cancel/discard. Best-effort.

        Called by `TaskService` on cancellation so abandoned task
        branches don't accumulate on the remote.
        """
        git_token = await self._token_for_project(project_slug)
        if not git_token:
            return
        # Resolve remote from any workspace — branch deletion only needs
        # the owner/repo, not a checkout. Use a service-root probe path
        # if no agent workspace is available.
        try:
            project_service = get_project_service(self.session)
            project = await project_service.get_by_slug(project_slug)
            if not project or not project.git_url:
                return
            owner, repo = self._parse_git_url(project.git_url)
        except Exception:
            return
        await self._delete_remote_branch_best_effort(
            owner, repo, branch_name, git_token
        )

    async def merge_pull_request(
        self, workspace: Path, pr_number: int, merge_method: str, project_slug: str
    ) -> tuple[str, str]:
        """Merge a PR via the GitHub REST API.

        Returns: (target_branch, merge_commit)
        """
        git_token = await self._get_project_token_or_raise(project_slug)
        owner, repo = self._parse_github_remote(workspace)
        if merge_method not in {"merge", "squash", "rebase"}:
            merge_method = "squash"

        resp = await self._call_merge_api(
            owner, repo, pr_number, git_token, merge_method
        )
        if not resp.is_success:
            raise GitError(
                f"GitHub API refused PR merge ({resp.status_code}): {resp.text[:200]}",
                {"owner": owner, "repo": repo, "pr": pr_number},
            )

        await self._delete_pr_branch_best_effort(owner, repo, pr_number, git_token)

        target_branch = await self._project_default_branch(project_slug)
        merge_commit = await self._sync_target_branch(
            workspace, target_branch, git_token
        )
        return target_branch, merge_commit


def get_git_service(session: AsyncSession) -> GitService:
    """Factory function to get git service."""
    return GitService(session)
