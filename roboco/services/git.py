"""
Git Service

Handles git operations for agents working on code tasks.
All business logic for git commands, commit templates, PR generation.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, cast
from uuid import UUID

import httpx

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    # `api.schemas.git` would trigger `api/__init__.py` (which historically
    # loaded the FastAPI app + every route module). The package init no
    # longer re-exports `app`, but keeping these imports type-only respects
    # the layer rule: services don't depend on the api layer at runtime.
    # Routes already pass the Pydantic models, so the methods are duck-typed
    # at call time.
    from roboco.api.schemas.git import (
        GitCheckoutRequest,
        GitCommitRequest,
        GitCreateBranchRequest,
        GitCreatePRRequest,
        GitMergePRRequest,
    )
    from roboco.db.tables import TaskTable
from roboco.config import settings
from roboco.exceptions import (
    GitCommandError,
    GitError,
    GitTimeoutError,
    MergeConflictError,
)
from roboco.models.base import AgentRole, TaskStatus
from roboco.services.base import (
    BaseService,
    NotFoundError,
    ServiceError,
    UnauthorizedError,
    ValidationError,
)
from roboco.services.gateway.quality_gate import GateResult, run_quality_commands
from roboco.services.project import get_project_service
from roboco.services.task import TaskService, get_task_service
from roboco.services.work_session import get_work_session_service
from roboco.services.workspace import (
    WorkspaceError,
    WorkspaceService,
    get_workspace_service,
)
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
from roboco.utils.converters import require_uuid


# Git command timeout in seconds. Sourced from settings so operators can
# raise it without a code change; `_run_git` accepts a per-call override for
# the rare long-running op (staging/committing a large changeset). Read via a
# helper rather than at import time so a test-time settings patch is honored.
def _default_git_timeout() -> int:
    return settings.git_command_timeout_seconds


def _commit_git_timeout() -> int:
    return settings.git_commit_timeout_seconds


def _network_git_timeout() -> int:
    """Budget for git ops that talk to origin (fetch / pull / push).

    A push/fetch on a large private monorepo from a self-hosted runner can
    take far longer than the sub-second local-op default — short-budgeting it
    is what made open_pr time out before the branch ever reached the remote.
    """
    return settings.git_network_timeout_seconds


# Dedicated thread pool for git subprocesses + the post-op ownership repair.
# These run in threads (subprocess + an os.walk chown); under concurrent agent
# load they must not queue behind — or starve — the event loop's default
# executor that every other to_thread call shares. A bounded dedicated pool
# isolates git work and caps host parallelism so a burst of commits/pushes
# can't thrash the box. Never shut down: lives for the process.
_GIT_EXECUTOR = ThreadPoolExecutor(
    max_workers=int(os.environ.get("ROBOCO_GIT_EXECUTOR_WORKERS", "16")),
    thread_name_prefix="git",
)

# A git subprocess or ownership repair slower than this is logged so a run can
# pinpoint where time goes (e.g. a push to origin). Below it, the fast path
# stays quiet. Set above the normal cost of a push/fetch to a private monorepo
# from a self-hosted runner (~1-2s) so routine ops don't spam "slow git op";
# only a genuinely slow op (>5s) is worth a warning.
_SLOW_GIT_OP_MS = 5000.0


# `_get_gh_env` and the gh-CLI code paths were removed in favor of direct
# GitHub REST API calls — no CLI dependency, and the PAT no longer touches
# subprocess argv / environ.

# Expected number of parts in various git outputs
_REV_LIST_PARTS = 2

# GitHub REST API status codes
_GH_UNPROCESSABLE = 422
# 404 means the PR (or repo) does not exist; surfaced as a typed GitError
# by `update_pr_for_task` so the gateway can convert it into a specific
# invalid_state envelope rather than the generic refusal message.
_HTTP_NOT_FOUND = 404
# 409 means the PR can't be merged in its current state — typically because
# a concurrent sibling-subtask merge updated the target branch and our local
# refs are stale. `pr_merge` re-syncs and retries exactly once on this code.
_HTTP_CONFLICT = 409

# --- Self-heal CI signal -------------------------------------------------
# Pull a WINDOW of recent completed runs (not just the single newest) so the
# conclusion can be resolved against the branch's current HEAD rather than
# whichever run finished most recently — otherwise a green run on an older
# commit, or (on the unscoped all-workflows endpoint) an unrelated green
# workflow, masks the HEAD commit's failing run and the signal flickers.
_CI_RUN_WINDOW = 20
# Transient GitHub failures (network, 429, 5xx) are retried within the cycle so
# a single blip does not silently skip a whole self-heal pass.
_CI_FETCH_ATTEMPTS = 3
_CI_FETCH_BACKOFF_SECONDS = 0.5
_CI_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _select_ci_head_run(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick the run reflecting the branch's current-HEAD CI conclusion.

    GitHub returns completed runs newest-created first, so ``runs[0]`` belongs to
    the newest commit. Among all returned runs sharing that ``head_sha`` we take
    the highest ``run_attempt`` so a green re-run supersedes the original
    failure. This stops a stale green run on an older commit (or an unrelated
    workflow on the all-workflows endpoint) from masking the HEAD failure.
    """
    head_sha = runs[0].get("head_sha")
    same_head = [r for r in runs if r.get("head_sha") == head_sha] or [runs[0]]
    return max(same_head, key=lambda r: int(r.get("run_attempt") or 0))


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
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run a git command in the workspace (non-blocking).

        If `token` is given, injects an Authorization header via `-c
        http.extraheader=...` for this one invocation. The token never
        lands in argv (it goes in a config flag that git consumes and
        then forgets) and never touches `.git/config` on disk. Use this
        for push / fetch / ls-remote — any op that talks to origin.

        `timeout` overrides the default per-command budget
        (``settings.git_command_timeout_seconds``). Staging and committing
        a large changeset (e.g. the Next.js panel) can exceed the short
        default while git hashes every object and we re-chown the tree, so
        the commit choreography passes the longer
        ``settings.git_commit_timeout_seconds``.

        After every orchestrator-side git op, hand ownership back to the
        agent user. Git commands here run as root and create root-owned
        files under .git/ (refs, logs/refs, packed-refs, index, objects).
        If we don't re-chown, the agent container (uid 1000) can't append
        to those files on its next commit and fails with
        "unable to append to .git/logs/refs/heads/...".
        """
        from roboco.services.workspace import _ensure_agent_owned

        effective_timeout = timeout if timeout is not None else _default_git_timeout()

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
                timeout=effective_timeout,
                check=check,
            )

        loop = asyncio.get_running_loop()
        op = " ".join(args[:2])
        t0 = time.monotonic()
        try:
            result = await loop.run_in_executor(_GIT_EXECUTOR, _run)
        except subprocess.TimeoutExpired as e:
            raise GitTimeoutError(" ".join(args), effective_timeout) from e
        except subprocess.CalledProcessError as e:
            raise GitCommandError(
                " ".join(args), e.stderr or e.stdout or "Unknown error"
            ) from e
        git_ms = (time.monotonic() - t0) * 1000.0

        # Hand .git (and tracked files) back to the agent: this root-run op
        # created root-owned files under .git/. Runs in the dedicated git pool
        # so it doesn't compete with the event loop's default executor.
        t1 = time.monotonic()
        await loop.run_in_executor(_GIT_EXECUTOR, _ensure_agent_owned, workspace)
        chown_ms = (time.monotonic() - t1) * 1000.0

        # Surface slow git/chown ops (instrumentation): a single line that
        # pinpoints where an op's time went — the subprocess (e.g. a push to
        # origin) vs the ownership repair — without flooding the fast path.
        if git_ms > _SLOW_GIT_OP_MS or chown_ms > _SLOW_GIT_OP_MS:
            self.log.warning(
                "slow git op",
                op=op,
                git_ms=round(git_ms),
                chown_ms=round(chown_ms),
                timeout_s=effective_timeout,
                workspace=str(workspace),
            )
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
                    default_branch=project.default_branch or "master",
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

    _PORCELAIN_STATUS_WIDTH: ClassVar[int] = 2

    @staticmethod
    def _classify_porcelain(
        lines: list[str],
    ) -> tuple[list[str], list[str], list[str]]:
        """Split `git status --porcelain` lines into staged/unstaged/untracked.

        Canonical porcelain format is `XY PATH` — 2 status chars, 1 space
        separator, filename. Slicing `line[3:]` ASSUMES both X and Y are
        always present as chars. Git sometimes emits a single-column
        status (e.g., `M README.md` from very old git or non-canonical
        callers), which trips `line[3:]` into producing "EADME.md".
        Split on the first whitespace run to survive either layout.
        """
        staged: list[str] = []
        unstaged: list[str] = []
        untracked: list[str] = []
        min_len = GitService._PORCELAIN_STATUS_WIDTH + 1  # status + separator
        expected_parts = 2  # split gives [status, path]
        for line in lines:
            if not line or len(line) < min_len:
                continue
            # Canonical: XY is positions [0:2], separator at [2], path [3:].
            # Defensive: if the char at position 2 isn't whitespace (broken
            # status line), fall back to splitting on whitespace.
            if line[2] in (" ", "\t"):
                status_code = line[:2]
                file_path = line[3:]
            else:
                parts = line.split(maxsplit=1)
                if len(parts) < expected_parts:
                    continue
                status_code = parts[0].ljust(GitService._PORCELAIN_STATUS_WIDTH)[
                    : GitService._PORCELAIN_STATUS_WIDTH
                ]
                file_path = parts[1]
            if not file_path:
                continue
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
        # Use splitlines(), NOT stdout.strip().split("\n"): porcelain encodes the
        # index column in position 0, which is a SPACE for a worktree-only change
        # (e.g. " D file" = unstaged deletion). strip() eats that leading space on
        # the first line, turning " D file" into "D file" — which then parses as a
        # STAGED deletion. That false "staged" caused 6 wasted QA cycles when a
        # dev deleted a file but hadn't staged it. splitlines() preserves columns.
        lines = status_result.stdout.splitlines() if status_result.stdout else []

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
        POST /api/git/commit. Inspect loaded-state first and return
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

        When ``request.task_id`` is ``None`` the traceability template is
        skipped and a plain conventional-commit message is built instead
        (``type(scope): description``).  The git commit still happens; the
        commit is just not linked to any task record.

        Returns: (commit_hash, full_message, files_changed, insertions, deletions)
        """
        # Stage files. Large changesets get the longer commit-timeout budget
        # (see `git_commit_timeout_seconds`) — the same reason the gateway
        # `commit()` adapter uses it.
        commit_timeout = _commit_git_timeout()
        if request.files:
            for file in request.files:
                await self._run_git(workspace, ["add", file], timeout=commit_timeout)
        else:
            await self._run_git(workspace, ["add", "-A"], timeout=commit_timeout)

        if request.task_id is not None:
            # Get task info for commit template
            task_service = get_task_service(self.session)
            task = await task_service.get(request.task_id)

            # Get root task ID (walk up hierarchy)
            root_task_id = await get_root_task_id(request.task_id, task_service)

            # Get session ID
            session_id = self._get_primary_session_id(task)

            # Build commit message using template
            commit_ctx = CommitContext(
                task_id=str(request.task_id),
                root_task_id=str(root_task_id),
                agent_slug=str(agent_id),
                session_id=session_id,
                commit_type=request.commit_type,
                scope=request.scope,
                description=request.message,
                body=request.body,
            )
            full_message = build_commit_message(
                commit_ctx, settings.public_base_url.rstrip("/") + "/api"
            )
        else:
            # No task context — use plain conventional-commit format so the
            # git op still proceeds without raising CommitMessageError.
            type_scope = (
                f"{request.commit_type}({request.scope})"
                if request.scope
                else request.commit_type
            )
            full_message = f"{type_scope}: {request.message}"
            if request.body:
                full_message = f"{full_message}\n\n{request.body}"

        # Create commit with agent attribution
        author = f"{agent_id} <{agent_id}@roboco.ai>"
        await self._run_git(
            workspace,
            ["commit", "-m", full_message, "--author", author],
            timeout=commit_timeout,
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

    async def _assert_task_owned_with_branch(
        self, task_id: UUID, agent_id: UUID
    ) -> TaskTable:
        """Load task + check assignee + branch. Raises typed service errors."""
        task_service = get_task_service(self.session)
        task = await task_service.get(task_id)
        if not task:
            raise NotFoundError(resource_type="Task", resource_id=str(task_id))
        if task.assigned_to != agent_id:
            raise UnauthorizedError(
                action="commit",
                reason=(
                    "NOT_ASSIGNED: Only the assigned agent can operate on "
                    "this task's branch."
                ),
            )
        if not task.branch_name:
            raise ValidationError(
                "NO_BRANCH: Task has no branch set. Claim the task to "
                "generate one before committing."
            )
        return task

    async def _assert_on_task_branch(
        self, workspace: Path, task_branch: str | None
    ) -> None:
        """Reject ops when the workspace is on a branch other than the task's."""
        if not task_branch:
            return
        current_branch = await self.get_current_branch(workspace)
        if current_branch and current_branch != task_branch:
            raise ValidationError(
                f"BRANCH_MISMATCH: Workspace is on '{current_branch}' but "
                f"task requires '{task_branch}'. The branch is checked out "
                f"into your clone by your role's claim verb: "
                f"`i_will_work_on(task_id)` (devs), "
                f"`i_will_plan(task_id, plan)` (PMs), "
                f"`claim_doc_task(task_id)` (documenters), "
                f"`claim_review(task_id)` (QA). Re-call your role's claim "
                f"verb on this task instead of switching branches by hand."
            )

    async def _link_commit_to_task(
        self,
        task_uuid: UUID,
        commit_hash: str,
        message: str,
        agent_id: UUID,
    ) -> None:
        """Attach a new commit to the task + work session (best effort).

        Linking failures are logged but do not fail the commit: the commit
        itself has already landed on the branch. Silent-swallow would hide
        regressions in either add_commit path, so we log warnings instead.
        """
        task_service = get_task_service(self.session)
        try:
            task = await task_service.get(task_uuid)
            await task_service.add_commit(
                task_id=task_uuid,
                hash=commit_hash,
                message=message,
                agent_id=agent_id,
            )
            if task and task.work_session_id:
                work_session_service = get_work_session_service(self.session)
                await work_session_service.add_commit(
                    require_uuid(task.work_session_id), commit_hash
                )
            await self.session.commit()
        except Exception as e:
            self.log.warning(
                "Commit linking failed; commit present on branch but "
                "task rows not updated",
                task_id=str(task_uuid),
                commit_hash=commit_hash,
                error=str(e),
            )

    async def commit_for_task(
        self,
        agent_id: UUID,
        data: GitCommitRequest,
    ) -> tuple[str, str, int, int, int]:
        """Create a commit for the caller's assigned task.

        Orchestrates precondition checks, workspace resolution, the git
        commit itself, and commit-to-task linking. Raises typed service
        errors; the API layer translates them to HTTP status codes.

        When ``data.task_id`` is ``None`` the ownership, assignment, and
        branch-mismatch checks are skipped — the git commit proceeds
        unconditionally, and no commit-to-task linking is recorded.

        Returns: (commit_hash, full_message, files_changed, insertions, deletions)
        """
        if data.task_id is not None:
            task = await self._assert_task_owned_with_branch(data.task_id, agent_id)
            workspace = await self.get_workspace(data.project_slug, agent_id)
            await self._assert_on_task_branch(workspace, task.branch_name)
        else:
            workspace = await self.get_workspace(data.project_slug, agent_id)

        (
            commit_hash,
            full_message,
            files_changed,
            insertions,
            deletions,
        ) = await self.create_commit(workspace, agent_id, data)

        if data.task_id is not None:
            await self._link_commit_to_task(
                data.task_id, commit_hash, data.message, agent_id
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
        """Return the project's configured default branch, or 'master'."""
        project_service = get_project_service(self.session)
        project = await project_service.get_by_slug(project_slug)
        return (
            str(project.default_branch)
            if project and project.default_branch
            else "master"
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
        task_id = request.task_id
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

        # Fetch ONLY the refs this path needs (base + default), not every
        # remote branch — an all-refs `fetch origin` on a monorepo with dozens
        # of stale branches (dependabot, feature, abandoned) is pure network
        # cost on every branch creation. check=False so a base that isn't on
        # the remote yet doesn't abort (the fallback below handles it).
        fetch_refs = list(dict.fromkeys([base_branch, default_branch]))
        await self._run_git(
            workspace,
            ["fetch", "origin", *fetch_refs],
            token=project_token,
            check=False,
            timeout=_network_git_timeout(),
        )

        # The dev workspace is one persistent clone shared across this dev's
        # tasks, so a finished/abandoned prior task can leave it dirty and on a
        # sibling branch. Without a clean tree the base + feature checkouts below
        # fail; and because this git work is a side-effect that runs AFTER the
        # claim's DB transition has committed, a failed checkout leaves the
        # workspace on the wrong branch while the task is already marked
        # assigned — so the dev's next commit is rejected with BRANCH_MISMATCH.
        # This runs only on a FRESH claim (resume short-circuits in _dev_reentry
        # before reaching here), so any uncommitted changes are abandoned cruft
        # from a finished task — safe to discard. `reset --hard` clears tracked
        # changes; the gitignored .venv (and other ignored files) are untouched.
        await self._run_git(workspace, ["reset", "--hard"], check=False)

        base_branch = await self._checkout_base_with_fallback(
            workspace, base_branch, default_branch, task_id
        )

        # Fast-forward the checked-out base to the freshly-fetched remote tip.
        # A plain `git pull origin <base>` is fragile in automation: if the
        # local base has diverged at all it aborts with exit 128 ("Need to
        # specify how to reconcile divergent branches" / refusing to merge
        # unrelated histories), which then blows up the whole claim. We only
        # ever want the latest remote base before cutting a branch, so a local
        # `merge --ff-only origin/<base>` is the right intent — and it uses the
        # ref the scoped fetch above already updated (no second network call).
        # check=False: a non-fast-forward (divergent local) or a base that
        # isn't on the remote yet leaves the checked-out base as the branch
        # point instead of aborting branch creation.
        await self._run_git(
            workspace,
            ["merge", "--ff-only", f"origin/{base_branch}"],
            check=False,
        )
        # Idempotent branch creation: a prior attempt may have created the
        # branch on disk but failed before the DB recorded branch_name (the
        # claim rolls back its fields, but the on-disk branch persists). A
        # plain `checkout -b` then fails "already exists" (exit 128), and the
        # resulting error-handling cascade is how a retry spirals. Switch to
        # the existing branch instead.
        created = await self._run_git(
            workspace, ["checkout", "-b", branch_name], check=False
        )
        if created.returncode != 0:
            await self._run_git(workspace, ["checkout", branch_name])
            # The branch already existed on disk. If it carries no commits of
            # its own — a dependency-blocked task branched before its upstream
            # merged into the integration branch, then released and re-claimed —
            # re-point it at the freshly-pulled base so the agent builds on the
            # current integration tip, not a stale snapshot. Guarded on "no
            # commits unique to the branch": a branch with real work is left
            # exactly as-is.
            unique = await self._run_git(
                workspace,
                ["rev-list", "--count", f"{base_branch}..{branch_name}"],
                check=False,
            )
            if unique.returncode == 0 and unique.stdout.strip() == "0":
                await self._run_git(
                    workspace, ["reset", "--hard", base_branch], check=False
                )
        await self._run_git(
            workspace,
            ["push", "-u", "origin", branch_name],
            token=project_token,
            timeout=_network_git_timeout(),
        )
        # Ownership re-chown happens automatically inside `_run_git` now.

        # Store branch name on task
        await task_service.update(task_id, branch_name=branch_name)

        return branch_name, base_branch

    async def create_branch_from_pr_head(
        self,
        workspace: Path,
        project_slug: str,
        pr_number: int,
        branch_name: str,
    ) -> str:
        """Create + push a roboco-owned branch off a fork PR's head commits.

        Fork PR heads are NOT branches on origin; GitHub exposes them at the
        special ref ``refs/pull/{n}/head``. We fetch that ref into a
        roboco-owned local branch and push it to origin, so a dev cell can
        finish the contribution on a branch WE own and merge — we NEVER push to
        the contributor's fork. This is the first point untrusted contributor
        code enters a roboco branch, so the caller MUST only invoke it for a
        human-confirmed (``confirmed_by_human``) supersede.
        """
        project_token = await self._token_for_project(project_slug)
        pull_ref = f"refs/pull/{pr_number}/head"
        # Force the refspec (``+``) so a retry after a prior push (e.g. the
        # commit after the push failed and rolled the umbrella back) updates the
        # leftover local branch in the persistent system workspace instead of
        # hard-erroring on the existing ref — the branch cut is then idempotent.
        await self._run_git(
            workspace,
            ["fetch", "origin", f"+{pull_ref}:{branch_name}"],
            token=project_token,
            timeout=_network_git_timeout(),
        )
        await self._run_git(workspace, ["checkout", branch_name])
        await self._run_git(
            workspace,
            ["push", "-u", "origin", branch_name],
            token=project_token,
            timeout=_network_git_timeout(),
        )
        return branch_name

    @staticmethod
    def _enum_str(value: Any) -> str | None:
        """Return .value when present; otherwise str(), preserving None."""
        if value is None:
            return None
        return value.value if hasattr(value, "value") else str(value)

    def _resolve_branch_team(self, project_slug: str, project: Any, task: Any) -> str:
        """Pick the `team` segment for a create_branch call.

        Uses the TASK's team first (who is doing the work), then falls
        back to the project's assigned cell. This ensures a main-pm
        planning task gets a main_pm branch even if the project cell
        is backend.
        """
        project_cell = (
            self._enum_str(project.assigned_cell)
            if project and project.assigned_cell
            else None
        )
        task_team = self._enum_str(task.team) if task and task.team else None
        if project_cell == "fullstack":
            return f"{project_slug}/{task_team or 'cross'}"
        if task_team:
            return task_team
        if project_cell:
            return project_cell
        return "cross"

    async def create_branch_for_task(
        self,
        agent_id: UUID,
        data: GitCreateBranchRequest,
    ) -> tuple[str, str]:
        """Resolve workspace + team segment, create the branch, commit.

        Returns: (branch_name, created_from)
        """
        task_service = get_task_service(self.session)
        project_service = get_project_service(self.session)

        workspace = await self.get_workspace(data.project_slug, agent_id)
        task = await task_service.get(data.task_id)
        project = await project_service.get_by_slug(data.project_slug)

        team_for_branch = self._resolve_branch_team(data.project_slug, project, task)
        branch_name, created_from = await self.create_branch(
            workspace, team_for_branch, data
        )

        await self.session.commit()
        return branch_name, created_from

    async def checkout(self, workspace: Path, branch: str) -> None:
        """Checkout a branch.

        Fetches from origin first to ensure remote branches are available.
        If the branch doesn't exist locally, creates a tracking branch from remote.
        """
        token = await self._token_for_workspace(workspace)
        # Fetch just the branch we're about to check out, not every remote ref.
        # check=False: a not-yet-pushed branch makes this a no-op, and the
        # local/tracking checkout below still works.
        await self._run_git(
            workspace,
            ["fetch", "origin", branch],
            token=token,
            check=False,
            timeout=_network_git_timeout(),
        )

        # Try direct checkout first (works if local branch exists)
        result = await self._run_git(workspace, ["checkout", branch], check=False)
        if result.returncode != 0:
            # Branch doesn't exist locally - create tracking branch from remote
            await self._run_git(
                workspace, ["checkout", "-b", branch, f"origin/{branch}"]
            )

    async def _allowed_checkout_branches(
        self, project_slug: str, agent_id: UUID
    ) -> set[str]:
        """Collect branches this agent is allowed to checkout."""
        from sqlalchemy import or_, select

        from roboco.db.tables import TaskTable

        project_service = get_project_service(self.session)
        task_service = get_task_service(self.session)
        project = await project_service.get_by_slug(project_slug)
        allowed: set[str] = set()
        if project and project.default_branch:
            allowed.add(project.default_branch)

        # Include tasks where agent is either assignee OR claimer
        result = await self.session.execute(
            select(TaskTable)
            .where(
                or_(
                    TaskTable.assigned_to == agent_id,
                    TaskTable.claimed_by == agent_id,
                )
            )
            .where(TaskTable.branch_name.is_not(None))
        )
        for t in result.scalars().all():
            allowed.add(str(t.branch_name))

        # Also include ancestor branches (parent task branches) so devs
        # can checkout parent branches when working on subtasks.
        my_tasks = await task_service.list_by_assignee(agent_id)
        for t in my_tasks:
            if t.branch_name:
                allowed.add(str(t.branch_name))

        return allowed

    async def checkout_branch_for_agent(
        self,
        agent_id: UUID,
        data: GitCheckoutRequest,
    ) -> None:
        """Enforce the checkout allowlist + run the checkout.

        Agents can only check out branches they have reason to be on: any
        of their own assigned tasks' branches, or the project default
        branch (read-only inspection). Hierarchical prefixes are allowed
        so a PM can checkout the parent branch of a subtask they own.
        """
        allowed = await self._allowed_checkout_branches(data.project_slug, agent_id)
        if data.branch not in allowed and not any(
            owned.startswith(f"{data.branch}--") for owned in allowed
        ):
            raise UnauthorizedError(
                action="checkout",
                reason=(
                    f"CHECKOUT_RESTRICTED: Cannot checkout '{data.branch}'. "
                    f"Allowed: {sorted(allowed)} (and their ancestors). "
                    "Claim the task whose branch you want to work on."
                ),
            )

        workspace = await self.get_workspace(data.project_slug, agent_id)
        await self.checkout(workspace, data.branch)

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

        try:
            await self._run_git(
                workspace, args, token=token, timeout=_network_git_timeout()
            )
        except GitCommandError as e:
            # A >100MB file trips GitHub's GH001 pre-receive hook — a PERMANENT
            # rejection that retrying can never fix. Restate it unmistakably so
            # the agent stops blind-retrying (it otherwise mis-reads the raw
            # output as a transient timeout) and removes the file / blocks.
            blob = f"{e}".lower()
            if any(
                m in blob
                for m in (
                    "gh001",
                    "exceeds github's file size",
                    "100.00 mb",
                    "pre-receive hook declined",
                )
            ):
                raise GitCommandError(
                    "push",
                    "rejected — a committed file exceeds GitHub's 100 MB limit"
                    " (GH001). Retrying will NOT help: remove the oversized file"
                    " (usually a build/dependency artifact like a node or pnpm"
                    " store) from the commit and re-commit, or call i_am_blocked"
                    " if you cannot.",
                ) from e
            raise

        return branch, commits_to_push

    async def push_for_task(
        self,
        agent_id: UUID,
        agent_role: AgentRole | str,
        data: Any,
    ) -> tuple[str, int]:
        """Push commits for the caller's assigned task.

        Force-push is CEO-only. Otherwise standard assignee + branch
        preconditions. Raises typed service errors; the API layer
        translates them to HTTP status codes.

        When ``data.task_id`` is ``None`` the ownership and branch-mismatch
        checks are skipped — the push proceeds unconditionally (force-push
        role check still applies).

        Returns: (branch, commits_pushed)
        """
        if getattr(data, "force", False) and agent_role != AgentRole.CEO:
            raise UnauthorizedError(
                action="force_push",
                reason=(
                    "FORCE_PUSH_FORBIDDEN: Force-push is CEO-only. If your "
                    "branch diverged, `unclaim` the task and re-`claim` it — "
                    "the choreographer will rebuild the branch and you can "
                    "replay your commits via `commit(...)`."
                ),
            )

        if data.task_id is not None:
            task = await self._assert_task_owned_with_branch(data.task_id, agent_id)
            workspace = await self.get_workspace(data.project_slug, agent_id)
            await self._assert_on_task_branch(workspace, task.branch_name)
        else:
            workspace = await self.get_workspace(data.project_slug, agent_id)

        return await self.push(workspace, getattr(data, "force", False))

    async def push_task_branch(self, agent_id: UUID, task_id: UUID) -> int:
        """Idempotently push a task's branch to origin; return commits pushed.

        Reviewers see the remote PR branch, not the developer's workspace. A
        fix committed during a revision cycle lives only in that local clone
        until it is pushed — so without an explicit push at the QA-submission
        boundary, QA re-reviews the stale remote and fails the same task on
        every cycle. Self-resolves the project/workspace from the task so the
        choreographer can call it with just (agent, task). A no-op when there
        is nothing unpushed; raises typed service errors on a real failure.
        """
        task = await self._assert_task_owned_with_branch(task_id, agent_id)
        project = await self._project_for_task(task)
        if project is None:
            return 0
        workspace = await self.get_workspace(project.slug, agent_id)
        await self._assert_on_task_branch(workspace, task.branch_name)
        _branch, pushed = await self.push(workspace)
        return pushed

    # =========================================================================
    # PULL / FETCH / REBASE METHODS
    # =========================================================================

    async def pull(
        self, workspace: Path
    ) -> tuple[str, bool, list[str], list[str], list[str], int, int]:
        """Pull latest changes from origin and return post-pull status.

        Safety gates (both raise :class:`ValidationError`):

        1. **Dirty workspace** — refuses to pull when there are any
           uncommitted changes (staged, unstaged, or untracked).  A pull
           onto a dirty tree can produce unexpected merge conflicts or
           silently clobber un-staged edits.

        2. **Diverged branch** — uses ``--ff-only`` so a diverged branch
           is rejected rather than creating a merge commit.  The agent
           must rebase or reset before pulling.

        Uses _network_git_timeout() because the operation talks to origin.

        Returns: (current_branch, has_changes, staged, unstaged, untracked,
                  ahead, behind)
        """
        # Gate 1: dirty workspace check
        status_result = await self._run_git(
            workspace, ["status", "--porcelain"], check=False
        )
        if status_result.stdout.strip():
            raise ValidationError(
                "DIRTY_WORKSPACE: Cannot pull with uncommitted changes. "
                "Stage and commit (or stash) your changes before pulling."
            )

        token = await self._token_for_workspace(workspace)
        # Gate 2: fast-forward only — raises if branches have diverged
        pull_result = await self._run_git(
            workspace,
            ["pull", "--ff-only"],
            token=token,
            timeout=_network_git_timeout(),
            check=False,
        )
        if pull_result.returncode != 0:
            stderr = (pull_result.stderr or "").lower()
            if any(
                kw in stderr
                for kw in (
                    "not possible to fast-forward",
                    "diverged",
                    "fatal: not possible to fast",
                    "fast-forward",
                )
            ):
                raise ValidationError(
                    "DIVERGED_BRANCH: Branch has diverged from remote; "
                    "cannot fast-forward. Rebase your local commits onto "
                    "the remote tip with `git rebase origin/<branch>` "
                    "before pulling."
                )
            raise ValidationError(
                f"PULL_FAILED: git pull --ff-only exited non-zero. "
                f"stderr: {pull_result.stderr or '(none)'}"
            )
        return await self.get_status(workspace)

    async def fetch(
        self, workspace: Path
    ) -> tuple[str, bool, list[str], list[str], list[str], int, int]:
        """Fetch changes from origin without merging and return post-fetch status.

        Uses _network_git_timeout() because the operation talks to origin.

        Returns: (current_branch, has_changes, staged, unstaged, untracked,
                  ahead, behind)
        """
        token = await self._token_for_workspace(workspace)
        await self._run_git(
            workspace,
            ["fetch", "origin"],
            token=token,
            timeout=_network_git_timeout(),
        )
        return await self.get_status(workspace)

    async def rebase(
        self, workspace: Path, target_branch: str
    ) -> tuple[bool, list[str]]:
        """Rebase the current branch onto target_branch.

        Safety gate: raises :class:`ValidationError` if the HEAD branch or
        ``target_branch`` is ``master`` or ``main`` — rebasing a protected
        integration branch is never safe in automation.

        On conflict (non-zero exit): captures unmerged files via
        ``git diff --name-only --diff-filter=U``, aborts the rebase to
        restore a clean workspace, and returns ``(True, conflicted_files)``.

        On success: returns ``(False, [])``.
        """
        _PROTECTED = frozenset({"master", "main"})
        if target_branch in _PROTECTED:
            raise ValidationError(
                f"REBASE_FORBIDDEN: Cannot rebase onto '{target_branch}'. "
                "Rebasing onto 'master' or 'main' is not allowed in automation."
            )
        head_branch = await self.get_current_branch(workspace)
        if head_branch in _PROTECTED:
            raise ValidationError(
                f"REBASE_FORBIDDEN: Cannot rebase '{head_branch}'. "
                "Rebasing 'master' or 'main' is not allowed in automation."
            )
        result = await self._run_git(workspace, ["rebase", target_branch], check=False)
        if result.returncode != 0:
            conflict_result = await self._run_git(
                workspace,
                ["diff", "--name-only", "--diff-filter=U"],
                check=False,
            )
            conflicted_files = [
                f.strip()
                for f in conflict_result.stdout.strip().split("\n")
                if f.strip()
            ]
            await self._run_git(workspace, ["rebase", "--abort"], check=False)
            return True, conflicted_files
        return False, []

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
        async with httpx.AsyncClient(timeout=_default_git_timeout()) as client:
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

    async def list_open_prs(self, project_slug: str) -> list[dict[str, Any]]:
        """List a project's open PRs, normalized with fork/author classification.

        The inbound counterpart to the org's outbound PR calls: lists ALL open
        PRs (no ``head=`` filter), so it sees external/fork contributions the org
        did not create. Each record carries ``number``, ``url``, ``title``,
        ``head_ref``, ``head_sha`` (the head commit — the change signal for
        re-review), ``is_fork`` (head repo differs from base repo),
        ``user_login`` and ``author_association`` so the caller can classify
        trust. Returns ``[]`` on a missing token, unparseable remote, or any
        GitHub error — it never raises into the poll loop.
        """
        project = await get_project_service(self.session).get_by_slug(project_slug)
        if project is None or not project.git_url:
            return []
        try:
            owner, repo = self._parse_git_url(project.git_url)
        except GitError:
            return []
        git_token = await self._token_for_project(project_slug)
        if not git_token:
            return []
        raw = await self._fetch_open_prs(project_slug, owner, repo, git_token)
        if raw is None:
            return []
        base_full = f"{owner}/{repo}"
        return [self._normalize_open_pr(pr, base_full) for pr in raw]

    async def _fetch_open_prs(
        self, project_slug: str, owner: str, repo: str, git_token: str
    ) -> list[dict[str, Any]] | None:
        """GET a repo's open PRs; return the raw list, or None on any error."""
        api_base = settings.github_api_base_url.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=_default_git_timeout()) as client:
                resp = await client.get(
                    f"{api_base}/repos/{owner}/{repo}/pulls",
                    headers={
                        "Authorization": f"Bearer {git_token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                    params={"state": "open", "per_page": 100},
                )
        except httpx.HTTPError as e:
            self.log.warning(
                "list_open_prs request failed", project=project_slug, error=str(e)
            )
            return None
        if not resp.is_success:
            self.log.warning(
                "list_open_prs non-2xx",
                project=project_slug,
                status=resp.status_code,
            )
            return None
        data = resp.json()
        return data if isinstance(data, list) else []

    @staticmethod
    def _normalize_open_pr(pr: dict[str, Any], base_full: str) -> dict[str, Any]:
        """Normalize one GitHub PR payload into the inbound-review record."""
        head = pr.get("head") or {}
        head_repo = head.get("repo") or {}
        head_full = head_repo.get("full_name")
        login = (pr.get("user") or {}).get("login")
        base_owner = (base_full or "").split("/")[0]
        return {
            "number": pr.get("number"),
            "url": pr.get("html_url") or "",
            "title": pr.get("title") or "",
            "head_ref": head.get("ref"),
            "head_sha": head.get("sha"),
            "is_fork": bool(head_full and head_full != base_full),
            "user_login": login,
            # The reviewer reviews PRs the org did NOT author. A PR opened by the
            # repo-owner account is a self-review (GitHub 422s REQUEST_CHANGES on
            # your own PR) and re-reviewing the org's own in-flight work is noise —
            # ingestion skips these.
            "author_is_owner": bool(
                login and base_owner and login.lower() == base_owner.lower()
            ),
            "author_association": pr.get("author_association"),
        }

    async def get_latest_ci_conclusion(
        self, project_slug: str, *, workflow: str | None = None
    ) -> dict[str, Any] | None:
        """Latest completed CI (GitHub Actions) run on a project's default branch.

        The inbound telemetry signal for self-healing: the most recent COMPLETED
        workflow run on the project's default branch, normalized to
        ``conclusion`` (``success`` / ``failure`` / ``timed_out`` / ...),
        ``head_sha``, ``run_url``, ``run_name``, ``branch`` and ``completed_at``.
        Resolves owner/repo and the git token PER PROJECT. ``workflow`` (a
        workflow file name like ``ci.yml``) scopes the signal to one workflow —
        without it the latest run across ALL workflows is used, which is
        imprecise on a multi-workflow repo. Returns ``None`` on a missing token,
        unparseable remote, GitHub error, or a repo with no matching Actions runs
        (a repo that doesn't use GitHub Actions yields no signal, not a false
        one). It never raises into the poll loop.
        """
        project = await get_project_service(self.session).get_by_slug(project_slug)
        if project is None or not project.git_url:
            return None
        try:
            owner, repo = self._parse_git_url(project.git_url)
        except GitError:
            return None
        git_token = await self._token_for_project(project_slug)
        if not git_token:
            return None
        branch = project.default_branch or "master"
        run = await self._fetch_latest_ci_run(
            project_slug, (owner, repo), branch, git_token, workflow
        )
        if run is None:
            return None
        return {
            "conclusion": run.get("conclusion"),
            "head_sha": run.get("head_sha"),
            "run_url": run.get("html_url") or "",
            "run_name": run.get("name") or "",
            "branch": branch,
            "completed_at": run.get("updated_at"),
        }

    async def _get_ci_runs_response(
        self,
        project_slug: str,
        url: str,
        headers: dict[str, str],
        params: dict[str, str | int],
    ) -> httpx.Response | None:
        """GET *url* with retry/back-off; return the successful response or None."""
        resp: httpx.Response | None = None
        for attempt in range(_CI_FETCH_ATTEMPTS):
            last = attempt + 1 == _CI_FETCH_ATTEMPTS
            try:
                async with httpx.AsyncClient(timeout=_default_git_timeout()) as client:
                    resp = await client.get(url, headers=headers, params=params)
            except httpx.HTTPError as e:
                if last:
                    self.log.warning(
                        "get_latest_ci_conclusion request failed",
                        project=project_slug,
                        error=str(e),
                    )
                    return None
                await asyncio.sleep(_CI_FETCH_BACKOFF_SECONDS * (attempt + 1))
                continue
            if resp.is_success:
                return resp
            if resp.status_code in _CI_RETRYABLE_STATUS and not last:
                await asyncio.sleep(_CI_FETCH_BACKOFF_SECONDS * (attempt + 1))
                continue
            self.log.warning(
                "get_latest_ci_conclusion non-2xx",
                project=project_slug,
                status=resp.status_code,
            )
            return None
        return resp

    async def _fetch_latest_ci_run(
        self,
        project_slug: str,
        owner_repo: tuple[str, str],
        branch: str,
        git_token: str,
        workflow: str | None = None,
    ) -> dict[str, Any] | None:
        """Resolve ``branch``'s current-HEAD CI conclusion; None on error.

        Scopes to ``workflow`` (a workflow file name) when given — the precise
        signal — otherwise reads across ALL workflows, which on a multi-workflow
        repo is unreliable (an unrelated green run can mask a red CI run). Pulls a
        WINDOW of recent completed runs and selects the newest commit's latest
        attempt (see ``_select_ci_head_run``) rather than the single
        most-recently-completed run, so a green run on an older commit can't mask
        the HEAD's failure and a green re-run correctly supersedes it. ``branch``
        filters by head branch, so only pushes to the default branch (not
        pull-request runs, whose head is a feature branch) count — exactly the
        "is the default branch red" signal self-heal needs. Transient network /
        429 / 5xx errors are retried a few times before giving up so a single
        blip doesn't silently skip the cycle.
        """
        owner, repo = owner_repo
        api_base = settings.github_api_base_url.rstrip("/")
        base = f"{api_base}/repos/{owner}/{repo}/actions"
        url = f"{base}/workflows/{workflow}/runs" if workflow else f"{base}/runs"
        headers = {
            "Authorization": f"Bearer {git_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        params: dict[str, str | int] = {
            "branch": branch,
            "status": "completed",
            "per_page": _CI_RUN_WINDOW,
        }
        resp = await self._get_ci_runs_response(project_slug, url, headers, params)
        if resp is None or not resp.is_success:
            return None
        data = resp.json()
        runs = data.get("workflow_runs") if isinstance(data, dict) else None
        if not runs:
            return None
        return _select_ci_head_run(runs)

    async def _post_pr(
        self,
        owner: str,
        repo: str,
        git_token: str,
        payload: dict[str, Any],
    ) -> httpx.Response:
        """POST the PR payload to GitHub; translate HTTP errors to GitError."""
        try:
            async with httpx.AsyncClient(timeout=_default_git_timeout()) as client:
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

    async def _pr_base_on_remote(
        self,
        workspace: Path,
        target_branch: str,
        default_branch: str,
        git_token: str,
        task_id: UUID,
    ) -> str:
        """Return a PR base branch that actually exists on origin.

        GitHub rejects PR creation with 422 "base field invalid" when the base
        branch is absent on the remote — which happens when an ancestor task's
        branch was claimed but never pushed (e.g. a PM paused before any commit),
        stranding every child at PR time. Mirror the branch-cutting fallback in
        ``create_branch``: if the resolved base is missing on origin, retarget to
        the default branch instead of hard-failing. The default branch is assumed
        present, so it is returned unchecked.
        """
        if target_branch == default_branch:
            return target_branch
        ls = await self._run_git(
            workspace,
            ["ls-remote", "--heads", "origin", target_branch],
            check=False,
            token=git_token,
        )
        if ls.stdout.strip():
            return target_branch
        self.log.warning(
            "PR base branch not on remote; retargeting PR to the default branch",
            base_branch=target_branch,
            default_branch=default_branch,
            task_id=str(task_id),
        )
        return default_branch

    async def create_pull_request(
        self, workspace: Path, request: GitCreatePRRequest
    ) -> tuple[int, str, str, str, str]:
        """Create a pull request via the GitHub REST API.

        When ``request.task_id`` is ``None`` the task lookup, target-branch
        resolution, and template generation are skipped.  The PR targets the
        project's default branch and uses ``request.title`` / ``request.body``
        directly (falling back to ``source_branch`` / ``""`` when absent).

        Returns: (pr_number, pr_url, title, source_branch, target_branch)
        """
        source_branch = await self.get_current_branch(workspace)
        default_branch = await self._project_default_branch(request.project_slug)
        git_token = await self._get_project_token_or_raise(request.project_slug)
        target_branch, pr_title, pr_body = await self._resolve_new_pr_context(
            workspace, request, source_branch, default_branch, git_token
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

        existing = await self._existing_pr_tuple(
            resp, (owner, repo), (source_branch, target_branch), git_token, pr_title
        )
        if existing is not None:
            return existing

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

    async def _resolve_new_pr_context(
        self,
        workspace: Path,
        request: GitCreatePRRequest,
        source_branch: str,
        default_branch: str,
        git_token: str,
    ) -> tuple[str, str | None, str | None]:
        """Resolve (target_branch, title, body) for a new PR.

        With a ``task_id``: look the task up, resolve the target + remote base,
        and generate the title/body from templates. Without one: target the
        default branch and use the provided title/body (or minimal fallbacks).
        """
        if request.task_id is None:
            return default_branch, request.title or source_branch, request.body or ""
        task = await get_task_service(self.session).get(request.task_id)
        if not task:
            raise NotFoundError("Task", str(request.task_id))
        target_branch = await self._resolve_pr_target_branch(
            request, task, default_branch
        )
        target_branch = await self._pr_base_on_remote(
            workspace, target_branch, default_branch, git_token, request.task_id
        )
        pr_title, pr_body = await self._generate_pr_title_body(
            request, task, source_branch, target_branch, request.task_id
        )
        return target_branch, pr_title, pr_body

    async def _existing_pr_tuple(
        self,
        resp: httpx.Response,
        owner_repo: tuple[str, str],
        branches: tuple[str, str],
        git_token: str,
        pr_title: str | None,
    ) -> tuple[int, str, str, str, str] | None:
        """Idempotency: if the create hit an 'already exists' 422, return that PR.

        ``owner_repo`` is (owner, repo); ``branches`` is (source, target).
        """
        if resp.status_code != _GH_UNPROCESSABLE or "already exists" not in resp.text:
            return None
        owner, repo = owner_repo
        source_branch, target_branch = branches
        found = await self._find_existing_pr(
            owner, repo, source_branch, target_branch, git_token
        )
        if not found:
            return None
        return (
            int(found["number"]),
            found["html_url"],
            found.get("title", pr_title or ""),
            source_branch,
            target_branch,
        )

    async def _patch_pr_title_body(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        git_token: str,
        payload: dict[str, str],
    ) -> None:
        """PATCH /repos/{owner}/{repo}/pulls/{pr_number} with title/body.

        Translates HTTP failures into GitError so the verb layer can map
        them onto invalid_state envelopes. 404 → "PR not found"; any
        other non-2xx surfaces the GitHub validation text inline.
        """
        try:
            async with httpx.AsyncClient(timeout=_default_git_timeout()) as client:
                resp = await client.patch(
                    f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}",
                    headers={
                        "Authorization": f"Bearer {git_token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                    json=payload,
                )
        except httpx.HTTPError as e:
            raise GitError(
                f"GitHub API error while updating PR #{pr_number}: {e}",
                {"owner": owner, "repo": repo, "pr": pr_number},
            ) from e
        if resp.status_code == _HTTP_NOT_FOUND:
            raise GitError(
                f"PR not found: #{pr_number} on {owner}/{repo}",
                {"owner": owner, "repo": repo, "pr": pr_number},
            )
        if not resp.is_success:
            raise GitError(
                f"GitHub API refused PR update ({resp.status_code}): {resp.text[:200]}",
                {"owner": owner, "repo": repo, "pr": pr_number},
            )

    async def _post_pr_reviewers(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        git_token: str,
        reviewers: list[str],
    ) -> None:
        """POST /repos/{owner}/{repo}/pulls/{pr_number}/requested_reviewers.

        Mirrors `_patch_pr_title_body` error handling. The reviewers list
        is passed through verbatim — caller is responsible for mapping
        agent slugs onto GitHub usernames where the project records that.
        """
        try:
            async with httpx.AsyncClient(timeout=_default_git_timeout()) as client:
                resp = await client.post(
                    f"https://api.github.com/repos/{owner}/{repo}/pulls/"
                    f"{pr_number}/requested_reviewers",
                    headers={
                        "Authorization": f"Bearer {git_token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                    json={"reviewers": reviewers},
                )
        except httpx.HTTPError as e:
            raise GitError(
                f"GitHub API error while adding reviewers to PR #{pr_number}: {e}",
                {"owner": owner, "repo": repo, "pr": pr_number},
            ) from e
        if resp.status_code == _HTTP_NOT_FOUND:
            raise GitError(
                f"PR not found: #{pr_number} on {owner}/{repo}",
                {"owner": owner, "repo": repo, "pr": pr_number},
            )
        if not resp.is_success:
            raise GitError(
                f"GitHub API refused reviewer request ({resp.status_code}): "
                f"{resp.text[:200]}",
                {"owner": owner, "repo": repo, "pr": pr_number},
            )

    async def post_pr_review(
        self,
        project_slug: str,
        pr_number: int,
        body: str,
        *,
        event: str = "REQUEST_CHANGES",
    ) -> dict[str, Any]:
        """Post ONE review to a PR — ``POST /pulls/{n}/reviews``.

        ``event`` is ``REQUEST_CHANGES`` (default), ``APPROVE``, or ``COMMENT``.
        Authenticates as the project's PAT owner (the bot account); the agent /
        role that authored the review is named in ``body``, not the GitHub
        identity. Raises ``GitError`` on any GitHub failure so the calling
        side-effect can surface it (and stays idempotent — it runs once, after
        the DB commit).

        Self-review fallback: GitHub forbids ``APPROVE`` / ``REQUEST_CHANGES`` on
        a PR authored by the token's own account (422 "...on your own pull
        request"). The org's internal PRs — and any PR the PAT owner opened —
        hit this, so the review would otherwise never land. A plain ``COMMENT``
        review IS allowed on your own PR, so this retries once as ``COMMENT``
        (the verdict is already stated in ``body``).
        """
        details = {"project": project_slug, "pr": pr_number}
        project = await get_project_service(self.session).get_by_slug(project_slug)
        if project is None or not project.git_url:
            raise GitError(f"unknown project for PR review: {project_slug!r}", details)
        owner, repo = self._parse_git_url(project.git_url)
        git_token = await self._token_for_project(project_slug)
        if not git_token:
            raise GitError(f"no git token for project {project_slug!r}", details)
        api_base = settings.github_api_base_url.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=_default_git_timeout()) as client:
                resp = await client.post(
                    f"{api_base}/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
                    headers={
                        "Authorization": f"Bearer {git_token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                    json={"body": body, "event": event},
                )
        except httpx.HTTPError as e:
            raise GitError(
                f"GitHub API error while posting review to PR #{pr_number}: {e}",
                details,
            ) from e
        if resp.status_code == _HTTP_NOT_FOUND:
            raise GitError(f"PR not found: #{pr_number} on {owner}/{repo}", details)
        if (
            resp.status_code == _GH_UNPROCESSABLE
            and event != "COMMENT"
            and "own pull request" in resp.text.lower()
        ):
            # Self-review: downgrade to a COMMENT review (allowed on your own PR)
            # so the review actually posts instead of being silently dropped.
            self.log.warning(
                "post_pr_review: self-review forbidden, retrying as COMMENT",
                project=project_slug,
                pr=pr_number,
                requested_event=event,
            )
            return await self.post_pr_review(
                project_slug, pr_number, body, event="COMMENT"
            )
        if not resp.is_success:
            raise GitError(
                f"GitHub API refused PR review ({resp.status_code}): {resp.text[:200]}",
                details,
            )
        return cast("dict[str, Any]", resp.json())

    async def get_pr_diff(self, project_slug: str, pr_number: int) -> str:
        """Fetch a PR's unified diff READ-ONLY via the GitHub API.

        ``GET /pulls/{n}`` with the diff media type returns the unified diff
        text without checking out or running any of the contributor's code —
        the review is read-only; untrusted fork code never executes here.
        Returns ``""`` on a missing token / unparseable remote / GitHub error
        so the reviewer's claim still returns context instead of crashing.
        """
        project = await get_project_service(self.session).get_by_slug(project_slug)
        if project is None or not project.git_url:
            return ""
        try:
            owner, repo = self._parse_git_url(project.git_url)
        except GitError:
            return ""
        git_token = await self._token_for_project(project_slug)
        if not git_token:
            return ""
        api_base = settings.github_api_base_url.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=_default_git_timeout()) as client:
                resp = await client.get(
                    f"{api_base}/repos/{owner}/{repo}/pulls/{pr_number}",
                    headers={
                        "Authorization": f"Bearer {git_token}",
                        "Accept": "application/vnd.github.v3.diff",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
        except httpx.HTTPError as e:
            self.log.warning(
                "get_pr_diff request failed",
                project=project_slug,
                pr=pr_number,
                error=str(e),
            )
            return ""
        if not resp.is_success:
            self.log.warning(
                "get_pr_diff non-2xx",
                project=project_slug,
                pr=pr_number,
                status=resp.status_code,
            )
            return ""
        return resp.text

    async def update_pr_for_task(
        self,
        task_id: UUID,
        *,
        title: str | None = None,
        body: str | None = None,
        reviewers: list[str] | None = None,
    ) -> dict[str, Any]:
        """Update an open PR's title/body and/or request reviewers.

        Looks up the task, resolves the project's GitHub PAT, and routes
        through `_patch_pr_title_body` (when title or body is set) and
        `_post_pr_reviewers` (when reviewers is set). Either or both run;
        the verb layer guarantees at least one is provided.

        Returns a dict with `pr_number`, `pr_url`, and a `updated_fields`
        list naming which of title/body/reviewers actually went out.
        """
        task_service = get_task_service(self.session)
        task = await task_service.get(task_id)
        if task is None:
            raise NotFoundError("Task", str(task_id))
        if task.pr_number is None:
            raise GitError(
                f"Task {task_id} has no PR open; cannot update.",
                {"task_id": str(task_id)},
            )

        project = await self._project_for_task(task)
        if project is None:
            raise NotFoundError("Project for task", str(task.id))

        workspace_agent_id = self._resolve_workspace_agent_id(task, None)
        workspace = await self.get_workspace(project.slug, agent_id=workspace_agent_id)
        owner, repo = self._parse_github_remote(workspace)
        git_token = await self._get_project_token_or_raise(project.slug)
        pr_number = int(task.pr_number)

        updated: list[str] = []
        patch_payload: dict[str, str] = {}
        if title is not None:
            patch_payload["title"] = title
            updated.append("title")
        if body is not None:
            patch_payload["body"] = body
            updated.append("body")
        if patch_payload:
            await self._patch_pr_title_body(
                owner, repo, pr_number, git_token, patch_payload
            )
        if reviewers is not None:
            await self._post_pr_reviewers(owner, repo, pr_number, git_token, reviewers)
            updated.append("reviewers")

        return {
            "pr_number": pr_number,
            "pr_url": str(task.pr_url) if task.pr_url else "",
            "updated_fields": updated,
        }

    _PR_OPEN_STATES: ClassVar[frozenset[str]] = frozenset(
        {
            TaskStatus.IN_PROGRESS.value,
            TaskStatus.VERIFYING.value,
            TaskStatus.AWAITING_QA.value,
            TaskStatus.AWAITING_DOCUMENTATION.value,
            TaskStatus.NEEDS_REVISION.value,
        }
    )

    @staticmethod
    def _status_value(task: Any) -> str:
        """Task.status may be Enum or str across code paths — normalize."""
        status_attr = task.status
        return status_attr.value if hasattr(status_attr, "value") else str(status_attr)

    async def _assert_pr_create_allowed(
        self, task_id: UUID, agent_id: UUID
    ) -> TaskTable:
        """Enforce assignee + state gate for opening a PR."""
        task_service = get_task_service(self.session)
        task = await task_service.get(task_id)
        if not task:
            raise NotFoundError(resource_type="Task", resource_id=str(task_id))
        if task.assigned_to != agent_id:
            raise UnauthorizedError(
                action="create_pr",
                reason="NOT_ASSIGNED: Only the assignee can open the PR.",
            )
        current_status = self._status_value(task)
        if current_status not in self._PR_OPEN_STATES:
            raise ValidationError(
                f"INVALID_STATE_FOR_PR: Task is '{current_status}'; PR "
                f"can only be opened during active dev states "
                f"({sorted(self._PR_OPEN_STATES)})."
            )
        return task

    async def _record_pr_atomically(
        self,
        task_uuid: UUID,
        pr_number: int,
        pr_url: str,
    ) -> None:
        """Task flags + work_session PR fields must commit together.

        If either write fails, roll back and raise a typed error — the
        GitHub PR exists on remote but local bookkeeping is inconsistent,
        and the caller must know.
        """
        task_service = get_task_service(self.session)
        work_session_service = get_work_session_service(self.session)
        try:
            task = await task_service.mark_pr_created(
                task_id=task_uuid, pr_number=pr_number, pr_url=pr_url
            )
            if task is None:
                raise ServiceError(
                    "PR_MARK_FAILED: mark_pr_created returned None. "
                    "The task may be in an invalid state for PR recording. "
                    "Check task status and retry."
                )
            if task.work_session_id:
                await work_session_service.create_pr(
                    require_uuid(task.work_session_id), pr_number, pr_url
                )
            await self.session.commit()
        except Exception as exc:
            await self.session.rollback()
            raise ServiceError(
                "PR_STATE_SYNC_FAILED: GitHub PR was created but the local "
                f"state sync failed ({type(exc).__name__}). The PR exists "
                "on GitHub but task/work_session fields are unchanged. "
                "Retry the PR creation to reconcile, or update manually."
            ) from exc

    async def create_pr_for_task(
        self,
        agent_id: UUID,
        data: GitCreatePRRequest,
    ) -> tuple[int, str, str, str, str]:
        """Preconditions + PR creation + state sync.

        When ``data.task_id`` is ``None`` the ownership/state gate and the
        post-creation task-state sync are both skipped — the GitHub PR is still
        created, but no task record is updated.

        Returns: (pr_number, pr_url, title, source_branch, target_branch)
        """
        if data.task_id is not None:
            await self._assert_pr_create_allowed(data.task_id, agent_id)

        workspace = await self.get_workspace(data.project_slug, agent_id)
        (
            pr_number,
            pr_url,
            title,
            source_branch,
            target_branch,
        ) = await self.create_pull_request(workspace, data)

        if data.task_id is not None:
            await self._record_pr_atomically(data.task_id, pr_number, pr_url)
        return pr_number, pr_url, title, source_branch, target_branch

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
            async with httpx.AsyncClient(timeout=_default_git_timeout()) as client:
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

    async def _first_allowed_merge_method(
        self,
        owner: str,
        repo: str,
        git_token: str,
        *,
        exclude: str | None = None,
    ) -> str | None:
        """Return a merge method the repo permits (squash > merge > rebase),
        skipping ``exclude``; ``None`` if the lookup fails.

        Recovers when a merge is refused with 405 because the repo disables that
        merge method in its settings.
        """
        try:
            async with httpx.AsyncClient(timeout=_default_git_timeout()) as client:
                resp = await client.get(
                    f"https://api.github.com/repos/{owner}/{repo}",
                    headers={
                        "Authorization": f"Bearer {git_token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
            if not resp.is_success:
                return None
            data = resp.json()
        except httpx.HTTPError:
            return None
        allowed = {
            "squash": data.get("allow_squash_merge", True),
            "merge": data.get("allow_merge_commit", True),
            "rebase": data.get("allow_rebase_merge", True),
        }
        for method in ("squash", "merge", "rebase"):
            if method != exclude and allowed.get(method):
                return method
        return None

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
        # A 405 means the repo disallows this merge method (e.g. "Squash merges
        # are not allowed on this repository" when that button is off). Fall back
        # to a method the repo permits and retry once, so a repo's merge-button
        # settings can't permanently wedge the PM on an open, mergeable PR.
        if resp.status_code == httpx.codes.METHOD_NOT_ALLOWED:
            fallback = await self._first_allowed_merge_method(
                owner, repo, git_token, exclude=merge_method
            )
            if fallback and fallback != merge_method:
                self.log.info(
                    "Merge method refused by repo; retrying with a permitted one",
                    requested=merge_method,
                    fallback=fallback,
                    owner=owner,
                    repo=repo,
                    pr=pr_number,
                )
                resp = await self._call_merge_api(
                    owner, repo, pr_number, git_token, fallback
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

    _PM_MERGE_ROLES: ClassVar[frozenset[AgentRole]] = frozenset(
        {AgentRole.CELL_PM, AgentRole.MAIN_PM}
    )

    def _assert_merge_role(self, current_status: str, agent_role: AgentRole) -> None:
        """PR approval chain — PM for PM-review, CEO for CEO-approval."""
        if current_status == TaskStatus.AWAITING_CEO_APPROVAL.value:
            if agent_role != AgentRole.CEO:
                raise UnauthorizedError(
                    action="merge_pr",
                    reason=(
                        "CEO_ONLY: Merging from awaiting_ceo_approval "
                        "requires the CEO role. PMs escalate; CEO merges "
                        "to master."
                    ),
                )
            return
        if current_status == TaskStatus.AWAITING_PM_REVIEW.value:
            if agent_role not in self._PM_MERGE_ROLES:
                raise UnauthorizedError(
                    action="merge_pr",
                    reason=(
                        "PM_ONLY: Merging from awaiting_pm_review requires "
                        "a PM role (cell_pm or main_pm)."
                    ),
                )
            return
        raise ValidationError(
            f"INVALID_STATE_FOR_MERGE: Task is '{current_status}'. Only "
            "awaiting_pm_review (PM merge) or awaiting_ceo_approval "
            "(CEO merge) can be merged."
        )

    async def _auto_complete_on_merge(
        self,
        task_uuid: UUID,
        agent_id: UUID,
        agent_role: AgentRole,
    ) -> None:
        """Auto-transition task after PR merge based on status + merger role.

        - awaiting_ceo_approval + CEO merger → ceo_approve (→ completed)
        - awaiting_pm_review + PM merger    → complete
        Otherwise leaves the task alone.
        """
        task_service = get_task_service(self.session)
        task = await task_service.get(task_uuid)
        if not task:
            return
        current_status = self._status_value(task)
        if (
            current_status == TaskStatus.AWAITING_CEO_APPROVAL.value
            and agent_role == AgentRole.CEO
        ):
            await task_service.ceo_approve(task_uuid)
        elif (
            current_status == TaskStatus.AWAITING_PM_REVIEW.value
            and agent_role in self._PM_MERGE_ROLES
        ):
            await task_service.complete(task_uuid, agent_id)

    async def merge_pr_for_task(
        self,
        agent_id: UUID,
        agent_role: AgentRole,
        data: GitMergePRRequest,
    ) -> tuple[str, str]:
        """Role-gated merge + work-session record + auto-complete.

        When ``data.task_id`` is ``None`` the task lookup, role gate,
        work-session update, and auto-complete are all skipped — the GitHub PR
        merge still happens using the caller-provided ``project_slug`` and
        ``pr_number``.

        Returns: (target_branch, merge_commit)
        """
        if data.task_id is not None:
            task_service = get_task_service(self.session)
            work_session_service = get_work_session_service(self.session)

            task = await task_service.get(data.task_id)
            if not task:
                raise NotFoundError(resource_type="Task", resource_id=str(data.task_id))
            self._assert_merge_role(self._status_value(task), agent_role)

            # A coordination root has no project of its own, so the CEO's merge
            # request can't carry a project_slug. Resolve the root's repo from
            # its product server-side; non-root tasks keep the client slug.
            project_slug = data.project_slug
            if task.project_id is None:
                root_project = await self._project_for_task(task)
                if root_project is not None:
                    project_slug = root_project.slug

            workspace = await self.get_workspace(project_slug, agent_id)
            target_branch, merge_commit = await self.merge_pull_request(
                workspace=workspace,
                pr_number=data.pr_number,
                merge_method=data.merge_method,
                project_slug=project_slug,
            )

            if task.work_session_id:
                await work_session_service.merge_pr(
                    require_uuid(task.work_session_id), agent_id
                )

            await self._auto_complete_on_merge(data.task_id, agent_id, agent_role)
            await self.session.commit()
        else:
            # No task context — proceed directly to the merge without
            # role/ownership checks or post-merge state transitions.
            project_slug = data.project_slug
            workspace = await self.get_workspace(project_slug, agent_id)
            target_branch, merge_commit = await self.merge_pull_request(
                workspace=workspace,
                pr_number=data.pr_number,
                merge_method=data.merge_method,
                project_slug=project_slug,
            )

        return target_branch, merge_commit

    # =========================================================================
    # GATEWAY (CHOREOGRAPHER) BACKFILL
    #
    # Branch-keyed entry points. The gateway holds branch_name + pr_number
    # but not project_slug or workspace path; these methods derive both
    # from the task that owns the branch.
    # =========================================================================

    async def _task_for_branch(self, branch_name: str) -> TaskTable | None:
        """Find the task whose branch_name matches `branch_name`."""
        from sqlalchemy import select

        from roboco.db.tables import TaskTable as _TaskTable

        result = await self.session.execute(
            select(_TaskTable).where(_TaskTable.branch_name == branch_name).limit(1)
        )
        return result.scalar_one_or_none()

    async def _project_for_task(self, task: Any) -> Any | None:
        """Resolve the Project whose repo a task's branch lives in.

        A normal task carries ``project_id``. A coordination root carries a
        product (cell->repo map) but no project of its own — its
        ``feature/main_pm/{root}`` integration branch lives in the product's
        repo(s). Fall through to the product's first distinct repo (monorepo =>
        the single repo) so root-level git ops (create_pr root->master, the CEO
        merge) resolve a workspace. Purely additive: a task WITH project_id
        resolves exactly as before.
        """
        project_service = get_project_service(self.session)
        if task.project_id is not None:
            return await project_service.get(UUID(str(task.project_id)))
        product_id = getattr(task, "product_id", None)
        if product_id is None:
            return None
        from roboco.services.product import get_product_service

        product_service = get_product_service(self.session)
        project_ids = await product_service.distinct_project_ids(UUID(str(product_id)))
        if not project_ids:
            return None
        return await project_service.get(project_ids[0])

    @staticmethod
    def _fast_gate_commands(project: Any) -> list[tuple[str, str]]:
        """The project's non-mutating fast-gate commands.

        A configured ``quality_command`` is the project's complete fast gate
        (lint + types + complexity, no tests; e.g. ``make gate``) and takes
        precedence — it runs alone. Otherwise fall back to the lint + typecheck
        pair. Format and the test suite are intentionally excluded: format
        mutates files, and the slow test run stays on CI.
        """
        quality = getattr(project, "quality_command", None)
        if quality:
            return [("quality", quality)]
        candidates = (
            ("lint", getattr(project, "lint_command", None)),
            ("typecheck", getattr(project, "typecheck_command", None)),
        )
        return [(name, cmd) for name, cmd in candidates if cmd]

    async def run_pre_submit_quality_gate(
        self, actor_agent_id: UUID, task: Any
    ) -> GateResult:
        """Run the project's fast quality gate in the developer's workspace.

        Resolves the task's project and the developer's workspace clone, then
        runs the configured lint + typecheck commands there. Returns a skipped
        pass when the project configures no fast-gate commands (so projects that
        opt out are never blocked). Raises only on workspace-resolution failure;
        the caller treats any such failure as fail-open.
        """
        project = await self._project_for_task(task)
        if project is None:
            return GateResult(passed=True, skipped=True)
        commands = self._fast_gate_commands(project)
        if not commands:
            return GateResult(passed=True, skipped=True)
        workspace = await self.get_workspace(project.slug, actor_agent_id)
        return await run_quality_commands(workspace, commands)

    async def toolchain_status_for_task(
        self, actor_agent_id: UUID, task: Any
    ) -> str | None:
        """The recorded toolchain status (``ok`` | ``broken`` | ``unknown``) for
        the acting agent's workspace clone of the task's project, or ``None``.

        ``None`` on any resolution/read failure — the caller fails open and
        never blocks a gate on an inability to read the marker.
        """
        try:
            project = await self._project_for_task(task)
            if project is None:
                return None
            workspace = await self.get_workspace(project.slug, actor_agent_id)
            _python, status = WorkspaceService.read_toolchain_status(workspace)
            return status
        except Exception:
            return None

    async def _project_slug_for_branch(self, branch_name: str) -> str | None:
        """Resolve project slug via the task that owns the branch."""
        task = await self._task_for_branch(branch_name)
        if task is None:
            return None
        project = await self._project_for_task(task)
        return project.slug if project else None

    @staticmethod
    def _resolve_workspace_agent_id(
        task: Any, actor_agent_id: UUID | None
    ) -> UUID | None:
        """Workspace-agent resolution priority.

        actor_agent_id → task.assigned_to → task.created_by → None.
        Centralised so push_branch/create_pr/commit/diff/pr_target/pr_merge
        share one chain — and individual methods stay below the
        cyclomatic-complexity gate (xenon B).
        """
        candidate = actor_agent_id or (
            UUID(str(task.assigned_to)) if task.assigned_to is not None else None
        )
        if candidate is None and task.created_by:
            candidate = UUID(str(task.created_by))
        return candidate

    async def _workspace_for_branch(
        self,
        branch_name: str,
        *,
        actor_agent_id: UUID | None = None,
    ) -> Path:
        """Get a workspace where this branch can be operated on.

        Resolves the workspace via ``_resolve_workspace_agent_id`` (the
        actor → assignee → creator fallback chain). Without it, post-
        handoff calls (e.g. pr_target on a task whose assigned_to was
        cleared by submit_qa) raise ValidationError when
        project.workspace_path is unset.
        """
        task = await self._task_for_branch(branch_name)
        if task is None:
            raise NotFoundError("Branch", branch_name)
        project = await self._project_for_task(task)
        if project is None:
            raise NotFoundError("Project", str(task.project_id))
        workspace_agent_id = self._resolve_workspace_agent_id(task, actor_agent_id)
        return await self.get_workspace(project.slug, agent_id=workspace_agent_id)

    async def _token_for_branch(self, branch_name: str) -> str | None:
        """Best-effort project PAT for authenticated fetch in the diff path.

        An unauthenticated ``git fetch`` fails on a private
        repo ("could not read Username for github.com"), so the diff base
        stays the stale clone-time ``origin/<default>`` and the three-dot
        diff spans the whole repo delta instead of the branch's change.
        Returns None on ANY resolution failure so the fetch degrades to
        unauthenticated (prior behaviour) rather than raising inside an
        evidence-assembly path — authentication is an optimisation here,
        never a hard dependency of producing a diff.
        """
        try:
            task = await self._task_for_branch(branch_name)
            if task is None:
                return None
            project = await self._project_for_task(task)
            if project is None:
                return None
            return await self._get_project_token_or_raise(project.slug)
        except Exception:
            return None

    async def checkout_branch_in_agent_workspace(
        self,
        branch_name: str,
        *,
        actor_agent_id: UUID,
    ) -> None:
        """Check out `branch_name` into the actor's own clone.

        Dev/PM workspaces land on the right branch because
        ``_auto_create_branch`` runs ``git checkout -b`` in the dev's
        clone at claim time. The documenter's clone is a *separate*
        workspace; when it claims an awaiting_documentation task the
        branch already exists (created by the dev) so no checkout ever
        ran in the doc's clone — it stayed on the default branch and
        ``roboco_docs_write`` / ``commit`` failed with BRANCH_MISMATCH.
        This puts the doc's workspace on the task branch (fetch +
        tracking-branch create). Best-effort: a checkout failure must
        not break the claim itself — the caller surfaces a remediation.
        """
        workspace = await self._workspace_for_branch(
            branch_name, actor_agent_id=actor_agent_id
        )
        await self.checkout(workspace, branch_name)

    async def push_branch(
        self,
        branch_name: str,
        *,
        actor_agent_id: UUID | None = None,
    ) -> tuple[str, int]:
        """Push `branch_name` to origin from the assignee's workspace.

        Gateway-only entry point — the legacy `push(workspace, force)`
        signature stays intact for non-gateway callers. Resolves the
        workspace from the task that owns the branch (with caller-actor
        fallback), then delegates. Returns
        (branch, commits_pushed).
        """
        workspace = await self._workspace_for_branch(
            branch_name, actor_agent_id=actor_agent_id
        )
        return await self.push(workspace)

    async def _ensure_base_on_remote(
        self,
        workspace: Path,
        base_branch: str,
        project_slug: str,
        git_token: str,
    ) -> str:
        """Ensure the PR base branch exists on origin; create it if missing.

        ``open_pr`` (via :meth:`create_pr`) targets an ancestor task's branch
        — e.g. the cell-PM integration branch — which may never have been
        pushed (a PM paused before its first push, or the workspace was
        wiped). GitHub then rejects the PR with 422 "base field invalid".
        Rather than fail, create the missing base on the remote off the
        default branch's tip so the PR has a valid base and the integration
        layering is preserved. Fall back to the default branch only if the
        create push itself fails.
        """
        default_branch = await self._project_default_branch(project_slug)
        if base_branch == default_branch:
            return base_branch
        ls = await self._run_git(
            workspace,
            ["ls-remote", "--heads", "origin", base_branch],
            check=False,
            token=git_token,
        )
        if ls.stdout.strip():
            return base_branch
        await self._run_git(
            workspace,
            ["fetch", "origin", default_branch],
            check=False,
            token=git_token,
        )
        push = await self._run_git(
            workspace,
            ["push", "origin", f"origin/{default_branch}:refs/heads/{base_branch}"],
            check=False,
            token=git_token,
        )
        if push.returncode != 0:
            self.log.warning(
                "could not create missing PR base on remote; retargeting to default",
                base_branch=base_branch,
                default_branch=default_branch,
                stderr=(push.stderr or "")[:200],
            )
            return default_branch
        self.log.info(
            "created missing PR base branch on remote off default",
            base_branch=base_branch,
            default_branch=default_branch,
        )
        return base_branch

    async def create_pr(
        self,
        branch_name: str,
        *,
        parent: str,
        is_root_pr: bool,
        actor_agent_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Open a PR for `branch_name` targeting `parent`.

        Returns: ``{"pr_number": int, "pr_url": str}``. Resolves the
        underlying project + task from the branch name; the gateway never
        passes a project_slug. The `parent` arg supersedes the task's
        natural parent so root PRs can target master.

        ``actor_agent_id`` lets PMs opening the master PR (where
        ``task.assigned_to`` may be None at completion time) resolve a
        workspace via the actor's clone.
        """
        task = await self._task_for_branch(branch_name)
        if task is None:
            raise NotFoundError("Branch", branch_name)
        # Resolve via _project_for_task so a coordination root (project_id null)
        # opening its root->master PR resolves the repo from its product.
        project = await self._project_for_task(task)
        if project is None:
            raise NotFoundError("Project", str(task.project_id))

        workspace = await self._workspace_for_branch(
            branch_name, actor_agent_id=actor_agent_id
        )
        git_token = await self._get_project_token_or_raise(project.slug)
        owner, repo = self._parse_github_remote(workspace)

        # `open_pr` targets an ancestor task's branch (e.g. the cell-PM
        # integration branch) that may not exist on origin — a PM paused
        # before pushing it, or the workspace was wiped. Create it on the
        # remote off the default branch so GitHub doesn't 422 'base invalid'
        # and the integration hierarchy is preserved.
        parent = await self._ensure_base_on_remote(
            workspace, parent, project.slug, git_token
        )

        pr_title = f"[{str(task.id)[:8]}] {task.title}"
        pr_body = task.description or ""

        resp = await self._post_pr(
            owner,
            repo,
            git_token,
            {
                "title": pr_title,
                "body": pr_body,
                "head": branch_name,
                "base": parent,
            },
        )

        if resp.status_code == _GH_UNPROCESSABLE and "already exists" in resp.text:
            found = await self._find_existing_pr(
                owner, repo, branch_name, parent, git_token
            )
            if found:
                pr_number = int(found["number"])
                pr_url = str(found["html_url"])
                await self._record_pr_atomically(UUID(str(task.id)), pr_number, pr_url)
                return {
                    "pr_number": pr_number,
                    "pr_url": pr_url,
                    "is_root_pr": is_root_pr,
                }

        if not resp.is_success:
            raise GitError(
                f"GitHub API refused PR creation ({resp.status_code}): "
                f"{resp.text[:200]}",
                {"owner": owner, "repo": repo, "head": branch_name},
            )

        pr_data = resp.json()
        pr_number = int(pr_data["number"])
        pr_url = str(pr_data["html_url"])
        await self._record_pr_atomically(UUID(str(task.id)), pr_number, pr_url)
        return {"pr_number": pr_number, "pr_url": pr_url, "is_root_pr": is_root_pr}

    async def _lock_parent_task_for_merge(self, parent_task_id: UUID | None) -> None:
        """SELECT FOR UPDATE on the parent task row, if any.

        Two PMs completing different subtasks of the same parent could
        race on the gh API merge call. Holding a row-level lock on the
        parent task serializes those merges at the DB layer — the second
        PM's transaction blocks until the first commits, by which time
        the first PR is already merged. The lock is auto-released when
        the surrounding transaction commits or rolls back.

        Root tasks (no parent) skip the lock — there's nothing to
        contend on at parent level, and master-bound merges are
        serialized by GitHub's PR-state machine alone.
        """
        if parent_task_id is None:
            return
        from sqlalchemy import select

        from roboco.db.tables import TaskTable as _TaskTable

        await self.session.execute(
            select(_TaskTable)
            .where(_TaskTable.id == parent_task_id)
            .with_for_update(of=_TaskTable)
        )

    @staticmethod
    def _resolve_merger_id(task: Any, actor_agent_id: UUID | None) -> UUID:
        """merged_by attribution priority for pr_merge.

        actor → assigned_to → created_by → UUID(int=0) sentinel.
        ``UUID(0)`` is the explicit "nothing was recoverable" marker
        instead of the silent NULL we used to write.
        """
        merger = (
            actor_agent_id
            or (UUID(str(task.assigned_to)) if task.assigned_to else None)
            or (UUID(str(task.created_by)) if task.created_by else None)
        )
        return merger or UUID(int=0)

    @dataclass(frozen=True)
    class _MergeContext:
        """Bundle of params for `_merge_with_retry` (keeps arg count under 5)."""

        owner: str
        repo: str
        pr_number: int
        git_token: str
        workspace: Path
        target: str

    async def _merge_with_retry(self, ctx: GitService._MergeContext) -> Any:
        """Single-retry merge: on 409 (race), sync target then retry once."""
        resp = await self._call_merge_api(
            ctx.owner, ctx.repo, ctx.pr_number, ctx.git_token, "squash"
        )
        if resp.status_code == _HTTP_CONFLICT:
            # Another PM merged a sibling subtask first and our local target
            # ref is stale. Refresh and retry once; a second 409 is a real
            # conflict the PM resolves manually.
            await self._sync_target_branch(ctx.workspace, ctx.target, ctx.git_token)
            resp = await self._call_merge_api(
                ctx.owner, ctx.repo, ctx.pr_number, ctx.git_token, "squash"
            )
        if not resp.is_success:
            # A merge refusal (typically 405 "not mergeable") means the branch
            # conflicts with the base — a sibling landed overlapping work first.
            # Raise the specific subclass so the completion path can rebase /
            # close-superseded / escalate instead of failing into a respawn loop.
            raise MergeConflictError(
                f"GitHub API refused PR merge ({resp.status_code}): {resp.text[:200]}",
                {"owner": ctx.owner, "repo": ctx.repo, "pr": ctx.pr_number},
            )
        return resp

    async def pr_merge(
        self,
        pr_number: int,
        *,
        target: str,
        actor_agent_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Merge PR `pr_number` into `target`.

        Returns: ``{"merge_commit_sha": str | None}``. Looks up the
        task/project that owns the PR to resolve workspace + token.

        Concurrency: takes a row-level lock on the parent task before
        invoking the GitHub merge API so that two PMs completing
        sibling subtasks of the same parent are serialized. On a 409
        merge conflict the local target branch is re-pulled and the
        merge is retried exactly once before giving up with `GitError`.
        """
        from sqlalchemy import select

        from roboco.db.tables import TaskTable as _TaskTable

        result = await self.session.execute(
            select(_TaskTable).where(_TaskTable.pr_number == pr_number).limit(1)
        )
        task = result.scalar_one_or_none()
        if task is None:
            raise NotFoundError("PR", str(pr_number))
        project = await self._project_for_task(task)
        if project is None:
            raise NotFoundError("Project for task", str(task.id))

        workspace_agent_id = self._resolve_workspace_agent_id(task, actor_agent_id)
        workspace = await self.get_workspace(project.slug, agent_id=workspace_agent_id)
        git_token = await self._get_project_token_or_raise(project.slug)
        owner, repo = self._parse_github_remote(workspace)

        # CEO is the only one who merges to master. This agent-facing merge path
        # (a cell PM merging a leaf/cell PR up the chain) may NEVER target a
        # repo's default branch — a root→master PR is merged solely by the CEO
        # via approve-&-merge (merge_pr_for_task, CEO-gated from
        # awaiting_ceo_approval). Agents open the master PR and escalate.
        default_branch = await self._project_default_branch(project.slug)
        if target == default_branch:
            raise UnauthorizedError(
                action="pr_merge",
                reason=(
                    "CEO_ONLY: merging into the default branch "
                    f"('{default_branch}') is reserved for the CEO via "
                    "approve-&-merge from awaiting_ceo_approval. Open the PR "
                    "and escalate; agents never merge to master."
                ),
            )

        parent_id = UUID(str(task.parent_task_id)) if task.parent_task_id else None
        await self._lock_parent_task_for_merge(parent_id)

        await self._merge_with_retry(
            self._MergeContext(
                owner=owner,
                repo=repo,
                pr_number=pr_number,
                git_token=git_token,
                workspace=workspace,
                target=target,
            )
        )
        await self._delete_pr_branch_best_effort(owner, repo, pr_number, git_token)
        merge_commit = await self._sync_target_branch(workspace, target, git_token)

        if task.work_session_id:
            ws_service = get_work_session_service(self.session)
            await ws_service.merge_pr(
                require_uuid(task.work_session_id),
                self._resolve_merger_id(task, actor_agent_id),
            )
        return {"merge_commit_sha": merge_commit or None}

    async def _get_pr_refs(
        self, owner: str, repo: str, pr_number: int, git_token: str
    ) -> tuple[str, str] | None:
        """Return ``(head_ref, base_ref)`` for a PR, or ``None`` if unavailable."""
        try:
            async with httpx.AsyncClient(timeout=_default_git_timeout()) as client:
                resp = await client.get(
                    f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}",
                    headers={
                        "Authorization": f"Bearer {git_token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
        except httpx.HTTPError:
            return None
        if not resp.is_success:
            return None
        data = resp.json()
        head = (data.get("head") or {}).get("ref")
        base = (data.get("base") or {}).get("ref")
        if not head or not base:
            return None
        return str(head), str(base)

    async def rebase_onto_base(
        self,
        workspace: Path,
        *,
        head_branch: str,
        base_branch: str,
        git_token: str,
    ) -> dict[str, Any]:
        """Rebase ``head_branch`` onto the latest ``base_branch`` from origin.

        This is the primitive both the sequence-ordered merge (rebase a later
        sibling onto the prior one's merged result) and the conflict resolver
        (rebase a wedged PR before re-merging) build on.

        Returns one of:
          - ``{"status": "superseded"}`` — the rebase was clean and the head
            has NO commits the base lacks; all its work is already in the base
            (e.g. a sibling that merged a superset landed first). Safe to close
            the PR + complete the task without a merge.
          - ``{"status": "rebased", "unique_commits": int}`` — clean rebase
            with unique work; the rebased branch was force-pushed to origin and
            can now merge cleanly.
          - ``{"status": "conflicts", "files": [...]}`` — the rebase hit
            conflicts and was aborted; a developer must resolve by hand.

        Never touches the base branch and only ever force-pushes
        ``head_branch`` (with ``--force-with-lease``). The caller must ensure
        ``base_branch`` is not a protected/default branch — agents never
        rebase-merge into master.
        """
        await self._run_git(workspace, ["fetch", "origin"], token=git_token)
        await self._run_git(workspace, ["checkout", head_branch])
        await self._run_git(workspace, ["reset", "--hard", f"origin/{head_branch}"])
        rebase = await self._run_git(
            workspace, ["rebase", f"origin/{base_branch}"], check=False
        )
        if rebase.returncode != 0:
            conflict = await self._run_git(
                workspace,
                ["diff", "--name-only", "--diff-filter=U"],
                check=False,
            )
            files = [f for f in conflict.stdout.splitlines() if f.strip()]
            await self._run_git(workspace, ["rebase", "--abort"], check=False)
            return {"status": "conflicts", "files": files}
        count = await self._run_git(
            workspace,
            ["rev-list", "--count", f"origin/{base_branch}..HEAD"],
        )
        unique = int(count.stdout.strip() or "0")
        if unique == 0:
            return {"status": "superseded"}
        await self._run_git(
            workspace,
            ["push", "--force-with-lease", "origin", f"HEAD:{head_branch}"],
            token=git_token,
        )
        return {"status": "rebased", "unique_commits": unique}

    async def rebase_pr_for_task(
        self,
        pr_number: int,
        *,
        actor_agent_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Resolve workspace/refs for a PR and rebase its branch onto the base.

        Thin wrapper over :meth:`rebase_onto_base` that loads the task/project
        that owns the PR (mirrors :meth:`pr_merge`) and reads the PR's head/base
        refs from GitHub. Returns the same classification dict, or
        ``{"status": "unknown"}`` when refs can't be resolved.
        """
        from sqlalchemy import select

        from roboco.db.tables import TaskTable as _TaskTable

        result = await self.session.execute(
            select(_TaskTable).where(_TaskTable.pr_number == pr_number).limit(1)
        )
        task = result.scalar_one_or_none()
        if task is None:
            raise NotFoundError("PR", str(pr_number))
        project = await self._project_for_task(task)
        if project is None:
            raise NotFoundError("Project for task", str(task.id))

        workspace_agent_id = self._resolve_workspace_agent_id(task, actor_agent_id)
        workspace = await self.get_workspace(project.slug, agent_id=workspace_agent_id)
        git_token = await self._get_project_token_or_raise(project.slug)
        owner, repo = self._parse_github_remote(workspace)

        refs = await self._get_pr_refs(owner, repo, pr_number, git_token)
        if refs is None:
            return {"status": "unknown"}
        head_branch, base_branch = refs
        return await self.rebase_onto_base(
            workspace,
            head_branch=head_branch,
            base_branch=base_branch,
            git_token=git_token,
        )

    async def close_pull_request(
        self,
        pr_number: int,
        *,
        comment: str | None = None,
        delete_branch: bool = True,
        actor_agent_id: UUID | None = None,
        project_id: UUID | None = None,
    ) -> None:
        """Close PR ``pr_number`` on GitHub, optionally with an explanatory comment.

        Used to retire a PR whose work is already in the base (superseded) so a
        wedged task can complete without a merge — the "close the dead PR"
        action agents had no verb for. Best-effort branch cleanup on close.

        ``pr_number`` alone is ambiguous across projects (GitHub numbers PRs
        per-repo), so when the caller knows which project the PR belongs to it
        MUST pass ``project_id`` — the task lookup is then scoped to it so a
        same-numbered PR in another project's repo is never resolved by
        accident. Idempotent: a PR that is already closed is a no-op (no
        duplicate comment), so a retried close-on-land never re-comments.
        """
        from sqlalchemy import select

        from roboco.db.tables import TaskTable as _TaskTable

        stmt = select(_TaskTable).where(_TaskTable.pr_number == pr_number)
        if project_id is not None:
            stmt = stmt.where(_TaskTable.project_id == project_id)
        result = await self.session.execute(stmt.limit(1))
        task = result.scalar_one_or_none()
        if task is None:
            raise NotFoundError("PR", str(pr_number))
        project = await self._project_for_task(task)
        if project is None:
            raise NotFoundError("Project for task", str(task.id))

        workspace_agent_id = self._resolve_workspace_agent_id(task, actor_agent_id)
        workspace = await self.get_workspace(project.slug, agent_id=workspace_agent_id)
        git_token = await self._get_project_token_or_raise(project.slug)
        owner, repo = self._parse_github_remote(workspace)

        headers = {
            "Authorization": f"Bearer {git_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        async with httpx.AsyncClient(timeout=_default_git_timeout()) as client:
            existing = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}",
                headers=headers,
            )
            already_closed = (
                existing.is_success and existing.json().get("state") == "closed"
            )
            if not already_closed:
                if comment:
                    await client.post(
                        f"https://api.github.com/repos/{owner}/{repo}/issues/"
                        f"{pr_number}/comments",
                        headers=headers,
                        json={"body": comment},
                    )
                resp = await client.patch(
                    f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}",
                    headers=headers,
                    json={"state": "closed"},
                )
                if not resp.is_success:
                    raise GitError(
                        f"GitHub API refused PR close ({resp.status_code}): "
                        f"{resp.text[:200]}",
                        {"owner": owner, "repo": repo, "pr": pr_number},
                    )
        if delete_branch:
            await self._delete_pr_branch_best_effort(owner, repo, pr_number, git_token)

    async def pr_target(
        self,
        pr_number: int,
        *,
        actor_agent_id: UUID | None = None,
    ) -> str:
        """Return the current target (base) branch of an open PR.

        Workspace resolution mirrors pr_merge: actor → assigned_to →
        created_by. Lets the Main PM call pr_target after
        ``submit_qa`` has cleared ``assigned_to`` without ValidationError.
        """
        from sqlalchemy import select

        from roboco.db.tables import TaskTable as _TaskTable

        result = await self.session.execute(
            select(_TaskTable).where(_TaskTable.pr_number == pr_number).limit(1)
        )
        task = result.scalar_one_or_none()
        if task is None:
            raise NotFoundError("PR", str(pr_number))
        project = await self._project_for_task(task)
        if project is None:
            raise NotFoundError("Project for task", str(task.id))

        workspace_agent_id = self._resolve_workspace_agent_id(task, actor_agent_id)
        workspace = await self.get_workspace(project.slug, agent_id=workspace_agent_id)
        owner, repo = self._parse_github_remote(workspace)
        git_token = await self._get_project_token_or_raise(project.slug)

        try:
            async with httpx.AsyncClient(timeout=_default_git_timeout()) as client:
                resp = await client.get(
                    f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}",
                    headers={
                        "Authorization": f"Bearer {git_token}",
                        "Accept": "application/vnd.github+json",
                    },
                )
        except httpx.HTTPError as e:
            raise GitError(
                f"GitHub API error fetching PR #{pr_number}: {e}",
                {"owner": owner, "repo": repo, "pr": pr_number},
            ) from e
        if not resp.is_success:
            raise GitError(
                f"GitHub API refused PR fetch ({resp.status_code}): {resp.text[:200]}",
                {"owner": owner, "repo": repo, "pr": pr_number},
            )
        base_ref = (resp.json().get("base") or {}).get("ref")
        if not base_ref:
            raise GitError(
                f"PR #{pr_number} has no base ref",
                {"owner": owner, "repo": repo, "pr": pr_number},
            )
        return str(base_ref)

    async def _ref_exists(self, workspace: Any, ref: str) -> bool:
        """True iff `ref` resolves in `workspace` (e.g. 'origin/<branch>')."""
        result = await self._run_git(
            workspace,
            ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
            check=False,
        )
        return result.returncode == 0

    async def _default_branch_ref(
        self, workspace: Any, token: str | None = None
    ) -> str:
        """Resolve the repo's default remote branch ref.

        Tries ``origin/HEAD`` (the canonical pointer), then common
        defaults. Always returns a usable ref string; falls back to
        ``origin/master`` so the diff command is still well-formed even
        on a misconfigured remote (an empty/garbage diff is recoverable;
        a malformed git invocation is not). This only resolves the ref
        NAME — the caller is responsible for fetching it fresh.
        """
        head = await self._run_git(
            workspace,
            ["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"],
            check=False,
        )
        target = head.stdout.strip()
        if head.returncode == 0 and target:
            # refs/remotes/origin/HEAD -> refs/remotes/origin/<name>
            return target.replace("refs/remotes/", "", 1)
        for candidate in ("origin/master", "origin/main"):
            await self._run_git(
                workspace,
                ["fetch", "origin", candidate.split("/", 1)[1]],
                check=False,
                token=token,
            )
            if await self._ref_exists(workspace, candidate):
                return candidate
        return "origin/master"

    async def _resolve_diff_base(
        self, workspace: Any, branch_name: str, token: str | None = None
    ) -> str:
        """Best diff base for `branch_name` when no explicit base is given.

        A leaf dev branch's ``parent_branch_for`` is the
        cell-PM branch (``feature/{team}/{root}--{cellpm}``) which is
        NEVER pushed — only devs push their own leaf branch. Diffing
        against a non-existent ``origin/<parent>`` returns an empty
        diff, so QA / docs see nothing. Fall back to the repo default
        branch when the computed parent ref is absent on origin.

        The default-branch ref in an inspecting clone is the
        stale clone-time tip (origin/HEAD is set, so _default_branch_ref
        early-returns its NAME without fetching). A three-dot diff against
        a stale base spans the whole repo delta, not the branch's change.
        Re-fetch the resolved base authenticated (unauth fails on private
        repos) so the base is current.
        """
        from roboco.services.gateway.merge_chain import parent_branch_for

        parent = parent_branch_for(branch_name)
        await self._run_git(
            workspace, ["fetch", "origin", parent], check=False, token=token
        )
        if await self._ref_exists(workspace, f"origin/{parent}"):
            return f"origin/{parent}"
        default = await self._default_branch_ref(workspace, token=token)
        short = default.split("/", 1)[1] if "/" in default else default
        await self._run_git(
            workspace, ["fetch", "origin", short], check=False, token=token
        )
        return default

    async def _resolve_head_ref(
        self, workspace: Any, branch_name: str, token: str | None = None
    ) -> str:
        """Ref for the branch tip that actually resolves in `workspace`.

        The local ``<branch_name>`` ref only exists in
        the clone where the dev ran ``git checkout -b`` at claim. QA /
        documenter / PM inspect from their OWN clones, which never had
        that local branch — a bare ``<branch_name>`` resolves
        ``refs/heads`` then ``refs/remotes/<name>`` but NEVER
        ``refs/remotes/origin/<name>``, so ``git diff base...<branch>``
        had an unresolvable head and returned an empty diff (QA saw no
        changes on a real PR). ``open_pr`` pushes the leaf branch, so
        ``origin/<branch>`` is the workspace-independent source of truth.
        Fetch it, then prefer the local branch (dev's own clone) and fall
        back to ``origin/<branch>``; last resort the bare name so the
        diff command stays well-formed.
        """
        await self._run_git(
            workspace, ["fetch", "origin", branch_name], check=False, token=token
        )
        if await self._ref_exists(workspace, branch_name):
            return branch_name
        if await self._ref_exists(workspace, f"origin/{branch_name}"):
            return f"origin/{branch_name}"
        return branch_name

    async def diff(
        self,
        *,
        branch_name: str,
        base: str | None = None,
        actor_agent_id: UUID | None = None,
    ) -> str:
        """Return the git diff for `branch_name` against `base`.

        When `base` is omitted, diffs against the branch's parent (per
        `parent_branch_for`), falling back to the repo default branch
        when that parent was never pushed. Content_actions
        evidence path can pass `HEAD~1` for an incremental diff.

        ``actor_agent_id`` resolves the workspace via the caller's clone
        when ``task.assigned_to`` is None — important for
        QA reviewing post-submit_qa.
        """
        workspace = await self._workspace_for_branch(
            branch_name, actor_agent_id=actor_agent_id
        )
        token = await self._token_for_branch(branch_name)
        head_ref = await self._resolve_head_ref(workspace, branch_name, token=token)
        base_ref = (
            base
            if base is not None
            else await self._resolve_diff_base(workspace, branch_name, token=token)
        )
        diff_result = await self._run_git(
            workspace, ["diff", f"{base_ref}...{head_ref}"], check=False
        )
        return diff_result.stdout

    async def list_changed_files(
        self,
        *,
        branch_name: str,
        base: str | None = None,
        actor_agent_id: UUID | None = None,
    ) -> list[str]:
        """Return the file paths changed on `branch_name` relative to `base`.

        Mirrors ``diff`` but invokes ``git diff --name-only`` so the
        gateway evidence path can populate ``files_changed`` from the
        authoritative git state — independent of whether the agent
        ever called the legacy ``add_files_modified`` HTTP endpoint
        (which the gateway commit() does not call). Empty paths are
        skipped; output preserves git's order. Same default-
        branch fallback as ``diff``.
        """
        workspace = await self._workspace_for_branch(
            branch_name, actor_agent_id=actor_agent_id
        )
        token = await self._token_for_branch(branch_name)
        head_ref = await self._resolve_head_ref(workspace, branch_name, token=token)
        base_ref = (
            base
            if base is not None
            else await self._resolve_diff_base(workspace, branch_name, token=token)
        )
        result = await self._run_git(
            workspace,
            ["diff", "--name-only", f"{base_ref}...{head_ref}"],
            check=False,
        )
        return [line for line in result.stdout.splitlines() if line.strip()]

    async def read_file_at_branch(
        self,
        *,
        branch_name: str,
        path: str,
        actor_agent_id: UUID | None = None,
    ) -> str | None:
        """Return the content of ``path`` as committed on ``branch_name``.

        Reads the file straight out of the branch tip with ``git show`` in the
        resolved workspace, so the orchestrator can capture a doc an agent
        authored (and committed) in its OWN clone — without any shared
        filesystem mount. A missing file, an uncommitted file, or a bad ref
        yields ``None`` rather than raising; callers treat this as best-effort.
        """
        workspace = await self._workspace_for_branch(
            branch_name, actor_agent_id=actor_agent_id
        )
        token = await self._token_for_branch(branch_name)
        head_ref = await self._resolve_head_ref(workspace, branch_name, token=token)
        result = await self._run_git(
            workspace, ["show", f"{head_ref}:{path}"], check=False
        )
        if result.returncode != 0:
            return None
        return result.stdout

    async def commit(
        self,
        *,
        branch_name: str,
        message: str,
        task_id: UUID,
        files: list[str] | None = None,
        actor_agent_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Gateway adapter — commit on `branch_name` with a free-form message.

        Resolves the workspace + project from the branch (same approach as
        `push_branch`/`create_pr`), stages the requested files (or all),
        and runs `git commit -m {message}`. Bypasses the conventional-commit
        template — content_actions has its own validator that asserts the
        message is descriptive, and the orchestrator-side git template only
        applies to the structured `commit_for_task` API path.

        ``actor_agent_id`` falls back through the same chain as pr_merge
        when ``task.assigned_to`` was cleared by an earlier transition.

        Returns a dict shaped for the gateway: ``{"sha": str, "message": str,
        "files_changed": int, "insertions": int, "deletions": int}``. Tests
        and downstream gateway code only consume `sha`; the rest is included
        so we don't have to invent a new shape later.
        """
        workspace = await self._workspace_for_branch(
            branch_name, actor_agent_id=actor_agent_id
        )
        await self._assert_on_task_branch(workspace, branch_name)

        # Stage files explicitly when provided; otherwise stage everything
        # the agent has touched. Mirrors create_commit's staging logic.
        # Staging + committing a large changeset (the Next.js panel runs into
        # the hundreds of files) can exceed the short default git timeout, so
        # these ops get the longer `git_commit_timeout_seconds` budget.
        commit_timeout = _commit_git_timeout()
        if files:
            for file in files:
                await self._run_git(workspace, ["add", file], timeout=commit_timeout)
        else:
            await self._run_git(workspace, ["add", "-A"], timeout=commit_timeout)

        # Free-form gateway commit — message is passed verbatim. The
        # gateway's commit_validator already rejected garbage messages
        # before we got here; we don't double-validate.
        await self._run_git(
            workspace, ["commit", "-m", message], timeout=commit_timeout
        )

        log_result = await self._run_git(workspace, ["log", "-1", "--format=%H|%s"])
        parts = log_result.stdout.strip().split("|", 1)
        commit_hash = parts[0] if parts else "unknown"
        full_message = parts[1] if len(parts) > 1 else message

        stat_result = await self._run_git(
            workspace, ["diff", "--stat", "HEAD~1..HEAD"], check=False
        )
        insertions, deletions, files_changed = self._parse_commit_stats(
            stat_result.stdout
        )

        # Best-effort link to the task; mirrors commit_for_task. A linking
        # failure must not lose the commit.
        task = await self._task_for_branch(branch_name)
        if task is not None and task.assigned_to is not None:
            await self._link_commit_to_task(
                task_id, commit_hash, message, UUID(str(task.assigned_to))
            )

        return {
            "sha": commit_hash,
            "message": full_message,
            "files_changed": files_changed,
            "insertions": insertions,
            "deletions": deletions,
        }

    async def conventions_check_for_task(
        self, actor_agent_id: UUID | None, task: Any
    ) -> dict[str, Any]:
        """Run the conventions validator on a task's changed files.

        Resolves the acting agent's workspace + the branch's changed files and
        runs ``python -m roboco.conventions`` over them. Fail-open on a
        resolution error (returns no findings, ``could_not_run=False``); the
        validator's OWN fail-loud (exit 3) sets ``could_not_run=True`` so the
        gate blocks rather than passing on an unanalyzable diff.
        """
        try:
            branch = task.branch_name
            if not branch:
                return {"findings": [], "could_not_run": False}
            workspace = await self._workspace_for_branch(
                branch, actor_agent_id=actor_agent_id
            )
            changed = await self.list_changed_files(
                branch_name=branch, actor_agent_id=actor_agent_id
            )
        except Exception:
            return {"findings": [], "could_not_run": False}
        if not changed:
            return {"findings": [], "could_not_run": False}
        return await self._run_conventions_validator(workspace, changed)

    async def _run_conventions_validator(
        self, workspace: Path, files: list[str]
    ) -> dict[str, Any]:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "roboco.conventions",
            "check",
            "--root",
            str(workspace),
            "--files",
            *files,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            reason = err.decode(errors="replace").strip() or "validator crashed"
            return {"findings": [], "could_not_run": True, "reason": reason[:300]}
        findings: list[dict[str, Any]] = []
        for line in out.decode(errors="replace").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                findings.append(json.loads(stripped))
            except json.JSONDecodeError:
                continue
        return {"findings": findings, "could_not_run": False, "reason": None}

    async def open_conventions_pr(
        self,
        project_slug: str,
        *,
        content: str,
        title: str,
        body: str,
        workspace: Path | None = None,
    ) -> dict[str, Any] | None:
        """Commit ``.roboco/conventions.yml`` on the scaffold branch + open a PR.

        Best-effort and project-level (no task): writes ``content`` on a fresh
        ``CONVENTIONS_SCAFFOLD_BRANCH`` cut from the default branch in
        ``workspace`` (an agent's fresh clone) or the project's configured
        ``workspace_path``, commits it (always), then pushes + opens a PR (only
        with a git token + remote), and restores the clone to its original
        branch so a shared workspace is never stranded on the scaffold branch.
        Returns ``{"branch", "pr_number", "pr_url"}`` (``pr_number=None`` when no
        remote PR was opened) or ``None`` when there is no usable workspace.
        """
        project_service = get_project_service(self.session)
        project = await project_service.get_by_slug(project_slug)
        if project is None:
            return None
        ws = workspace or (
            Path(project.workspace_path) if project.workspace_path else None
        )
        if ws is None or not ws.exists():
            return None
        base = project.default_branch or "master"
        spec = _ConventionsPr(
            content=content,
            branch=CONVENTIONS_SCAFFOLD_BRANCH,
            title=title,
            body=body,
        )
        original = await self.get_current_branch(ws)
        try:
            await self._commit_conventions_file(ws, base, spec)
            return await self._push_and_open_conventions_pr(
                project_slug, ws, base, spec
            )
        finally:
            await self._run_git(ws, ["checkout", original], check=False)

    async def _commit_conventions_file(
        self, workspace: Path, base: str, spec: _ConventionsPr
    ) -> None:
        await self._run_git(workspace, ["checkout", base], check=False)
        await self._run_git(workspace, ["checkout", "-B", spec.branch])
        target = workspace / ".roboco" / "conventions.yml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(spec.content)
        await self._run_git(workspace, ["add", ".roboco/conventions.yml"])
        await self._run_git(workspace, ["commit", "-m", spec.title])

    async def _push_and_open_conventions_pr(
        self, project_slug: str, workspace: Path, base: str, spec: _ConventionsPr
    ) -> dict[str, Any]:
        unopened: dict[str, Any] = {
            "branch": spec.branch,
            "pr_number": None,
            "pr_url": None,
        }
        token = await self._token_for_project(project_slug)
        if not token:
            return unopened
        try:
            await self.push(workspace)
            owner, repo = self._parse_github_remote(workspace)
            resp = await self._post_pr(
                owner,
                repo,
                token,
                {
                    "title": spec.title,
                    "head": spec.branch,
                    "base": base,
                    "body": spec.body,
                },
            )
        except GitError:
            return unopened
        if not resp.is_success:
            return unopened
        data = resp.json()
        return {
            "branch": spec.branch,
            "pr_number": data.get("number"),
            "pr_url": data.get("html_url"),
        }


CONVENTIONS_SCAFFOLD_BRANCH = "chore/roboco-conventions-scaffold"


@dataclass(frozen=True)
class _ConventionsPr:
    """The fields for a project-level conventions scaffold/restore PR."""

    content: str
    branch: str
    title: str
    body: str


def get_git_service(session: AsyncSession) -> GitService:
    """Factory function to get git service."""
    return GitService(session)
