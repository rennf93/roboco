"""
Git Service

Handles git operations for agents working on code tasks.
All business logic for git commands, commit templates, PR generation.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
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
    from collections.abc import Coroutine

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
from roboco.foundation.policy import lifecycle
from roboco.foundation.policy.pr_labels import CONVENTIONS_PR_LABELS, derive_pr_labels
from roboco.models.base import AgentRole, TaskStatus
from roboco.models.env_branches import effective_environments, head_branch
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


async def _await_shielded[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run a session write shielded from cancellation, waiting it out.

    A bare ``asyncio.shield(coro)`` detaches the write on cancellation but
    lets the CancelledError propagate immediately — the still-running write
    then races ``get_db``'s rollback on the SAME AsyncSession and asyncpg
    raises ``InterfaceError: another operation is in progress`` (a 500
    instead of the middleware's clean 504). On cancellation, await the
    in-flight write to completion BEFORE re-raising, so the session is quiet
    by the time the rollback runs. Mirrors
    ``VideoPostService._commit_shielded``.
    """
    task = asyncio.ensure_future(coro)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)
        raise


def _completed_branchful_children(children: list[Any]) -> list[Any]:
    """Children whose completed work can be integrity-checked: completed,
    branch-bearing, with recorded commits."""
    return [
        c
        for c in children
        if str(getattr(c, "status", "")) in ("completed", "TaskStatus.COMPLETED")
        and getattr(c, "branch_name", None)
        and getattr(c, "commits", None)
    ]


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


def resolve_git_dir(workspace: Path) -> Path | None:
    """Resolve the real ``.git`` directory for a workspace or linked worktree.

    A normal clone's ``.git`` is a directory. A linked worktree's ``.git`` is a
    *file* containing ``gitdir: <path>`` pointing into the clone root's
    ``.git/worktrees/<id>/``. Callers that rglob locks / parse config must go
    through here, not assume ``workspace / ".git"`` is a directory.

    Returns the resolved git dir, or None if the workspace has no git metadata.
    """
    dot_git = workspace / ".git"
    if dot_git.is_dir():
        return dot_git
    if dot_git.is_file():
        try:
            first = dot_git.read_text().splitlines()[0].strip()
        except (OSError, IndexError):
            return None
        if not first.startswith("gitdir: "):
            return None
        target = Path(first[len("gitdir: ") :].strip())
        if not target.is_absolute():
            target = (workspace / target).resolve()
        return target if target.is_dir() else None
    return None


def _remove_stale_git_locks(workspace: Path) -> None:
    """Best-effort removal of orphaned ``.git/**/*.lock`` files.

    F019: a git mutation op (commit / merge --ff-only / rebase / reset --hard /
    add) killed by ``_run_git``'s timeout (SIGKILL) can orphan lock files —
    ``index.lock`` especially — that wedge the workspace for every subsequent
    op (incl. the next fresh-claim ``reset --hard``) with
    "Another git process seems to be running in this repository". By the time
    the timeout fires the git process is dead, so its orphaned locks are safe
    to remove. Best-effort: any error (no .git, race with a real process) is
    swallowed — this only ever *helps*, never blocks.

    Worktree-aware (F123): a linked worktree's ``.git`` is a gitdir pointer —
    route through ``resolve_git_dir`` so locks inside ``.git/worktrees/<id>/``
    are reached.
    """
    git_dir = resolve_git_dir(workspace)
    if git_dir is None or not git_dir.is_dir():
        return
    try:
        for lock in git_dir.rglob("*.lock"):
            # A lock a real process just grabbed, or a permission issue —
            # leave it. The TTL/next-op path is the backstop.
            with contextlib.suppress(OSError):
                lock.unlink()
    except OSError:
        return


# Verbs that never write anything — `_run_git`'s post-op ownership repair is
# pure waste after these (live NAS: a "rev-parse --verify" cost 5165ms of
# chown for a command that touches nothing). Read-only forms of `branch` /
# `symbolic-ref` are handled separately below since they share a verb name
# with a mutating form.
_READ_ONLY_GIT_VERBS = frozenset(
    {
        "status",
        "log",
        "diff",
        "rev-parse",
        "rev-list",
        "ls-remote",
        "merge-base",
        "show",
        "cherry",
    }
)

# Verbs that write only inside `.git/` (refs, objects, index) — never the
# working tree. Ownership repair can be scoped to `.git/` alone instead of a
# full-workspace walk.
_GIT_SCOPED_VERBS = frozenset({"add", "commit", "fetch", "push"})


def _branch_or_symbolic_ref_scope(verb: str, rest: list[str]) -> str:
    """Query vs SET form of `branch` / `symbolic-ref` (same verb, different
    scope): query reads only, SET writes a ref under `.git/`.

    Query: `branch --show-current` (0 positional) or `symbolic-ref [-q]
    <name>` (1 positional — naming which ref to read). SET (`branch <name>
    <start>`, `symbolic-ref <name> <ref>`) always carries 2+ positional args.
    """
    positional = [a for a in rest if not a.startswith("-")]
    is_query = (verb == "branch" and not positional) or (
        verb == "symbolic-ref" and len(positional) <= 1
    )
    return "none" if is_query else "git"


def _git_ownership_scope(args: list[str]) -> str:
    """Classify a git invocation's post-op ownership-repair scope.

    Root cause of the chown cost: the orchestrator's git subprocess runs as
    root, so a MUTATING op leaves root-owned files under `.git/` (and, for
    checkout/reset/rebase/pull, the working tree) that the agent container
    (uid 1000) can't write. A read-only op never writes anything, so
    repairing ownership after one is pure waste — on the NAS this cost
    5-10s PER git call, and a single `i_am_done` chains ~a dozen ops.

    Returns "none" (skip repair — zero syscalls), "git" (repair `.git/`
    only, worktree-aware via `_resolve_clone_root`), or "full" (repair the
    whole workspace — unchanged behavior, and the safe default for
    checkout/reset/rebase/pull/stash or any verb this classifier doesn't
    recognize, so an unclassified op is never under-repaired). `stash`
    (rebase_onto_base's `stash=True` path, #337 scoping) writes both the
    working tree (push/pop) and `.git/` (the stash ref + objects) — it is
    deliberately left unclassified so it falls to the safe "full" default
    rather than a `.git/`-only repair that would strand agent-owned files.
    """
    if not args:
        return "full"
    verb = args[0]
    if verb in _READ_ONLY_GIT_VERBS:
        return "none"
    if verb in ("branch", "symbolic-ref"):
        return _branch_or_symbolic_ref_scope(verb, args[1:])
    if verb in _GIT_SCOPED_VERBS:
        return "git"
    return "full"


# `_get_gh_env` and the gh-CLI code paths were removed in favor of direct
# GitHub REST API calls — no CLI dependency, and the PAT no longer touches
# subprocess argv / environ.

# Expected number of parts in various git outputs
_REV_LIST_PARTS = 2

# GitHub REST API status codes
_GH_UNPROCESSABLE = 422
# merges-API success codes: 201 = merge commit created + pushed; 204 = nothing
# to merge (head already an ancestor of base).
_HTTP_CREATED = 201
_HTTP_NO_CONTENT = 204
# 404 means the PR (or repo) does not exist; surfaced as a typed GitError
# by `update_pr_for_task` so the gateway can convert it into a specific
# invalid_state envelope rather than the generic refusal message.
_HTTP_NOT_FOUND = 404
# 409 means the PR can't be merged in its current state — typically because
# a concurrent sibling-subtask merge updated the target branch and our local
# refs are stale. `pr_merge` re-syncs and retries exactly once on this code.
_HTTP_CONFLICT = 409
# GitHub returns 405 when the repo's settings disallow the requested merge
# method (e.g. "Squash merges are not allowed on this repository" with the
# squash button off) — distinct from a 405 on an already-merged PR. The agent
# `_merge_with_retry` falls back to a permitted method on this code, mirroring
# the CEO `merge_pull_request` path.
_HTTP_METHOD_NOT_ALLOWED = 405

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
# Cap a conventions-validator run so a hung subprocess (tree-sitter deadlock,
# huge repo) can't hang the i_am_done/pr_pass gate forever.
_CONVENTIONS_VALIDATOR_TIMEOUT_SECONDS = 120
# --- pr_pass CI-status guard ------------------------------------------------
# GitHub check-run conclusions that count as a failing check on a PR's head
# commit. ``neutral``/``skipped``/``success`` (and ``None`` on a still-running
# run) are not failing.
_FAILING_CHECK_CONCLUSIONS = frozenset(
    {"failure", "cancelled", "timed_out", "action_required"}
)
# A 404 resolving a PR's head SHA means the repo/PR is unreachable or doesn't
# exist — classified as no_ci_configured, distinct from any other non-2xx
# (a genuine API failure on a real, reachable repo) which is `error`.
_HTTP_NOT_FOUND = 404


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


def _api_base() -> str:
    """GitHub REST base URL — honors ``settings.github_api_base_url``.

    Five call sites already read the setting (CI runs, open-PR list); the
    PR create/merge/branch sites hardcoded the public host, which broke any
    GitHub Enterprise or test override. One helper keeps them uniform.
    """
    return settings.github_api_base_url.rstrip("/")


@dataclass(frozen=True)
class _CiRunQuery:
    """Bundle of per-project inputs to a CI-run fetch (owner/repo, branch, token,
    slug for logging) so ``_fetch_latest_ci_run`` stays under the arg-count gate —
    owner_repo alone was already bundled for the same reason."""

    project_slug: str
    owner_repo: tuple[str, str]
    branch: str
    git_token: str


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
        agent user — SCOPED to what this op could actually have written
        (see `_git_ownership_scope`). Git commands here run as root and
        create root-owned files under .git/ (refs, logs/refs, packed-refs,
        index, objects). If we don't re-chown, the agent container (uid
        1000) can't append to those files on its next commit and fails
        with "unable to append to .git/logs/refs/heads/...". A read-only
        op (status, log, diff, ...) never writes, so it skips the repair
        entirely.
        """
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
            # Timed-out git process was SIGKILL'd mid-mutation and may orphan
            # .git/*.lock files; clear them so the workspace isn't wedged.
            await loop.run_in_executor(
                _GIT_EXECUTOR, _remove_stale_git_locks, workspace
            )
            raise GitTimeoutError(" ".join(args), effective_timeout) from e
        except subprocess.CalledProcessError as e:
            raise GitCommandError(
                " ".join(args), e.stderr or e.stdout or "Unknown error"
            ) from e
        git_ms = (time.monotonic() - t0) * 1000.0
        chown_ms = await self._reown_after_git_op(loop, workspace, args)

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

    @staticmethod
    async def _reown_after_git_op(
        loop: asyncio.AbstractEventLoop, workspace: Path, args: list[str]
    ) -> float:
        """Run the scope-appropriate post-op ownership repair; return its ms cost.

        Extracted out of `_run_git` so the classify-then-dispatch logic stays
        out of that method's cyclomatic budget. Runs in the dedicated git
        executor so it doesn't compete with the event loop's default pool.
        A "none"-scope op costs zero syscalls and returns 0.0 so the slow-op
        instrumentation still sees the true (near-zero) cost.
        """
        from roboco.services.workspace import _ensure_agent_owned, _ensure_git_dir_owned

        scope = _git_ownership_scope(args)
        if scope == "none":
            return 0.0
        repair = _ensure_git_dir_owned if scope == "git" else _ensure_agent_owned
        t1 = time.monotonic()
        await loop.run_in_executor(_GIT_EXECUTOR, repair, workspace)
        return (time.monotonic() - t1) * 1000.0

    async def _token_for_project(self, project_slug: str) -> str | None:
        """Decrypted project token for orchestrator-side remote git ops.

        A decryption failure (an encryption-key rotation left the stored PAT
        encrypted with the old key) is logged loudly with the project slug
        before returning None — without this every best-effort workspace git
        op (push, PR, clone-with-token) silently looks like 'this project has
        no token', indistinguishable from a project that genuinely never set
        one, and the operator can't tell which project a key rotation wedged.
        The best-effort skip behavior is preserved (callers still get None and
        skip the remote op); this only makes the cause diagnosable.
        """
        from roboco.utils.crypto import EncryptionError

        project_service = get_project_service(self.session)
        try:
            return await project_service.get_decrypted_token_by_slug(project_slug)
        except EncryptionError as exc:
            self.log.error(
                "git token decryption failed — encryption key may have rotated;"
                " treating as no-token for this project's remote git ops",
                project_slug=project_slug,
                error=str(exc),
            )
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
                    default_branch=head_branch(project),
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

    async def branch_exists_on_remote(
        self,
        project_slug: str,
        branch_name: str,
        agent_id: UUID | None = None,
    ) -> bool | None:
        """Probe whether ``branch_name`` exists on the project's ``origin``.

        Returns True when the ref is present, False when confirmed absent, and
        None when the probe itself errored (network blip, missing workspace or
        token) — callers fail-soft on None so a transient glitch can't fail a
        normal claim. Mirrors the ``ls-remote --heads origin <branch>`` idiom
        in :meth:`create_branch`'s parent-branch check, reusing the same
        workspace + decrypted-token resolution so the probe is authoritative.
        """
        try:
            workspace = await self.get_workspace(project_slug, agent_id)
            token = await self._token_for_project(project_slug)
            result = await self._run_git(
                workspace,
                ["ls-remote", "--heads", "origin", branch_name],
                check=False,
                token=token,
                timeout=_network_git_timeout(),
            )
        except Exception as exc:
            self.log.warning(
                "branch_exists_on_remote probe failed; failing soft",
                project_slug=project_slug,
                branch_name=branch_name,
                error=str(exc),
            )
            return None
        return bool(result.stdout.strip())

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

    def _get_primary_session_id(self, _task: TaskTable | None) -> str | None:
        """Discussion sessions are retired; always None.

        ``CommitContext.session_id`` is Optional and the commit template
        renders the trailer only ``if set``, so None is safe.
        """
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

    @staticmethod
    def _worktree_for_task(clone_root: Path, task_id: UUID) -> Path:
        """Per-task worktree path under a clone root (F123).

        Matches ``create_branch``'s ``{clone_root}/.worktrees/{task_id[:8]}``
        layout so commit/checkout/rebase paths resolve the same worktree the
        claim cut and the spawn cwd pointed the agent at.
        """
        return clone_root / ".worktrees" / str(task_id)[:8]

    async def _ensure_worktree_for_commit(
        self, clone_root: Path, worktree: Path, branch: str | None
    ) -> None:
        """Ensure a task's worktree is attached before a cwd-dependent git op.

        Resume re-adds a pruned worktree, but a worktree can also be evicted
        mid-task (disk pressure, manual cleanup); a commit/checkout against a
        missing dir fails opaquely. Idempotent — no-op when the worktree is
        present, re-adds (no ``-b``) from the surviving branch ref when pruned.
        """
        if not branch:
            return
        workspace_service = get_workspace_service(self.session)
        await workspace_service.ensure_worktree_for_resume(clone_root, worktree, branch)

    async def _assert_on_task_branch(
        self, workspace: Path, task_branch: str | None
    ) -> None:
        """Ensure the workspace is on the task's branch, recovering a resumed
        clone that drifted — instead of hard-failing.

        A dev/documenter/QA clone is shared across tasks; on a respawn/resume it
        can sit on a sibling task's branch, or a re-provisioned clone can lack
        the task branch as a local ref (commits only on origin). The
        fresh-claim path git-reset-hards the clone, but resume short-circuits
        before it — so the agent's next commit hit BRANCH_MISMATCH and the task
        wedged in a blocked respawn loop (the documented resume deadlock). This
        now fetches + checks out the task branch (recreating a missing local ref
        from origin) and only raises if it genuinely cannot switch (uncommitted
        changes block it). It NEVER discards local work — checkout, not reset, so
        a resumed agent's unpushed commits are preserved.
        """
        if not task_branch:
            return
        current_branch = await self.get_current_branch(workspace)
        if not current_branch or current_branch == task_branch:
            return
        # Resumed/re-provisioned clone parked on the wrong branch — try to
        # recover onto the task branch before rejecting.
        token = await self._token_for_workspace(workspace)
        local = await self._run_git(
            workspace,
            ["rev-parse", "--verify", "--quiet", f"refs/heads/{task_branch}"],
            check=False,
        )
        if local.returncode != 0:
            # Local ref absent (re-provisioned clone) — recover it from origin.
            await self._run_git(
                workspace,
                ["fetch", "origin", task_branch],
                token=token,
                check=False,
                timeout=_network_git_timeout(),
            )
            await self._run_git(
                workspace,
                ["branch", task_branch, f"origin/{task_branch}"],
                check=False,
            )
        switched = await self._run_git(
            workspace, ["checkout", task_branch], check=False
        )
        if (
            switched.returncode == 0
            and await self.get_current_branch(workspace) == task_branch
        ):
            self.log.info(
                "recovered workspace onto task branch on resume",
                task_branch=task_branch,
                from_branch=current_branch,
            )
            return
        raise ValidationError(
            f"BRANCH_MISMATCH: Workspace is on '{current_branch}' but task "
            f"requires '{task_branch}', and it could not be switched "
            f"automatically (uncommitted changes likely block the switch). "
            f"Re-call your role's claim verb on this task "
            f"(`i_will_work_on` / `i_will_plan` / `claim_doc_task` / "
            f"`claim_review`); if it persists, unclaim and re-claim to rebuild "
            f"the clone, then replay your commits."
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
            await self.session.flush()
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
            clone_root = await self.get_workspace(data.project_slug, agent_id)
            # Commit inside the task's per-task worktree (F123), not the shared
            # clone — the clone's HEAD may be parked on the default branch.
            worktree = self._worktree_for_task(clone_root, data.task_id)
            await self._ensure_worktree_for_commit(
                clone_root, worktree, task.branch_name
            )
            workspace = worktree
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
        """Return the project's head environment branch (ladder index 0).

        This is where dev/cell/leaf PRs target — the dev trunk. Falls back to
        default_branch (and then 'master') via the env-ladder shim when the
        project has no declared environment ladder.
        """
        project_service = get_project_service(self.session)
        project = await project_service.get_by_slug(project_slug)
        if project is None:
            return "master"
        return head_branch(project)

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

        # --- F123: per-task worktree, not a shared-clone checkout. ---
        # The dev clone is one persistent checkout shared across this dev's
        # tasks, and a coordinator PM may hold several in_progress roots at
        # once. The old `reset --hard` + `checkout -b` on the shared clone
        # clobbered a still-active sibling root's working tree (live on NAS:
        # main-pm ping-ponged two roots on one clone). Each task now gets its
        # own linked worktree under {clone_root}/.worktrees/{task-short}/ via
        # `git worktree add`; the clone's HEAD is never moved by a claim, so
        # sibling roots' trees are isolated. This runs only on a FRESH claim
        # (resume short-circuits in _dev_reentry before reaching here).
        worktree_path = workspace / ".worktrees" / str(task_id)[:8]

        # Branch from the fetched remote tip (matches the old
        # `merge --ff-only origin/<base>` intent — build on the latest remote
        # base, not a stale local checkout). Fall back to origin/<default> if
        # <base> isn't on the remote yet (the ls-remote above already retargets
        # base_branch to default in that case; this covers a residual miss).
        base_ref = f"origin/{base_branch}"
        ref_check = await self._run_git(
            workspace, ["rev-parse", "--verify", "--quiet", base_ref], check=False
        )
        if ref_check.returncode != 0:
            base_ref = f"origin/{default_branch}"
            base_branch = default_branch

        # ensure_worktree: `git worktree add -b <branch> <base>` for a new
        # branch, or `worktree add <branch>` (reuse) for an existing on-disk
        # branch (a prior attempt that rolled back DB fields but left the
        # branch). Idempotent on an already-present worktree (re-claim).
        workspace_service = get_workspace_service(self.session)
        await workspace_service.ensure_worktree(
            workspace, worktree_path, branch_name, base_ref
        )

        # An existing branch with no commits of its own — a dependency-blocked
        # task re-claimed after its upstream merged — is re-pointed at the fresh
        # base so the agent builds on the current tip. Runs on the WORKTREE,
        # never the shared clone. A freshly `-b`'d branch is already at base, so
        # this is a no-op for new branches; a branch carrying real work
        # (unique > 0) is left exactly as-is.
        unique = await self._run_git(
            workspace,
            ["rev-list", "--count", f"{base_ref}..{branch_name}"],
            check=False,
        )
        if unique.returncode == 0 and unique.stdout.strip() == "0":
            await self._run_git(
                worktree_path, ["reset", "--hard", base_ref], check=False
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

    async def merge_dependency_lineage(
        self,
        workspace: Path,
        task_id: UUID,
        branch_name: str,
        source_branch: str,
        project_slug: str,
    ) -> dict[str, Any]:
        """Backfill a freshly cut branch with a dependency's merged content.

        The claim-time dependency gate only holds a task until every
        ``dependency_ids`` entry is terminal (TIMING) — it never checks
        whether that entry's merged work is actually reachable from this
        branch's base (CONTENT). A cross-subtree/cross-cell dependency edge
        can complete on a branch this one never descends from (e.g. a UX
        cell task merges into the UX cell branch; a sibling frontend cell
        task cut from the frontend cell branch has no ancestry into it until
        the UX cell itself submits up).

        No-op when ``source_branch`` is already an ancestor of
        ``branch_name`` (the common, transitively-safe case: same-parent
        siblings, same-project root waves — master/the shared ancestor
        already carries the work). On a real conflict the merge is aborted
        and the branch is left exactly at its cut point: this is a
        claim-time content assist, never a gate, so it always returns a
        status rather than raising.

        Returns ``{"status": ...}``, one of ``already_ancestor`` /
        ``missing_ref`` / ``merged`` / ``merged_push_failed`` / ``conflict``
        (the last carries ``"files"``).
        """
        token = await self._token_for_project(project_slug)
        await self._run_git(
            workspace,
            ["fetch", "origin", source_branch],
            check=False,
            token=token,
            timeout=_network_git_timeout(),
        )
        origin_ref = f"origin/{source_branch}"
        if not await self._ref_exists(workspace, origin_ref):
            return {"status": "missing_ref"}

        worktree = self._worktree_for_task(workspace, task_id)
        await self._ensure_worktree_for_commit(workspace, worktree, branch_name)

        ancestor = await self._run_git(
            worktree,
            ["merge-base", "--is-ancestor", origin_ref, branch_name],
            check=False,
        )
        if ancestor.returncode == 0:
            return {"status": "already_ancestor"}

        merge = await self._run_git(
            worktree, ["merge", "--no-edit", origin_ref], check=False
        )
        if merge.returncode != 0:
            return await self._abort_lineage_merge_conflict(worktree)

        push = await self._run_git(
            worktree,
            ["push", "origin", branch_name],
            check=False,
            token=token,
            timeout=_network_git_timeout(),
        )
        return {"status": "merged" if push.returncode == 0 else "merged_push_failed"}

    async def _abort_lineage_merge_conflict(self, worktree: Path) -> dict[str, Any]:
        """Collect conflicted files and abort a failed dependency-lineage merge."""
        conflict = await self._run_git(
            worktree, ["diff", "--name-only", "--diff-filter=U"], check=False
        )
        files = [f for f in conflict.stdout.splitlines() if f.strip()]
        await self._run_git(worktree, ["merge", "--abort"], check=False)
        return {"status": "conflict", "files": files}

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
        if project:
            allowed.add(head_branch(project))

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

    async def _ensure_pushable_branch(
        self, workspace: Path, branch: str, token: str | None
    ) -> None:
        """Make sure ``branch`` exists as a local ref before a push-by-name.

        Push-by-name (``git push origin <branch>``) decouples the push from the
        workspace checkout, but it still requires the named ref to exist LOCALLY.
        A dev's clone is shared across tasks and may be freshly re-provisioned
        (the per-task workspace-collision recovery re-clones it), so by push time
        the task branch can be ABSENT as a local ref even though its commits are
        already safe on origin — the workspace is parked on a different task's
        branch. ``git push`` then dies with the cryptic ``src refspec <branch>
        does not match any`` and the task wedges in a blocked respawn loop.

        Recover idempotently: if the local ref is missing, fetch ``origin
        <branch>`` and recreate the local tracking ref so the subsequent
        push-by-name is a clean no-op. If origin has no such branch either, the
        commits are genuinely not in this clone — fail loud with a recoverable
        instruction instead of the raw refspec error.
        """
        local = await self._run_git(
            workspace,
            ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
            check=False,
        )
        if local.returncode == 0:
            return
        # Local ref missing — try to recover it from origin (commits often
        # already pushed in a prior cycle / clone).
        await self._run_git(
            workspace,
            ["fetch", "origin", branch],
            token=token,
            check=False,
            timeout=_network_git_timeout(),
        )
        remote = await self._run_git(
            workspace,
            ["rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}"],
            check=False,
        )
        if remote.returncode == 0:
            # Recreate the local ref from origin so push-by-name is a no-op
            # instead of a refspec error. The local ref is known-absent here, so
            # this only ever creates (never clobbers local-only commits).
            await self._run_git(
                workspace,
                ["branch", branch, f"origin/{branch}"],
                check=False,
            )
            return
        raise GitCommandError(
            "push",
            f"the task branch '{branch}' does not exist in this workspace and "
            "is not on origin — your commits are not in this clone (it was "
            "likely re-provisioned after a reassignment). unclaim the task and "
            "re-claim it to rebuild the branch, then replay your commits via "
            "commit(...).",
        )

    async def push(
        self, workspace: Path, force: bool = False, branch: str | None = None
    ) -> tuple[str, int]:
        """Push commits to remote.

        ``branch`` pushes that named local branch by ref (``git push origin
        <branch>``), independent of the workspace's current checkout — a dev's
        single clone is shared across many tasks, so by push time it is often
        parked on a LATER task's branch. Defaults to the current branch.

        Returns: (branch, commits_pushed)
        """
        if branch is None:
            branch = await self.get_current_branch(workspace)
        token = await self._token_for_workspace(workspace)
        # The named ref must exist locally for push-by-name; recover it from
        # origin if a re-provisioned/shared clone is missing it (else the push
        # dies on "src refspec ... does not match any" and the task wedges).
        await self._ensure_pushable_branch(workspace, branch, token)

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
            # --force-with-lease fails fast on a concurrent remote advance
            # instead of silently clobbering someone else's commits.
            args.insert(1, "--force-with-lease")

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

        force = getattr(data, "force", False)
        if data.task_id is not None:
            task = await self._assert_task_owned_with_branch(data.task_id, agent_id)
            workspace = await self.get_workspace(data.project_slug, agent_id)
            # Push the task's branch BY NAME, not the current checkout — the
            # shared clone is often parked on a later task's branch by now.
            return await self.push(workspace, force, branch=str(task.branch_name))
        workspace = await self.get_workspace(data.project_slug, agent_id)
        return await self.push(workspace, force)

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
        # Push the task's branch BY NAME, independent of the current checkout.
        # The dev's clone is shared across tasks, so by the QA-submission /
        # open_pr boundary it is usually parked on a LATER task's branch; the
        # old assert-on-current-branch then push-current rejected the push, and
        # the locally-committed work never reached origin → open_pr then saw
        # "No commits between" (origin branch empty/missing). The local task
        # branch ref carries the commits; push it by name.
        _branch, pushed = await self.push(workspace, branch=str(task.branch_name))
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
    def _primary_session_id(_task: TaskTable) -> str | None:
        """Discussion sessions are retired; always None.

        ``RootPRContext.primary_session_id`` is Optional and renders only
        ``if set``, so None is safe.
        """
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
                f"{_api_base()}/repos/{owner}/{repo}/pulls",
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
        self,
        project_slug: str,
        *,
        workflow: str | None = None,
        head_sha: str | None = None,
        branch: str | None = None,
    ) -> dict[str, Any] | None:
        """Latest completed CI (GitHub Actions) run on a project's default branch.

        The inbound telemetry signal for self-healing: the most recent COMPLETED
        workflow run on the project's default branch, normalized to
        ``conclusion`` (``success`` / ``failure`` / ``timed_out`` / ...),
        ``head_sha``, ``run_url``, ``run_name``, ``branch`` and ``completed_at``.
        Resolves owner/repo and the git token PER PROJECT. ``workflow`` (a
        workflow file name like ``ci.yml``) scopes the signal to one workflow —
        without it the latest run across ALL workflows is used, which is
        imprecise on a multi-workflow repo. ``head_sha`` further scopes the run
        window to a specific commit (the release gate uses this so a later push
        to the default branch can't mask the release commit's own CI). Returns
        ``None`` on a missing token, unparseable remote, GitHub error, or a repo
        with no matching Actions runs (a repo that doesn't use GitHub Actions
        yields no signal, not a false one). It never raises into the poll loop.
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
        # Default to the head rung (where dev work and the release gate look);
        # the release-commit CI wait overrides with the prod rung, where the
        # pushed release commit actually lives.
        branch = branch or head_branch(project)
        query = _CiRunQuery(
            project_slug=project_slug,
            owner_repo=(owner, repo),
            branch=branch,
            git_token=git_token,
        )
        run = await self._fetch_latest_ci_run(query, workflow, head_sha)
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
        query: _CiRunQuery,
        workflow: str | None = None,
        head_sha: str | None = None,
    ) -> dict[str, Any] | None:
        """Resolve ``branch``'s current-HEAD CI conclusion; None on error.

        Scopes to ``workflow`` (a workflow file name) when given — the precise
        signal — otherwise reads across ALL workflows, which on a multi-workflow
        repo is unreliable (an unrelated green run can mask a red CI run).
        ``head_sha`` further scopes the window to one commit so the release gate
        can wait on a SPECIFIC release commit's CI without a later push to the
        default branch masking it. Pulls a WINDOW of recent completed runs and
        selects the newest commit's latest attempt (see
        ``_select_ci_head_run``) rather than the single most-recently-completed
        run, so a green run on an older commit can't mask the HEAD's failure and
        a green re-run correctly supersedes it. ``branch`` filters by head
        branch, so only pushes to the default branch (not pull-request runs,
        whose head is a feature branch) count — exactly the "is the default
        branch red" signal self-heal needs. Transient network / 429 / 5xx errors
        are retried a few times before giving up so a single blip doesn't
        silently skip the cycle.
        """
        owner, repo = query.owner_repo
        api_base = settings.github_api_base_url.rstrip("/")
        base = f"{api_base}/repos/{owner}/{repo}/actions"
        url = f"{base}/workflows/{workflow}/runs" if workflow else f"{base}/runs"
        headers = {
            "Authorization": f"Bearer {query.git_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        params: dict[str, str | int] = {
            "branch": query.branch,
            "status": "completed",
            "per_page": _CI_RUN_WINDOW,
        }
        if head_sha:
            params["head_sha"] = head_sha
        resp = await self._get_ci_runs_response(
            query.project_slug, url, headers, params
        )
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
                    f"{_api_base()}/repos/{owner}/{repo}/pulls",
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

    # A single neutral color — labels are distinguished by name, not hue, and
    # GitHub's create-label endpoint requires a color (it won't auto-assign).
    _PR_LABEL_COLOR = "5e6ad2"

    async def _ensure_label_exists(
        self, owner: str, repo: str, git_token: str, name: str
    ) -> None:
        """Create a repo label if missing (GitHub's add-label API 404s on an
        unknown label instead of auto-creating). Swallow 'already exists'
        (422/409). Best-effort: logs and never raises — a missing label must not
        block PR creation."""
        try:
            async with httpx.AsyncClient(timeout=_default_git_timeout()) as client:
                resp = await client.post(
                    f"{_api_base()}/repos/{owner}/{repo}/labels",
                    headers={
                        "Authorization": f"Bearer {git_token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                    json={"name": name, "color": self._PR_LABEL_COLOR},
                )
        except Exception as e:
            self.log.warning("PR label ensure HTTP error", label=name, error=str(e))
            return
        # 422 (already_exists) / 409 (conflict) = the label is already present.
        if resp.is_success or resp.status_code in (409, 422):
            return
        self.log.warning(
            "could not ensure PR label exists",
            label=name,
            status=resp.status_code,
            body=(resp.text or "")[:200],
        )

    async def _apply_pr_labels(
        self,
        owner: str,
        repo: str,
        git_token: str,
        pr_number: int,
        labels: list[str],
    ) -> None:
        """Best-effort: create each label (GitHub won't auto-create on add) then
        add them to the PR. Re-adding is a no-op, so the 422 'PR already exists'
        path is safe to re-label. Never raises — labeling must not block PR
        creation (same posture as ``_record_pr_atomically``)."""
        if not labels:
            return
        for name in labels:
            await self._ensure_label_exists(owner, repo, git_token, name)
        try:
            async with httpx.AsyncClient(timeout=_default_git_timeout()) as client:
                resp = await client.post(
                    f"{_api_base()}/repos/{owner}/{repo}/issues/{pr_number}/labels",
                    headers={
                        "Authorization": f"Bearer {git_token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                    json={"labels": labels},
                )
        except Exception as e:
            self.log.warning("add PR labels HTTP error", pr=pr_number, error=str(e))
            return
        if not resp.is_success:
            self.log.warning(
                "could not add PR labels",
                pr=pr_number,
                status=resp.status_code,
                body=(resp.text or "")[:200],
            )

    async def _task_has_children(self, task_id: UUID) -> bool:
        """True iff the task has any subtask (a one-row probe). PR creation is
        rare; the query is negligible and keeps ``has_children`` honest instead
        of assumed per call site."""
        from sqlalchemy import select

        from roboco.db.tables import TaskTable

        result = await self.session.execute(
            select(TaskTable.id).where(TaskTable.parent_task_id == task_id).limit(1)
        )
        return result.first() is not None

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

    async def _pr_head_branch(
        self, workspace: Path, request: GitCreatePRRequest
    ) -> str:
        """The PR head branch — the task's recorded branch by name.

        A dev's single clone is shared across many tasks, so by open_pr time the
        workspace is often parked on a LATER task's branch; ``get_current_branch``
        would open the PR for the wrong head (or fail). Use the task's
        ``branch_name`` when ``task_id`` is set; fall back to the current branch.
        """
        if request.task_id is not None:
            task_service = get_task_service(self.session)
            task = await task_service.get(request.task_id)
            if task and task.branch_name:
                return str(task.branch_name)
        return await self.get_current_branch(workspace)

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
        source_branch = await self._pr_head_branch(workspace, request)
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

        labels = await self._labels_for_pr_request(request)

        existing = await self._existing_pr_tuple(
            resp, (owner, repo), (source_branch, target_branch), git_token, pr_title
        )
        if existing is not None:
            await self._apply_pr_labels(owner, repo, git_token, existing[0], labels)
            return existing

        if not resp.is_success:
            raise GitError(
                f"GitHub API refused PR creation ({resp.status_code}): "
                f"{resp.text[:200]}",
                {"owner": owner, "repo": repo, "head": source_branch},
            )

        pr_data = resp.json()
        pr_number = int(pr_data["number"])
        await self._apply_pr_labels(owner, repo, git_token, pr_number, labels)
        return (
            pr_number,
            str(pr_data["html_url"]),
            pr_title or "",
            source_branch,
            target_branch,
        )

    async def sync_env_branch(
        self, project_slug: str, target_branch: str, source_branch: str
    ) -> dict[str, Any]:
        """Merge ``source_branch`` (an upper env rung) into ``target_branch`` (the
        lower rung) server-side via GitHub's merges API — one step of the
        prod→head cascade. The merge commit lands on ``target_branch`` (the
        clean-cascade auto-push). The cascade's target is never prod by
        construction (``ladder_pairs``), so prod is never pushed here.

        Returns ``{"status": ...}``:

        * ``already_ancestor`` — target already contains source (HTTP 204).
        * ``merged`` — merge commit created + pushed to target (HTTP 201; ``sha``).
        * ``conflict`` — non-fast-forward / merge conflict (HTTP 409); no commit.
        * ``missing_ref`` — no token / unparseable remote / a branch absent (422).

        Never raises into the engine loop. Does NOT open a PR on conflict —
        the caller decides that.
        """
        project = await get_project_service(self.session).get_by_slug(project_slug)
        if project is None or not project.git_url:
            return {"status": "missing_ref"}
        git_token = await self._token_for_project(project_slug)
        if not git_token:
            return {"status": "missing_ref"}
        try:
            owner, repo = self._parse_git_url(project.git_url)
        except GitError:
            return {"status": "missing_ref"}
        try:
            async with httpx.AsyncClient(timeout=_default_git_timeout()) as client:
                resp = await client.post(
                    f"{_api_base()}/repos/{owner}/{repo}/merges",
                    headers={
                        "Authorization": f"Bearer {git_token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                    json={
                        "base": target_branch,
                        "head": source_branch,
                        "commit_message": f"sync: {source_branch} → {target_branch}",
                    },
                )
        except httpx.HTTPError as exc:
            self.log.warning(
                "env-sync merges API error", project=project_slug, error=str(exc)
            )
            return {"status": "missing_ref"}
        return self._env_merge_status(resp, project_slug)

    def _env_merge_status(
        self, resp: httpx.Response, project_slug: str
    ) -> dict[str, Any]:
        """Map a GitHub merges-API response to an env-sync status dict.

        ``merged`` carries the new merge ``sha``; ``conflict`` (409) leaves the
        target untouched; any other code (incl. 422 missing-ref / no-merge) is
        ``missing_ref`` so the engine skips without opening a PR.
        """
        if resp.status_code == _HTTP_CREATED:
            return {"status": "merged", "sha": resp.json().get("sha")}
        if resp.status_code == _HTTP_NO_CONTENT:
            return {"status": "already_ancestor"}
        if resp.status_code == _HTTP_CONFLICT:
            return {"status": "conflict"}
        self.log.warning(
            "env-sync merges API unexpected status",
            project=project_slug,
            status=resp.status_code,
            body=resp.text[:200],
        )
        return {"status": "missing_ref"}

    async def open_sync_pr(
        self, project_slug: str, source_branch: str, target_branch: str, body: str
    ) -> dict[str, Any] | None:
        """Open (or reuse) a sync PR ``source_branch → target_branch``.

        Idempotent: reuses an already-open PR for the same head→base. Returns
        ``{"number", "url"}`` or None on a missing token / unparseable remote /
        GitHub error — never raises into the engine loop.
        """
        project = await get_project_service(self.session).get_by_slug(project_slug)
        if project is None or not project.git_url:
            return None
        git_token = await self._token_for_project(project_slug)
        if not git_token:
            return None
        try:
            owner_repo = self._parse_git_url(project.git_url)
        except GitError:
            return None
        return await self._post_sync_pr(
            owner_repo, git_token, (source_branch, target_branch), body, project_slug
        )

    async def _post_sync_pr(
        self,
        owner_repo: tuple[str, str],
        git_token: str,
        branches: tuple[str, str],
        body: str,
        project_slug: str,
    ) -> dict[str, Any] | None:
        """Reuse an open sync PR or create a new one for ``source→target``.

        Never raises into the engine loop: a missing existing PR, a rejected
        create, or a transport error all return None.
        """
        owner, repo = owner_repo
        source_branch, target_branch = branches
        existing = await self._find_existing_pr(
            owner, repo, source_branch, target_branch, git_token
        )
        if existing is not None:
            return {
                "number": int(existing["number"]),
                "url": str(existing.get("html_url", "")),
            }
        try:
            resp = await self._post_pr(
                owner,
                repo,
                git_token,
                {
                    "title": f"sync: {source_branch} → {target_branch}",
                    "body": body,
                    "head": source_branch,
                    "base": target_branch,
                },
            )
        except GitError as exc:
            self.log.warning(
                "env-sync PR create failed", project=project_slug, error=str(exc)
            )
            return None
        if not resp.is_success:
            self.log.warning(
                "env-sync PR create rejected",
                project=project_slug,
                status=resp.status_code,
                body=resp.text[:200],
            )
            return None
        data = resp.json()
        return {"number": int(data["number"]), "url": str(data.get("html_url", ""))}

    async def _labels_for_pr_request(
        self,
        request: GitCreatePRRequest,
    ) -> list[str]:
        """The org-structure labels for the REST/task PR path. A task PR derives
        team / batch / has_children from the task; a freeform PR (``task_id``
        None) carries only the tree + root flags."""
        if request.task_id is None:
            return derive_pr_labels(
                is_root_pr=request.is_root_pr,
                task_team=None,
                batch_id=None,
                has_children=False,
            )
        task = await get_task_service(self.session).get(request.task_id)
        if task is None:
            return derive_pr_labels(
                is_root_pr=request.is_root_pr,
                task_team=None,
                batch_id=None,
                has_children=False,
            )
        return derive_pr_labels(
            is_root_pr=request.is_root_pr,
            task_team=task.team,
            batch_id=task.batch_id,
            has_children=await self._task_has_children(UUID(str(task.id))),
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
                    f"{_api_base()}/repos/{owner}/{repo}/pulls/{pr_number}",
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
                    f"{_api_base()}/repos/{owner}/{repo}/pulls/"
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

    async def get_pr_head_sha(self, project_slug: str, pr_number: int) -> str | None:
        """Fetch a PR's current head commit SHA READ-ONLY via the GitHub API.

        Used by the hard ``submit_root`` gate to detect that the assembled root
        PR is byte-identical to the one a prior ``pr_fail`` already rejected —
        i.e. no new cell work landed on the root branch since the fail — so the
        gate can structurally refuse the re-submit instead of looping a weak
        coordinator back into the same failed review (the 2026-06-27
        ``pr_fail`` re-submit loop on PR #139). The head SHA equals the branch
        HEAD at PR-open time, so two PRs opened from an unchanged branch share a
        head SHA — the comparison holds whether or not a new PR number was cut.
        Returns ``None`` on a missing token / unparseable remote / GitHub error
        / a closed-or-missing PR so the gate FAILS OPEN rather than wedging the
        PM (only the exact-unchanged case is hard-blocked; ambiguous cases pass
        through and rely on the reviewer to re-fail).
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
        api_base = settings.github_api_base_url.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=_default_git_timeout()) as client:
                resp = await client.get(
                    f"{api_base}/repos/{owner}/{repo}/pulls/{pr_number}",
                    headers={
                        "Authorization": f"Bearer {git_token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
            if not resp.is_success:
                self.log.warning(
                    "get_pr_head_sha non-2xx",
                    project=project_slug,
                    pr=pr_number,
                    status=resp.status_code,
                )
                return None
            return str(resp.json()["head"]["sha"])
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            self.log.warning(
                "get_pr_head_sha request/parse failed",
                project=project_slug,
                pr=pr_number,
                error=str(e),
            )
            return None

    async def get_pr_ci_status(
        self, project_slug: str, pr_number: int
    ) -> dict[str, Any] | None:
        """CI status of a PR's current head commit, for the in-path ``pr_pass`` gate.

        Reads GitHub check-runs on the PR's head SHA — the same signal GitHub
        Actions surfaces on the PR page — and classifies it into one
        ``state``: ``success`` (every check-run completed with no failing
        conclusion), ``failure`` (at least one completed check-run failed —
        ``failing_checks`` names them), ``pending`` (at least one check-run
        has not completed yet), ``pending_not_scheduled`` (zero check-runs
        exist for this commit but the repo has workflows configured — CI
        just hasn't started), ``no_ci_configured`` (zero check-runs AND the
        repo has no workflows at all — or the project/git_url/token is
        missing, or the repo/PR is unreachable/nonexistent), or ``error`` (a
        genuine GitHub API failure on a real, reachable repo).

        Every unresolvable case is now classified explicitly rather than
        returning ``None``: a missing project/git_url/git-token, or an
        unreachable/nonexistent repo or PR (a network failure or a 404 on
        the PR-head, check-runs, or workflows lookup) all classify as
        ``no_ci_configured`` — ``pr_pass`` passes through cleanly and still
        stamps the evidence note, instead of silently skipping the guard. A
        genuine GitHub API failure on a real, reachable repo (any other
        non-2xx, or an unparseable response) classifies as ``error`` so
        ``pr_pass`` stays fail-closed and retryable. This mirrors ``get_pr_head_sha`` /
        ``_capture_pr_head_sha`` in spirit (never mistake a configuration gap
        for a CI signal) but resolves head-sha lookups via a dedicated helper
        so the two failure classes above stay distinguishable — the shared
        ``get_pr_head_sha`` (used by the unrelated pr_fail head-sha capture)
        is untouched.

        ponytail: reads GitHub check-runs only (the project's own CI is
        GitHub Actions). A repo whose only signal is the legacy commit-status
        API would show zero check-runs here; add a statuses fallback if that
        ever becomes a real CI provider for a project.
        """
        config = await self._ci_status_config(project_slug)
        if isinstance(config, dict):
            return config
        owner, repo, headers = config
        head_sha_or_gap = await self._resolve_ci_head_sha(
            project_slug, pr_number, owner, repo, headers
        )
        if isinstance(head_sha_or_gap, dict):
            return head_sha_or_gap
        head_sha = head_sha_or_gap
        check_runs = await self._fetch_check_runs(
            project_slug, owner, repo, head_sha, headers
        )
        if isinstance(check_runs, dict):
            return check_runs
        if check_runs:
            return self._classify_check_runs(check_runs, head_sha)
        return await self._classify_zero_check_runs(
            project_slug, owner, repo, head_sha, headers
        )

    async def _ci_status_config(
        self, project_slug: str
    ) -> tuple[str, str, dict[str, str]] | dict[str, Any]:
        """Resolve ``(owner, repo, auth headers)`` for a CI-status lookup, or
        a terminal ``no_ci_configured`` gap dict when the project, its
        git_url, or a git token is missing, or the git_url doesn't parse."""
        project = await get_project_service(self.session).get_by_slug(project_slug)
        if project is None or not project.git_url:
            return {"state": "no_ci_configured", "head_sha": None}
        try:
            owner, repo = self._parse_git_url(project.git_url)
        except GitError:
            return {"state": "no_ci_configured", "head_sha": None}
        git_token = await self._token_for_project(project_slug)
        if not git_token:
            return {"state": "no_ci_configured", "head_sha": None}
        headers = {
            "Authorization": f"Bearer {git_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        return owner, repo, headers

    async def _resolve_ci_head_sha(
        self,
        project_slug: str,
        pr_number: int,
        owner: str,
        repo: str,
        headers: dict[str, str],
    ) -> str | dict[str, Any]:
        """Resolve the PR's head SHA for ``get_pr_ci_status`` specifically.

        Returns the head SHA (``str``) on success, or a terminal gap-state
        dict to return directly from the caller on failure. A network error
        or a 404 (the repo or PR doesn't exist / isn't reachable) is
        ``no_ci_configured`` — there is no way to determine a CI signal, so
        the guard should pass through, not block. Any other non-2xx or an
        unparseable response is a real, reachable repo whose API call itself
        failed, so it is ``error`` — a retryable signal the guard must not
        treat as green.
        """
        try:
            async with httpx.AsyncClient(timeout=_default_git_timeout()) as client:
                resp = await client.get(
                    f"{_api_base()}/repos/{owner}/{repo}/pulls/{pr_number}",
                    headers=headers,
                )
        except httpx.HTTPError as e:
            self.log.warning(
                "get_pr_ci_status pr lookup unreachable",
                project=project_slug,
                error=str(e),
            )
            return {"state": "no_ci_configured", "head_sha": None}
        if resp.status_code == _HTTP_NOT_FOUND:
            return {"state": "no_ci_configured", "head_sha": None}
        if not resp.is_success:
            self.log.warning(
                "get_pr_ci_status pr lookup non-2xx",
                project=project_slug,
                status=resp.status_code,
            )
            return {"state": "error", "head_sha": None}
        try:
            return str(resp.json()["head"]["sha"])
        except (ValueError, KeyError, TypeError) as e:
            self.log.warning(
                "get_pr_ci_status pr lookup parse failed",
                project=project_slug,
                error=str(e),
            )
            return {"state": "error", "head_sha": None}

    async def _fetch_check_runs(
        self,
        project_slug: str,
        owner: str,
        repo: str,
        head_sha: str,
        headers: dict[str, str],
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """GET the check-runs for ``head_sha``.

        Returns the (possibly empty) check-runs list on success, or a
        terminal gap-state dict to return directly from the caller: a 404
        (the repo/commit isn't reachable, e.g. no CI integration at all) is
        ``no_ci_configured``; any other failure (network, non-2xx,
        unparseable body) is ``error``.
        """
        try:
            async with httpx.AsyncClient(timeout=_default_git_timeout()) as client:
                resp = await client.get(
                    f"{_api_base()}/repos/{owner}/{repo}/commits/{head_sha}/check-runs",
                    headers=headers,
                    params={"per_page": 100},
                )
        except httpx.HTTPError as e:
            self.log.warning(
                "get_pr_ci_status check-runs request failed",
                project=project_slug,
                error=str(e),
            )
            return {"state": "error", "head_sha": head_sha}
        if resp.status_code == _HTTP_NOT_FOUND:
            return {"state": "no_ci_configured", "head_sha": head_sha}
        if not resp.is_success:
            self.log.warning(
                "get_pr_ci_status check-runs non-2xx",
                project=project_slug,
                status=resp.status_code,
            )
            return {"state": "error", "head_sha": head_sha}
        try:
            runs = resp.json().get("check_runs")
        except (ValueError, AttributeError) as e:
            self.log.warning(
                "get_pr_ci_status check-runs parse failed",
                project=project_slug,
                error=str(e),
            )
            return {"state": "error", "head_sha": head_sha}
        return runs if isinstance(runs, list) else []

    @staticmethod
    def _classify_check_runs(
        check_runs: list[dict[str, Any]], head_sha: str
    ) -> dict[str, Any]:
        """Map a non-empty check-runs list to a failure/pending/success state."""
        failing = [
            str(cr.get("name") or "check")
            for cr in check_runs
            if cr.get("status") == "completed"
            and cr.get("conclusion") in _FAILING_CHECK_CONCLUSIONS
        ]
        if failing:
            return {"state": "failure", "failing_checks": failing, "head_sha": head_sha}
        if any(cr.get("status") != "completed" for cr in check_runs):
            return {"state": "pending", "head_sha": head_sha}
        return {"state": "success", "head_sha": head_sha}

    async def _classify_zero_check_runs(
        self,
        project_slug: str,
        owner: str,
        repo: str,
        head_sha: str,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        """No check-runs exist yet for ``head_sha`` — tell "not scheduled" apart
        from "no CI configured" by asking whether the repo has any workflows.

        A 404 here (repo unreachable/nonexistent — no CI integration) is
        ``no_ci_configured``; any other failure is ``error``.
        """
        try:
            async with httpx.AsyncClient(timeout=_default_git_timeout()) as client:
                resp = await client.get(
                    f"{_api_base()}/repos/{owner}/{repo}/actions/workflows",
                    headers=headers,
                    params={"per_page": 1},
                )
        except httpx.HTTPError as e:
            self.log.warning(
                "get_pr_ci_status workflows request failed",
                project=project_slug,
                error=str(e),
            )
            return {"state": "error", "head_sha": head_sha}
        if resp.status_code == _HTTP_NOT_FOUND:
            return {"state": "no_ci_configured", "head_sha": head_sha}
        if not resp.is_success:
            self.log.warning(
                "get_pr_ci_status workflows non-2xx",
                project=project_slug,
                status=resp.status_code,
            )
            return {"state": "error", "head_sha": head_sha}
        try:
            total = int(resp.json().get("total_count") or 0)
        except (ValueError, AttributeError, TypeError) as e:
            self.log.warning(
                "get_pr_ci_status workflows parse failed",
                project=project_slug,
                error=str(e),
            )
            return {"state": "error", "head_sha": head_sha}
        state = "pending_not_scheduled" if total > 0 else "no_ci_configured"
        return {"state": state, "head_sha": head_sha}

    async def update_pr_for_task(
        self,
        task_id: UUID,
        *,
        title: str | None = None,
        body: str | None = None,
        reviewers: list[str] | None = None,
        actor_agent_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Update an open PR's title/body and/or request reviewers.

        Looks up the task, resolves the project's GitHub PAT, and routes
        through `_patch_pr_title_body` (when title or body is set) and
        `_post_pr_reviewers` (when reviewers is set). Either or both run;
        the verb layer guarantees at least one is provided.

        ``actor_agent_id`` is the agent who actually performed the action
        (e.g. a PM editing a dev's PR); it is threaded through to
        workspace resolution so a creator who never cloned the project
        is never selected as the workspace owner.

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

        workspace_agent_id = self._resolve_workspace_agent_id(task, actor_agent_id)
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

    # PR-open-eligible states — the lifecycle policy owns the canon
    # (``PR_OPEN_STATES``); the HTTP path derives its str set from it so the
    # gateway ``open_pr`` spec gate and this HTTP gate can never drift.
    _PR_OPEN_STATES: ClassVar[frozenset[str]] = frozenset(
        s.value for s in lifecycle.PR_OPEN_STATES
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
        audit_agent_id: UUID | None = None,
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
                task_id=task_uuid,
                pr_number=pr_number,
                pr_url=pr_url,
                audit_agent_id=audit_agent_id,
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
            await self._record_pr_atomically(
                data.task_id, pr_number, pr_url, audit_agent_id=agent_id
            )
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
                    f"{_api_base()}/repos/{owner}/{repo}/pulls/{pr_number}/merge",
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
        """Checkout + pull the target branch, return the tip commit hash.

        If the target branch has no local ref (common in agent workspaces that
        only ever checked out their own task branch), fetch it from origin and
        create a tracking branch before pulling. This prevents the "parent
        branch doesn't exist locally" SERVICE_ERROR that blocks every leaf→cell
        merge in a shared workspace.
        """
        checkout = await self._run_git(
            workspace, ["checkout", target_branch], check=False
        )
        if checkout.returncode != 0:
            # Local ref missing — fetch the single branch from origin and create
            # a tracking branch. A full `git fetch` is avoided because the
            # workspace may hold many refs; narrowing to the target keeps it
            # cheap and avoids token-burn on a large repo.
            await self._run_git(
                workspace, ["fetch", "origin", target_branch], token=git_token
            )
            tracking = await self._run_git(
                workspace,
                ["checkout", "-b", target_branch, f"origin/{target_branch}"],
                check=False,
            )
            if tracking.returncode != 0:
                raise GitError(
                    f"Could not check out target branch '{target_branch}' "
                    "locally or from origin.",
                    {
                        "target_branch": target_branch,
                        "checkout_stderr": checkout.stderr.strip(),
                        "tracking_stderr": tracking.stderr.strip(),
                    },
                )
        await self._run_git(workspace, ["pull"], token=git_token)
        log_result = await self._run_git(workspace, ["log", "-1", "--format=%H"])
        return log_result.stdout.strip()

    async def _sync_target_branch_best_effort(
        self, workspace: Path, target_branch: str, git_token: str
    ) -> str | None:
        """Post-merge local sync of ``target_branch`` — best-effort, never raises.

        Callers reach this only AFTER the authoritative GitHub merge has already
        landed (``merge_pull_request`` / ``pr_merge`` raise if it did not), so
        refreshing the local workspace copy of the target branch is cosmetic.
        If the branch is gone from origin — e.g. an integration (cell/root)
        branch deleted after a sibling cell→root merge, stranding a late
        straggler leaf — ``_sync_target_branch`` raises ``fetch origin <branch>
        — couldn't find remote ref``. Letting that propagate turned an
        already-completed merge into a retryable ``SERVICE_ERROR`` that re-blocked
        the task and respawn-looped the PM. Swallow + log instead: the merge is
        done; the local sync is not worth failing on.
        """
        try:
            return await self._sync_target_branch(workspace, target_branch, git_token)
        except GitError as exc:
            self.log.warning(
                "post-merge target-branch sync failed; merge already landed,"
                " continuing without local sync",
                target_branch=target_branch,
                error=str(exc),
            )
            return None

    async def _branch_has_open_dependents(
        self, owner: str, repo: str, branch: str, git_token: str
    ) -> bool:
        """True if any OPEN PR still targets ``branch`` as its base.

        Such a branch is an active integration target — a leaf still merging
        into its cell branch, or a cell still merging into the
        ``feature/main_pm/{root}`` integration branch. Deleting it strands those
        in-flight child PRs (their base vanishes), which is the run-zombifying
        "branch gone from origin" wedge. Fails SAFE: on any error returns True
        so the branch is preserved (cleanup is best-effort; stranding is not).
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{_api_base()}/repos/{owner}/{repo}/pulls",
                    params={"base": branch, "state": "open", "per_page": 1},
                    headers={
                        "Authorization": f"Bearer {git_token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
            if not resp.is_success:
                return True
            return bool(resp.json())
        except httpx.HTTPError:
            return True

    async def _delete_remote_branch_best_effort(
        self, owner: str, repo: str, branch: str, git_token: str
    ) -> bool:
        """Best-effort: delete a remote branch by name.

        Silently swallows errors — cleanup is not critical. Skips branches that
        look like project defaults (main / master / develop) and any branch that
        still has open dependent PRs (an active integration target — deleting it
        would strand in-flight child work). Returns True if the delete request
        was issued with no transport error, False on any skip/failure — callers
        that only fire-and-forget can ignore it; the branch-cleanup sweep uses
        it to report counts.
        """
        if branch in ("main", "master", "develop", ""):
            return False
        if await self._branch_has_open_dependents(owner, repo, branch, git_token):
            self.log.info(
                "branch delete skipped: open dependent PRs target it as base",
                branch=branch,
                owner=owner,
                repo=repo,
            )
            return False
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.delete(
                    f"{_api_base()}/repos/{owner}/{repo}/git/refs/heads/{branch}",
                    headers={
                        "Authorization": f"Bearer {git_token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
            return True
        except httpx.HTTPError:
            return False

    async def _delete_pr_branch_best_effort(
        self, owner: str, repo: str, pr_number: int, git_token: str
    ) -> None:
        """Best-effort: delete the PR's source branch on the remote after merge.

        Silently swallows errors — branch cleanup is not critical.
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                pr_resp = await client.get(
                    f"{_api_base()}/repos/{owner}/{repo}/pulls/{pr_number}",
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

    async def delete_task_branch(self, project_slug: str, branch_name: str) -> bool:
        """Delete a remote task branch after cancel/discard. Best-effort.

        Called by `TaskService` on cancellation so abandoned task
        branches don't accumulate on the remote. Returns whether the delete
        was actually issued (see ``_delete_remote_branch_best_effort``).

        This is the chokepoint every task-scoped remote-delete call routes
        through, so the environment-ladder guard lives here rather than only
        at each caller: ``_delete_remote_branch_best_effort``'s own
        main/master/develop skip predates the env-ladder model and doesn't
        know about it (it's a generic branch-delete primitive also used by
        the merged-PR source-branch cleanup, which never targets a ladder
        branch by construction) — a task's ``branch_name`` could otherwise
        coincide with a ladder rung and get deleted out from under it.
        """
        git_token = await self._token_for_project(project_slug)
        if not git_token:
            return False
        # Resolve remote from any workspace — branch deletion only needs
        # the owner/repo, not a checkout. Use a service-root probe path
        # if no agent workspace is available.
        try:
            project_service = get_project_service(self.session)
            project = await project_service.get_by_slug(project_slug)
            if not project or not project.git_url:
                return False
            if branch_name in {r.branch for r in effective_environments(project)}:
                return False
            owner, repo = self._parse_git_url(project.git_url)
        except Exception:
            return False
        return await self._delete_remote_branch_best_effort(
            owner, repo, branch_name, git_token
        )

    # Per-call cap on the stale-branch sweep so one request can't hang on an
    # unbounded fan-out of remote-delete calls.
    _CLEANUP_BRANCH_LIMIT = 200

    async def cleanup_stale_branches(
        self, project_slug: str
    ) -> tuple[int, int, int, int, bool]:
        """Sweep a project's terminal tasks and delete their spent branches.

        Candidates are TERMINAL (completed/cancelled) tasks with a
        ``branch_name`` that isn't an environment-ladder rung (a ladder branch
        outlives any one task — see ``roboco.models.env_branches``). Capped at
        ``_CLEANUP_BRANCH_LIMIT``; a bigger candidate set is truncated, not
        rejected. Per branch, best-effort: remote delete (the same guarded
        ``delete_task_branch`` cancel already uses — main/master/develop and
        open-dependent-PR branches are skipped there too) and, in the
        assignee's clone, a local delete (force for a cancelled task's branch,
        safe ``-d`` for a completed one).

        Returns ``(remote_deleted, local_deleted, skipped, errors, truncated)``.
        ``local_deleted`` counts a local delete as ATTEMPTED (assignee/clone
        resolved), not confirmed — the underlying ``git branch -d/-D`` is
        itself best-effort and reports no outcome. ``skipped`` counts branches
        with no resolvable assignee/clone (nothing to locally clean up, though
        the remote delete may still have run); ``errors`` counts branches that
        raised unexpectedly while resolving the assignee's workspace.
        """
        from sqlalchemy import select

        from roboco.db.tables import TaskTable

        project_service = get_project_service(self.session)
        project = await project_service.get_by_slug(project_slug)
        if not project:
            return (0, 0, 0, 0, False)
        ladder_branches = {rung.branch for rung in effective_environments(project)}

        result = await self.session.execute(
            select(TaskTable)
            .where(TaskTable.project_id == project.id)
            .where(TaskTable.branch_name.is_not(None))
            .where(TaskTable.status.in_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]))
            .limit(self._CLEANUP_BRANCH_LIMIT + 1)
        )
        candidates = [
            t
            for t in result.scalars().all()
            if str(t.branch_name) not in ladder_branches
        ]
        truncated = len(candidates) > self._CLEANUP_BRANCH_LIMIT
        candidates = candidates[: self._CLEANUP_BRANCH_LIMIT]

        remote_deleted = local_deleted = skipped = errors = 0
        workspace_service = get_workspace_service(self.session)
        for task in candidates:
            branch = str(task.branch_name)
            try:
                remote_ok, local_attempted = await self._cleanup_one_stale_branch(
                    project_slug, task, branch, workspace_service
                )
            except Exception as e:
                errors += 1
                self.log.warning(
                    "Stale-branch cleanup skipped for branch",
                    project_slug=project_slug,
                    branch=branch,
                    error=str(e),
                )
                continue
            remote_deleted += int(remote_ok)
            if local_attempted:
                local_deleted += 1
            else:
                skipped += 1

        return (remote_deleted, local_deleted, skipped, errors, truncated)

    async def _cleanup_one_stale_branch(
        self,
        project_slug: str,
        task: TaskTable,
        branch: str,
        workspace_service: WorkspaceService,
    ) -> tuple[bool, bool]:
        """Delete one candidate's remote + local branch.

        Returns ``(remote_deleted, local_attempted)`` — see
        ``cleanup_stale_branches`` for what each means. Raises on an
        unexpected failure so the caller's per-branch try/except counts it.
        """
        remote_deleted = await self.delete_task_branch(project_slug, branch)

        assignee = task.assignee
        if assignee is None or assignee.team is None or assignee.slug is None:
            return remote_deleted, False

        clone_root = workspace_service.get_clone_root_path(
            project_slug, assignee.team, assignee.slug
        )
        await workspace_service.delete_local_branch(
            clone_root, branch, force=task.status == TaskStatus.CANCELLED
        )
        return remote_deleted, True

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
                    f"{_api_base()}/repos/{owner}/{repo}",
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
            # A merge PUT on an already-merged PR returns the same 405/409 as a
            # genuine "not mergeable" refusal. Disambiguate: if the PR is in
            # fact already merged (a CEO double-click, or a first PUT that
            # landed before a network blip made us retry), treat it as
            # idempotent success and fall through to the post-merge cleanup —
            # mirroring the agent-facing _merge_with_retry. Without this a
            # CEO retry raised GitError on the very master-merge path only the
            # CEO owns, surfacing a spurious failure for an action that had
            # already succeeded. None (HTTPError — indeterminate) is treated
            # as "assume merged" so a network blip can't surface a spurious
            # failure on the CEO-only master-merge path.
            merged = await self._pr_is_merged(owner, repo, pr_number, git_token)
            if merged is False:
                raise GitError(
                    f"GitHub API refused PR merge ({resp.status_code}):"
                    f" {resp.text[:200]}",
                    {"owner": owner, "repo": repo, "pr": pr_number},
                )
            self.log.info(
                "PR already merged on GitHub; treating as idempotent success",
                owner=owner,
                repo=repo,
                pr=pr_number,
                status_code=resp.status_code,
            )

        await self._delete_pr_branch_best_effort(owner, repo, pr_number, git_token)

        target_branch = await self._project_default_branch(project_slug)
        # Default branch always exists on origin, so the plain sync is correct
        # here. The best-effort variant guards the agent-facing pr_merge path,
        # whose target can be an integration branch deleted from origin.
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

            # The recorded PR (task.pr_number, set when the PR was opened) is
            # the source of truth for which PR belongs to this task. The
            # caller's data.pr_number is unverified client input — a stale panel
            # form, a cached old number, a buggy client — and merging it blindly
            # would merge the wrong PR against this task's work-session and
            # auto-complete. Refuse unless the caller's number matches the
            # recorded one; a task with no recorded PR has nothing to verify
            # against and is refused outright (re-opening the wrong-PR gap for
            # any task that lost its pr_number is worse than a clear error).
            recorded_pr = getattr(task, "pr_number", None)
            if recorded_pr is None:
                raise ValidationError(
                    "PR_MISMATCH: task has no recorded pr_number; refusing to"
                    f" merge caller-provided PR #{data.pr_number} for task"
                    f" {data.task_id} — the merge path requires the task's own"
                    " recorded PR."
                )
            if recorded_pr != data.pr_number:
                raise ValidationError(
                    "PR_MISMATCH: caller asked to merge PR"
                    f" #{data.pr_number} but task {data.task_id}'s recorded PR"
                    f" is #{recorded_pr}. The recorded PR is the source of"
                    " truth; merge the task's own PR, not a caller-provided"
                    " number."
                )

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
        if product_id is not None:
            from roboco.services.product import get_product_service

            product_service = get_product_service(self.session)
            project_ids = await product_service.distinct_project_ids(
                UUID(str(product_id))
            )
            if not project_ids:
                return None
            return await project_service.get(project_ids[0])
        # Ad-hoc per-cell map root-subtask: mirror the product root's first-project
        # resolution so root-level git ops resolve a workspace for the mapped repo.
        cell_map = getattr(task, "cell_projects", None) or []
        seen: set[UUID] = set()
        for mapping in sorted(cell_map, key=lambda m: m.team.value):
            pid = UUID(str(mapping.project_id))
            if pid not in seen:
                seen.add(pid)
                return await project_service.get(pid)
        return None

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

        actor_agent_id → task.assigned_to → None. The creator is NOT
        consulted: a PM who created the task but never cloned the
        project's workspace would 404 on get_workspace(PM-id), so
        falling back to created_by manufactures a broken lookup.
        Callers without a real actor get None (project.workspace_path).
        Centralised so push_branch/create_pr/commit/diff/pr_target/pr_merge
        share one chain — and individual methods stay below the
        cyclomatic-complexity gate (xenon B).
        """
        if actor_agent_id is not None:
            return actor_agent_id
        if task.assigned_to is not None:
            return UUID(str(task.assigned_to))
        return None

    async def _workspace_for_branch(
        self,
        branch_name: str,
        *,
        actor_agent_id: UUID | None = None,
    ) -> Path:
        """Get a workspace where this branch can be operated on.

        Resolves the workspace via ``_resolve_workspace_agent_id`` (the
        actor → assignee → None fallback, with project.workspace_path as
        the final fallback). Without it, post-handoff calls (e.g.
        pr_target on a task whose assigned_to was cleared by submit_qa)
        raise ValidationError when project.workspace_path is unset.
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
        # Push the NAMED branch, not the workspace's current checkout. The
        # clone root is shared across a dev's tasks and (F123) parked on the
        # default branch while the task branch lives in a per-task worktree;
        # push() with branch=None defaults to get_current_branch(workspace),
        # which pushed the wrong ref. The dev's commit then never reached
        # origin, so create_pr 422'd with "No commits between" and the dev was
        # forced into i_am_blocked — stranded work the PM's unblock can't fix
        # (it only flips status, not git state). Push-by-name also survives a
        # clone parked on a LATER task's branch.
        return await self.push(workspace, branch=branch_name)

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

        # Org-structure labels: create_pr is always an assembled PM PR
        # (cell->root or root->master), so has_children is True by construction.
        labels = derive_pr_labels(
            is_root_pr=is_root_pr,
            task_team=task.team,
            batch_id=task.batch_id,
            has_children=True,
        )

        if resp.status_code == _GH_UNPROCESSABLE and "already exists" in resp.text:
            found = await self._find_existing_pr(
                owner, repo, branch_name, parent, git_token
            )
            if found:
                pr_number = int(found["number"])
                pr_url = str(found["html_url"])
                # Shielded + waited-out: the PR already exists on GitHub, so
                # a cancellation here must not skip recording it locally —
                # and shield alone would leave the detached write racing
                # get_db's rollback on the same session (see _await_shielded).
                await _await_shielded(
                    self._record_pr_atomically(UUID(str(task.id)), pr_number, pr_url)
                )
                await self._apply_pr_labels(owner, repo, git_token, pr_number, labels)
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
        # _post_pr already created the PR on GitHub — shield the local record
        # so a cancellation landing between the POST and this commit can't
        # leave it unrecorded (self-heals on retry via _find_existing_pr, but
        # only after this window closes). Waited-out, not bare shield: the
        # detached write must finish before get_db's rollback touches the
        # same session (see _await_shielded).
        await _await_shielded(
            self._record_pr_atomically(UUID(str(task.id)), pr_number, pr_url)
        )
        await self._apply_pr_labels(owner, repo, git_token, pr_number, labels)
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
        """Single-retry merge: on 409 (race) sync target then retry; on 405
        (repo disallows the merge method) fall back to a permitted method."""
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
        if resp.status_code == _HTTP_METHOD_NOT_ALLOWED:
            # The repo's settings disallow squash (the button is off). Try a
            # method the repo permits before treating this as a conflict —
            # mirrors the CEO merge_pull_request 405 fallback so a repo's
            # merge-button config can't wedge the PM on an open, mergeable PR.
            # A 405 with no permitted fallback (or a second 405) falls through
            # to the already-merged disambiguation / MergeConflictError below.
            fallback = await self._first_allowed_merge_method(
                ctx.owner, ctx.repo, ctx.git_token, exclude="squash"
            )
            if fallback and fallback != "squash":
                self.log.info(
                    "Merge method refused by repo; retrying with a permitted one",
                    requested="squash",
                    fallback=fallback,
                    owner=ctx.owner,
                    repo=ctx.repo,
                    pr=ctx.pr_number,
                )
                resp = await self._call_merge_api(
                    ctx.owner, ctx.repo, ctx.pr_number, ctx.git_token, fallback
                )
        if not resp.is_success:
            # A merge PUT on an ALREADY-MERGED PR returns the same 405 as a
            # genuine "not mergeable" conflict. An already-merged PR (a prior
            # cycle, a sibling, or the CEO already landed it) is idempotent
            # success — NOT a conflict to rebase/escalate. Treating it as one is
            # the cell_pm_complete block<->unblock respawn loop, so disambiguate
            # before raising. None (HTTPError — indeterminate) is treated as
            # "assume merged" so a network blip can't respawn the PM against an
            # already-merged PR; only a clean False is a real conflict.
            merged = await self._pr_is_merged(
                ctx.owner, ctx.repo, ctx.pr_number, ctx.git_token
            )
            if merged is False:
                # A real merge refusal (typically 405 "not mergeable") means the
                # branch conflicts with the base — a sibling landed overlapping
                # work first. Raise the specific subclass so the completion path
                # can rebase / close-superseded / escalate instead of
                # respawn-looping.
                raise MergeConflictError(
                    f"GitHub API refused PR merge ({resp.status_code}):"
                    f" {resp.text[:200]}",
                    {"owner": ctx.owner, "repo": ctx.repo, "pr": ctx.pr_number},
                )
            return resp
        return resp

    async def _pr_is_merged(
        self, owner: str, repo: str, pr_number: int, git_token: str
    ) -> bool | None:
        """True if PR ``pr_number`` is already merged on GitHub.

        Disambiguates an already-merged PR from a genuine conflict (both surface
        as a 405 on the merge PUT): an already-merged PR is idempotent success,
        not something to rebase/escalate. Returns ``None`` on an
        ``httpx.HTTPError`` so an indeterminate lookup is NOT mistaken for a
        clean "not merged" — callers treat ``None`` as "assume merged" and
        fall through to the already-merged cleanup path instead of raising a
        conflict and respawning the PM against an already-merged PR. A
        non-success response is still False (GitHub answered, just not merged).
        """
        try:
            async with httpx.AsyncClient(timeout=_default_git_timeout()) as client:
                resp = await client.get(
                    f"{_api_base()}/repos/{owner}/{repo}/pulls/{pr_number}",
                    headers={
                        "Authorization": f"Bearer {git_token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
        except httpx.HTTPError:
            return None
        if not resp.is_success:
            return False
        return bool(resp.json().get("merged"))

    async def is_pr_merged_for_task(self, task_id: UUID) -> bool:
        """True if the task's PR is already merged on GitHub.

        Idempotency check for ``cell_pm_complete`` re-issues: a re-issue after
        a None-complete would otherwise 405 on the already-merged PR. An
        indeterminate ``_pr_is_merged`` (``None`` on ``httpx.HTTPError``) is
        treated as "assume merged" → ``True`` so the choreographer skips the
        merge call instead of 405-ing into a respawn loop on an already-merged
        PR. A clean False (GitHub answered, not merged) returns False — the
        caller proceeds to ``pr_merge``, which surfaces the real error.
        """
        from sqlalchemy import select

        from roboco.db.tables import TaskTable as _TaskTable

        result = await self.session.execute(
            select(_TaskTable).where(_TaskTable.id == task_id).limit(1)
        )
        task = result.scalar_one_or_none()
        if task is None or not task.pr_number:
            return False
        project = await self._project_for_task(task)
        if project is None:
            return False
        workspace_agent_id = self._resolve_workspace_agent_id(task, None)
        workspace = await self.get_workspace(project.slug, agent_id=workspace_agent_id)
        git_token = await self._get_project_token_or_raise(project.slug)
        owner, repo = self._parse_github_remote(workspace)
        merged = await self._pr_is_merged(owner, repo, task.pr_number, git_token)
        return True if merged is None else bool(merged)

    async def pr_merge(
        self,
        pr_number: int,
        *,
        target: str,
        project_id: UUID,
        actor_agent_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Merge PR `pr_number` into `target`.

        Returns: ``{"merge_commit_sha": str | None}``. Looks up the
        task/project that owns the PR to resolve workspace + token.

        ``pr_number`` alone is ambiguous across projects — GitHub numbers
        PRs per-repo, but ``tasks.pr_number`` stores the bare integer with
        no repo scoping, so two tasks on different repos can share a number.
        The caller MUST pass the ``project_id`` the PR belongs to so the
        task lookup is scoped to it: a same-numbered PR in another
        project's repo is never resolved (and merged, or its work session
        marked merged) by accident. Mirrors :meth:`close_pull_request`.

        Concurrency: takes a row-level lock on the parent task before
        invoking the GitHub merge API so that two PMs completing
        sibling subtasks of the same parent are serialized. On a 409
        merge conflict the local target branch is re-pulled and the
        merge is retried exactly once before giving up with `GitError`.
        """
        from sqlalchemy import select

        from roboco.db.tables import TaskTable as _TaskTable

        result = await self.session.execute(
            select(_TaskTable)
            .where(_TaskTable.pr_number == pr_number)
            .where(_TaskTable.project_id == project_id)
            .limit(1)
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

        # CEO is the only one who merges into the project's head environment
        # branch (ladder index 0 — "master" only when no ladder is declared;
        # see _project_default_branch). This agent-facing merge path (a cell
        # PM merging a leaf/cell PR up the chain) may NEVER target it — that
        # PR is merged solely by the CEO via approve-&-merge
        # (merge_pr_for_task, CEO-gated from awaiting_ceo_approval). Agents
        # open the PR to it and escalate.
        default_branch = await self._project_default_branch(project.slug)
        if target == default_branch:
            raise UnauthorizedError(
                action="pr_merge",
                reason=(
                    f"CEO_ONLY: merging into '{default_branch}' (this "
                    "project's head environment branch) is reserved for the "
                    "CEO via approve-&-merge from awaiting_ceo_approval. "
                    "Open the PR and escalate; agents never merge directly "
                    f"into '{default_branch}'."
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
        merge_commit = await self._sync_target_branch_best_effort(
            workspace, target, git_token
        )

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
                    f"{_api_base()}/repos/{owner}/{repo}/pulls/{pr_number}",
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
        stash: bool = False,
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
        Any of the above may carry ``"stash_pop_conflict": True`` when
        ``stash`` popped into a conflict (see below).

        Never touches the base branch and only ever force-pushes
        ``head_branch`` (with ``--force-with-lease``). A master/main base is
        legitimate when it is the head's true merge target; the choreographer
        refuses only a mis-resolved one.

        Safety gate (mirrors :meth:`pull`): refuses on a dirty worktree so the
        ``git reset --hard`` below can't discard uncommitted agent edits —
        UNLESS ``stash=True``, in which case the dirty worktree (tracked +
        untracked, ``-u``) is stashed first and popped back after the rebase
        instead of refusing outright (the dev-facing dead end this closes:
        DIRTY_WORKSPACE had no in-gate remedy other than a raw ``git`` the
        agent is denied). A pop conflict is never auto-resolved — the stash
        is left in place (never dropped) and the result gets
        ``stash_pop_conflict: True`` so the caller returns an actionable
        envelope; the agent's uncommitted work is never lost.
        """
        stashed = await self._stash_if_dirty(workspace, stash=stash)

        await self._run_git(workspace, ["fetch", "origin"], token=git_token)
        await self._run_git(workspace, ["checkout", head_branch])
        await self._run_git(workspace, ["reset", "--hard", f"origin/{head_branch}"])
        rebase = await self._run_git(
            workspace, ["rebase", f"origin/{base_branch}"], check=False
        )
        if rebase.returncode != 0:
            return await self._abort_rebase_conflict(workspace, stashed=stashed)
        count = await self._run_git(
            workspace,
            ["rev-list", "--count", f"origin/{base_branch}..HEAD"],
        )
        unique = int(count.stdout.strip() or "0")
        if unique == 0:
            result: dict[str, Any] = {"status": "superseded"}
        else:
            await self._run_git(
                workspace,
                ["push", "--force-with-lease", "origin", f"HEAD:{head_branch}"],
                token=git_token,
            )
            result = {"status": "rebased", "unique_commits": unique}
        if stashed:
            await self._pop_stash_into(workspace, result)
        return result

    async def _stash_if_dirty(self, workspace: Path, *, stash: bool) -> bool:
        """Clean-tree gate for :meth:`rebase_onto_base`.

        Refuses a dirty worktree (``DIRTY_WORKSPACE``) unless ``stash`` is
        set, in which case it auto-stashes (tracked + untracked) and returns
        ``True`` so the caller knows to pop it back later.
        """
        status_result = await self._run_git(
            workspace, ["status", "--porcelain"], check=False
        )
        if not status_result.stdout.strip():
            return False
        if not stash:
            raise ValidationError(
                "DIRTY_WORKSPACE: Cannot rebase with uncommitted changes. "
                "Stage and commit (or stash) your changes before rebasing."
            )
        await self._run_git(
            workspace, ["stash", "push", "-u", "-m", "sync_branch autostash"]
        )
        return True

    async def _abort_rebase_conflict(
        self, workspace: Path, *, stashed: bool
    ) -> dict[str, Any]:
        """Collect conflicted files and abort a failed rebase.

        The stash (if one was taken) is left untouched here — popping it onto
        an aborted, still-conflicted rebase would just stack a second conflict
        on top of the first. ``stash_preserved`` is only added when a stash
        was actually taken, so the non-stash result shape is unchanged.
        """
        conflict = await self._run_git(
            workspace, ["diff", "--name-only", "--diff-filter=U"], check=False
        )
        files = [f for f in conflict.stdout.splitlines() if f.strip()]
        await self._run_git(workspace, ["rebase", "--abort"], check=False)
        result: dict[str, Any] = {"status": "conflicts", "files": files}
        if stashed:
            result["stash_preserved"] = True
        return result

    async def _pop_stash_into(self, workspace: Path, result: dict[str, Any]) -> None:
        """Pop the autostash, flagging (never auto-resolving) a pop conflict."""
        pop = await self._run_git(workspace, ["stash", "pop"], check=False)
        if pop.returncode != 0:
            result["stash_pop_conflict"] = True

    async def rebase_pr_for_task(
        self,
        pr_number: int,
        *,
        project_id: UUID,
        actor_agent_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Resolve workspace/refs for a PR and rebase its branch onto the base.

        Thin wrapper over :meth:`rebase_onto_base` that loads the task/project
        that owns the PR (mirrors :meth:`pr_merge`) and reads the PR's head/base
        refs from GitHub. Returns the same classification dict, or
        ``{"status": "unknown"}`` when refs can't be resolved.

        ``pr_number`` is ambiguous across projects (GitHub numbers PRs per-repo)
        so the caller MUST pass ``project_id`` to scope the task lookup — the
        same cross-repo guard as :meth:`pr_merge` / :meth:`close_pull_request`.
        """
        from sqlalchemy import select

        from roboco.db.tables import TaskTable as _TaskTable

        result = await self.session.execute(
            select(_TaskTable)
            .where(_TaskTable.pr_number == pr_number)
            .where(_TaskTable.project_id == project_id)
            .limit(1)
        )
        task = result.scalar_one_or_none()
        if task is None:
            raise NotFoundError("PR", str(pr_number))
        project = await self._project_for_task(task)
        if project is None:
            raise NotFoundError("Project for task", str(task.id))

        workspace_agent_id = self._resolve_workspace_agent_id(task, actor_agent_id)
        clone_root = await self.get_workspace(project.slug, agent_id=workspace_agent_id)
        git_token = await self._get_project_token_or_raise(project.slug)
        owner, repo = self._parse_github_remote(clone_root)

        refs = await self._get_pr_refs(owner, repo, pr_number, git_token)
        if refs is None:
            return {"status": "unknown"}
        head_branch, base_branch = refs
        # Rebase inside the per-task worktree (F123): the PR head branch is
        # checked out there, so a checkout in the clone root would be refused.
        workspace = self._worktree_for_task(clone_root, require_uuid(task.id))
        await self._ensure_worktree_for_commit(clone_root, workspace, head_branch)
        return await self.rebase_onto_base(
            workspace,
            head_branch=head_branch,
            base_branch=base_branch,
            git_token=git_token,
        )

    async def sync_task_branch(
        self,
        task: Any,
        *,
        base_branch: str,
        actor_agent_id: UUID | None = None,
        stash: bool = False,
    ) -> dict[str, Any]:
        """Rebase a task's branch onto ``base_branch`` through the gate.

        Task-keyed twin of :meth:`rebase_pr_for_task`: ``head_branch`` is the
        task's own ``branch_name`` and ``base_branch`` is supplied by the caller
        (the choreographer resolves it via ``merge_chain.resolve_parent_branch``),
        so this works BEFORE a PR exists — a developer mid-work whose branch
        fell behind its base (a sibling's PR merged into the parent branch) can
        rebase through the dev ``sync_branch`` verb instead of the CEO/PM-only
        ``/rebase`` HTTP route. Mirrors ``rebase_pr_for_task``'s workspace/token
        resolution and delegates to :meth:`rebase_onto_base`, returning the same
        classification dict (``rebased`` / ``superseded`` / ``conflicts``).

        ``stash`` forwards to :meth:`rebase_onto_base` — auto-stash a dirty
        worktree instead of refusing DIRTY_WORKSPACE.

        A master/main base is legitimate when it is the task's true merge
        target (standalone task, branchless-parent child); the choreographer
        refuses only a mis-resolved one. The push only ever targets the task
        branch.
        """
        if not task.branch_name:
            raise ValueError("sync_task_branch requires a task with a branch_name")
        project = await self._project_for_task(task)
        if project is None:
            raise NotFoundError("Project for task", str(task.id))
        workspace_agent_id = self._resolve_workspace_agent_id(task, actor_agent_id)
        clone_root = await self.get_workspace(project.slug, agent_id=workspace_agent_id)
        git_token = await self._get_project_token_or_raise(project.slug)
        # Rebase inside the per-task worktree (F123): the branch is checked out
        # there, so a checkout in the clone root would be refused ("already
        # checked out at '<worktree>'") and the behind-base recovery loop dies.
        workspace = self._worktree_for_task(clone_root, require_uuid(task.id))
        await self._ensure_worktree_for_commit(clone_root, workspace, task.branch_name)
        return await self.rebase_onto_base(
            workspace,
            head_branch=task.branch_name,
            base_branch=base_branch,
            git_token=git_token,
            stash=stash,
        )

    async def unmerged_child_commits(
        self,
        task: Any,
        *,
        actor_agent_id: UUID | None = None,
    ) -> list[dict[str, Any]]:
        """Completed children whose commits are NOT in the assembled branch.

        Patch-equivalence via ``git cherry`` (rebase-safe — the assembled
        branch may have been rebased, rewriting SHAs). Best-effort per child:
        a child with no branch / no commits / a branch pruned from origin
        after merge contributes nothing. Returns
        ``[{"task_id", "title", "unmerged"}, ...]`` for children with at
        least one patch missing from the parent branch (live incident #11:
        a completed revert absent from the assembled cell PR).
        """
        if not getattr(task, "branch_name", None):
            return []
        task_service = get_task_service(self.session)
        children = await task_service.get_subtasks(require_uuid(task.id))
        candidates = _completed_branchful_children(children)
        if not candidates:
            return []
        project = await self._project_for_task(task)
        if project is None:
            return []
        workspace_agent_id = self._resolve_workspace_agent_id(task, actor_agent_id)
        workspace = await self.get_workspace(project.slug, agent_id=workspace_agent_id)
        git_token = await self._get_project_token_or_raise(project.slug)
        refs = [str(task.branch_name)] + [str(c.branch_name) for c in candidates]
        await self._run_git(
            workspace,
            ["fetch", "origin", *dict.fromkeys(refs)],
            token=git_token,
            check=False,
            timeout=_network_git_timeout(),
        )
        missing: list[dict[str, Any]] = []
        for child in candidates:
            entry = await self._cherry_unmerged_entry(
                workspace, str(task.branch_name), child
            )
            if entry is not None:
                missing.append(entry)
        return missing

    async def _cherry_unmerged_entry(
        self, workspace: Path, parent_branch: str, child: Any
    ) -> dict[str, Any] | None:
        """One child's unmerged-commit entry, or None (merged / unprobeable).

        A child branch pruned from origin after merge, or any git error,
        contributes nothing — best-effort per child.
        """
        child_ref = f"origin/{child.branch_name}"
        ref_ok = await self._run_git(
            workspace,
            ["rev-parse", "--verify", "--quiet", child_ref],
            check=False,
        )
        if ref_ok.returncode != 0:
            return None  # branch pruned after merge — nothing to compare
        cherry = await self._run_git(
            workspace,
            ["cherry", f"origin/{parent_branch}", child_ref],
            check=False,
        )
        if cherry.returncode != 0:
            return None
        unmerged = [line for line in cherry.stdout.splitlines() if line.startswith("+")]
        if not unmerged:
            return None
        # Squash-merge relief: cherry can't patch-match N child commits against
        # the one squashed commit, but every commit (incl. the squash) carries
        # the [taskid8] prefix — a marker commit on the parent proves the child
        # landed (live false positive 2026-07-02: 3 squash-merged children).
        marker = await self._run_git(
            workspace,
            [
                "log",
                f"origin/{parent_branch}",
                "--grep",
                rf"^\[{str(child.id)[:8]}\] ",
                "--oneline",
                "-1",
            ],
            check=False,
        )
        if marker.returncode == 0 and marker.stdout.strip():
            return None
        return {
            "task_id": str(child.id)[:8],
            "title": str(getattr(child, "title", ""))[:80],
            "unmerged": len(unmerged),
        }

    async def is_behind_base(
        self,
        task: Any,
        *,
        base_branch: str,
        actor_agent_id: UUID | None = None,
    ) -> tuple[int, int]:
        """Return ``(behind, ahead)`` commit counts: head vs base branch.

        ``behind`` = commits on ``origin/{base_branch}`` NOT on
        ``origin/{head_branch}`` — the work the head has fallen behind by (e.g.
        a sibling's PR merged into the parent branch while this dev worked).
        ``ahead`` = commits on the head NOT on the base — the head's own work.
        Both are read from origin after a fetch, so they reflect the pushed
        state a merge / PR base would actually see.

        The submit-time behind-base gate (``i_am_done``) uses this: a non-zero
        ``behind`` means the branch fell behind its base and the dev must
        ``sync_branch`` before submitting, else the PR can't merge cleanly /
        a sibling's merged work is missing from this branch. Mirrors
        :meth:`sync_task_branch`'s workspace/token resolution. Raises on git
        failure (consistent with :meth:`rebase_onto_base`); the gate handler
        fail-opens on a raised error so a flaky fetch can't strand a task at
        the submit gate — the merge layer has its own behind checks.
        """
        if not task.branch_name:
            raise ValueError("is_behind_base requires a task with a branch_name")
        project = await self._project_for_task(task)
        if project is None:
            raise NotFoundError("Project for task", str(task.id))
        workspace_agent_id = self._resolve_workspace_agent_id(task, actor_agent_id)
        workspace = await self.get_workspace(project.slug, agent_id=workspace_agent_id)
        git_token = await self._get_project_token_or_raise(project.slug)
        await self._run_git(workspace, ["fetch", "origin"], token=git_token)
        # --left-right --count A...B → "<left> <right>": left = commits only in
        # A (base, what the head is BEHIND by); right = commits only in B (head,
        # the head's own ahead work). Triple-dot = symmetric difference.
        count = await self._run_git(
            workspace,
            [
                "rev-list",
                "--left-right",
                "--count",
                f"origin/{base_branch}...origin/{task.branch_name}",
            ],
        )
        left, _, right = count.stdout.strip().partition(" ")
        behind = int(left) if left.strip().isdigit() else 0
        ahead = int(right) if right.strip().isdigit() else 0
        return behind, ahead

    async def close_pull_request(
        self,
        pr_number: int,
        *,
        project_id: UUID,
        comment: str | None = None,
        delete_branch: bool = False,
        actor_agent_id: UUID | None = None,
    ) -> None:
        """Close PR ``pr_number`` on GitHub, optionally with an explanatory comment.

        Used to retire a PR whose work is already in the base (superseded) so a
        wedged task can complete without a merge — the "close the dead PR"
        action agents had no verb for. Branch deletion is opt-in
        (``delete_branch=False`` by default): a superseded PR's branch may still
        be referenced or useful for audit, so close does not destroy it unless
        the caller explicitly asks — matching the orchestrator supersede path.

        ``pr_number`` alone is ambiguous across projects (GitHub numbers PRs
        per-repo, but ``tasks.pr_number`` stores the bare integer with no repo
        scoping, so two tasks on different repos can share a number). The
        caller MUST pass the ``project_id`` the PR belongs to — the task
        lookup is scoped to it so a same-numbered PR in another project's repo
        is never resolved (and closed) by accident. Mirrors :meth:`pr_merge`.
        Idempotent: a PR that is already closed is a no-op (no duplicate
        comment), so a retried close-on-land never re-comments.
        """
        from sqlalchemy import select

        from roboco.db.tables import TaskTable as _TaskTable

        result = await self.session.execute(
            select(_TaskTable)
            .where(_TaskTable.pr_number == pr_number)
            .where(_TaskTable.project_id == project_id)
            .limit(1)
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

        headers = {
            "Authorization": f"Bearer {git_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        async with httpx.AsyncClient(timeout=_default_git_timeout()) as client:
            existing = await client.get(
                f"{_api_base()}/repos/{owner}/{repo}/pulls/{pr_number}",
                headers=headers,
            )
            already_closed = (
                existing.is_success and existing.json().get("state") == "closed"
            )
            if not already_closed:
                if comment:
                    await client.post(
                        f"{_api_base()}/repos/{owner}/{repo}/issues/"
                        f"{pr_number}/comments",
                        headers=headers,
                        json={"body": comment},
                    )
                resp = await client.patch(
                    f"{_api_base()}/repos/{owner}/{repo}/pulls/{pr_number}",
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
        project_id: UUID,
        actor_agent_id: UUID | None = None,
    ) -> str:
        """Return the current target (base) branch of an open PR.

        Workspace resolution mirrors pr_merge: actor → assigned_to → None
        (project.workspace_path as the final fallback). Lets the Main PM
        call pr_target after ``submit_qa`` has cleared ``assigned_to``
        without ValidationError.

        ``pr_number`` alone is ambiguous across projects (GitHub numbers PRs
        per-repo, but ``tasks.pr_number`` stores the bare integer with no repo
        scoping, so two tasks on different repos can share a number). The
        caller MUST pass the ``project_id`` the PR belongs to — the task lookup
        is scoped to it so a same-numbered PR in another project's repo is
        never resolved by accident. Mirrors :meth:`pr_merge`.
        """
        from sqlalchemy import select

        from roboco.db.tables import TaskTable as _TaskTable

        result = await self.session.execute(
            select(_TaskTable)
            .where(_TaskTable.pr_number == pr_number)
            .where(_TaskTable.project_id == project_id)
            .limit(1)
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
                    f"{_api_base()}/repos/{owner}/{repo}/pulls/{pr_number}",
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
        self,
        workspace: Any,
        branch_name: str,
        token: str | None = None,
        *,
        preferred_parent: str | None = None,
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

        ``preferred_parent``, when given, overrides the string-derived
        ``parent_branch_for`` with an authoritative parent branch name (e.g.
        ``merge_chain.resolve_parent_branch``, which reads the parent TASK's
        own ``branch_name`` — correct across a team boundary, unlike the
        derivation below which reuses ``branch_name``'s own team segment).
        Still falls back to the repo default branch when that parent was
        never pushed, so an unassembled/branchless parent can't crash the
        diff.
        """
        from roboco.services.gateway.merge_chain import parent_branch_for

        parent = (
            preferred_parent
            if preferred_parent is not None
            else parent_branch_for(branch_name)
        )
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
        origin_ref = f"origin/{branch_name}"
        local_exists = await self._ref_exists(workspace, branch_name)
        origin_exists = await self._ref_exists(workspace, origin_ref)
        if local_exists and origin_exists:
            # An assembled branch advances on ORIGIN when child PRs merge on
            # GitHub, while the inspecting clone's local ref stays parked — a
            # diff off the stale local ref re-flags work that already landed
            # (live 2026-07-02: two false pr_fails on the S6 cell PR). Prefer
            # origin when the local ref is strictly behind it; a local ref
            # that is ahead (unpushed) or diverged keeps priority.
            behind = await self._run_git(
                workspace,
                ["merge-base", "--is-ancestor", branch_name, origin_ref],
                check=False,
            )
            return origin_ref if behind.returncode == 0 else branch_name
        if local_exists:
            return branch_name
        if origin_exists:
            return origin_ref
        return branch_name

    async def diff(
        self,
        *,
        branch_name: str,
        base: str | None = None,
        actor_agent_id: UUID | None = None,
        preferred_parent: str | None = None,
    ) -> str:
        """Return the git diff for `branch_name` against `base`.

        When `base` is omitted, diffs against the branch's parent (per
        `parent_branch_for`), falling back to the repo default branch
        when that parent was never pushed. Content_actions
        evidence path can pass `HEAD~1` for an incremental diff.

        ``actor_agent_id`` resolves the workspace via the caller's clone
        when ``task.assigned_to`` is None — important for
        QA reviewing post-submit_qa.

        ``preferred_parent`` is ignored once ``base`` is explicit; it only
        overrides the derived-parent lookup (see ``_resolve_diff_base``) for
        a caller with an authoritative parent branch name (a cross-team
        assembled-PR review) — never a literal ref like ``base="HEAD~1"``.
        """
        workspace = await self._workspace_for_branch(
            branch_name, actor_agent_id=actor_agent_id
        )
        token = await self._token_for_branch(branch_name)
        head_ref = await self._resolve_head_ref(workspace, branch_name, token=token)
        base_ref = (
            base
            if base is not None
            else await self._resolve_diff_base(
                workspace, branch_name, token=token, preferred_parent=preferred_parent
            )
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
        preferred_parent: str | None = None,
    ) -> list[str]:
        """Return the file paths changed on `branch_name` relative to `base`.

        Mirrors ``diff`` but invokes ``git diff --name-only`` so the
        gateway evidence path can populate ``files_changed`` from the
        authoritative git state — independent of whether the agent
        ever called the legacy ``add_files_modified`` HTTP endpoint
        (which the gateway commit() does not call). Empty paths are
        skipped; output preserves git's order. Same default-
        branch fallback as ``diff`` (including ``preferred_parent``).
        """
        workspace = await self._workspace_for_branch(
            branch_name, actor_agent_id=actor_agent_id
        )
        token = await self._token_for_branch(branch_name)
        head_ref = await self._resolve_head_ref(workspace, branch_name, token=token)
        base_ref = (
            base
            if base is not None
            else await self._resolve_diff_base(
                workspace, branch_name, token=token, preferred_parent=preferred_parent
            )
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
        clone_root = await self._workspace_for_branch(
            branch_name, actor_agent_id=actor_agent_id
        )
        # Commit inside the task's per-task worktree (F123), not the shared
        # clone — keyed by the task id so it matches create_branch's layout.
        workspace = self._worktree_for_task(clone_root, task_id)
        await self._ensure_worktree_for_commit(clone_root, workspace, branch_name)
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
        self,
        actor_agent_id: UUID | None,
        task: Any,
        *,
        preferred_parent: str | None = None,
    ) -> dict[str, Any]:
        """Run the conventions validator on a task's changed files.

        Resolves the acting agent's workspace + the branch's changed files and
        runs ``python -m roboco.conventions`` over them. A resolution error
        (workspace missing, diff failed) fails CLOSED — returns
        ``could_not_run=True`` so the block gate refuses the submit instead of
        silently passing on an unanalyzable diff (the validator's OWN fail-loud
        exit-3 philosophy). The two empty-result paths stay fail-open: a
        branchless task (no ``branch_name``) and a task with no changed files
        genuinely have nothing to validate, so the gate correctly passes.

        ``preferred_parent`` threads to ``list_changed_files`` — the in-path
        PR-review gate's cross-team parent (see ``diff``'s docstring).
        """
        try:
            branch = task.branch_name
            if not branch:
                return {"findings": [], "could_not_run": False}
            clone_root = await self._workspace_for_branch(
                branch, actor_agent_id=actor_agent_id
            )
            changed = await self.list_changed_files(
                branch_name=branch,
                actor_agent_id=actor_agent_id,
                preferred_parent=preferred_parent,
            )
        except Exception as exc:
            return {
                "findings": [],
                "could_not_run": True,
                "reason": f"resolution failed: {exc}"[:300],
            }
        if not changed:
            return {"findings": [], "could_not_run": False}
        # Validate the worktree's working tree (F123): the dev's changes live in
        # the per-task worktree, not the clone root (which sits on the default
        # branch). A validator run against the clone root reads stale/default
        # content and false-passes on newly-added files.
        workspace = self._worktree_for_task(clone_root, require_uuid(task.id))
        await self._ensure_worktree_for_commit(clone_root, workspace, branch)
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
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(),
                timeout=_CONVENTIONS_VALIDATOR_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            # Fail closed (could_not_run=True → block gate refuses the submit),
            # matching the validator's own fail-loud philosophy, and reap the
            # killed proc so it isn't orphaned on orchestrator restart.
            proc.kill()
            await proc.wait()
            return {
                "findings": [],
                "could_not_run": True,
                "reason": (
                    f"validator timed out after "
                    f"{_CONVENTIONS_VALIDATOR_TIMEOUT_SECONDS}s"
                ),
            }
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
        ws = workspace
        if ws is None and project.workspace_path:
            # workspace_path is API-settable (PM-gated route): only a path
            # inside THIS project's workspace tree may receive the scaffold
            # commit — anything else (arbitrary dir, another project's clone)
            # is refused as "no usable workspace".
            candidate = Path(project.workspace_path)
            try:
                candidate.resolve().relative_to(
                    Path(settings.workspaces_root) / project.slug
                )
                ws = candidate
            except (ValueError, OSError):
                ws = None
        if ws is None or not ws.exists():
            return None
        base = head_branch(project)
        spec = _ConventionsPr(
            content=content,
            branch=CONVENTIONS_SCAFFOLD_BRANCH,
            title=title,
            body=body,
        )
        original = await self.get_current_branch(ws)
        # A dirty tree is an agent's active workspace. Cutting the scaffold
        # branch here would sweep their uncommitted work into the conventions
        # commit — ``checkout <base>`` no-ops or is refused, ``checkout -B
        # <scaffold>`` carries the dirty change, and ``commit`` captures it,
        # so the agent's in-progress edit rides a project-level PR they never
        # intended and vanishes from their working tree. Refuse outright before
        # any checkout touches the tree.
        if not await self._working_tree_is_clean(ws):
            return None
        try:
            if not await self._commit_conventions_file(ws, base, spec):
                return None
            return await self._push_and_open_conventions_pr(
                project_slug, ws, base, spec
            )
        finally:
            await self._run_git(ws, ["checkout", original], check=False)

    async def _working_tree_is_clean(self, workspace: Path) -> bool:
        """True iff ``git status --porcelain`` is empty (no staged/unstaged
        changes, no untracked entries). Used to gate workspace-mutating
        project-level ops (the conventions scaffold) away from an agent's
        active dirty tree."""
        result = await self._run_git(workspace, ["status", "--porcelain"])
        return not result.stdout.strip()

    async def _commit_conventions_file(
        self, workspace: Path, base: str, spec: _ConventionsPr
    ) -> bool:
        """Commit the conventions file on the scaffold branch cut from ``base``.

        Returns False when the scaffold cannot be safely cut from ``base`` (the
        ``checkout <base>`` with ``check=False`` failed — a missing base ref,
        or a dirty tree that refused the switch). In that case ``checkout -B
        <scaffold>`` would cut the scaffold from the *current* branch (the
        agent's task branch), basing a project-level PR on the agent's work;
        the caller refuses rather than commit on the wrong base.
        """
        await self._run_git(workspace, ["checkout", base], check=False)
        # check=False swallows the failed checkout; verify we actually landed
        # on base before cutting the scaffold from here.
        if await self.get_current_branch(workspace) != base:
            return False
        await self._run_git(workspace, ["checkout", "-B", spec.branch])
        target = workspace / ".roboco" / "conventions.yml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(spec.content)
        await self._run_git(workspace, ["add", ".roboco/conventions.yml"])
        await self._run_git(workspace, ["commit", "-m", spec.title])
        return True

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
            await self.push(workspace, force=True)
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
        pr_number = data.get("number")
        if pr_number is not None:
            # Static label — a project-level scaffold/restore PR has no task or
            # org layer; best-effort, never blocks.
            await self._apply_pr_labels(
                owner, repo, token, int(pr_number), CONVENTIONS_PR_LABELS
            )
        return {
            "branch": spec.branch,
            "pr_number": pr_number,
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
