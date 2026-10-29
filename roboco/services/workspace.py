"""
Workspace Service

Manages multi-agent workspaces for git operations.

Each agent gets their own workspace (git clone) for a project, allowing
parallel development without conflicts:

    /data/workspaces/
    └── {project-slug}/
        └── {team}/
            └── {agent-slug}/
                └── [git repo files]

Example:
    /data/workspaces/roboco-api/backend/be-dev-1/
    /data/workspaces/roboco-api/backend/be-dev-2/
    /data/workspaces/roboco-api/frontend/fe-dev-1/
"""

import asyncio
import contextlib
import json
import math
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from roboco.config import settings
from roboco.db.tables import AgentTable
from roboco.exceptions import GitTimeoutError
from roboco.logging import get_logger
from roboco.models.base import Team
from roboco.models.env_branches import head_branch
from roboco.services.git_origin import OriginContext, run_origin_command
from roboco.services.git_relay import (
    GitRelay,
    is_dispatcher_owned,
    read_clone_path,
    relay_for,
    run_private_git,
)
from roboco.services.toolchain import resolve_target_python
from roboco.services.workspace_container import (
    WorkspaceContainerError,
    run_in_workspace_container,
)
from roboco.services.workspace_git import (
    UnsafeWorkspaceGitConfigError,
    prepare_clone_parent,
    read_workspace_file,
    remove_workspace_tree,
    run_as_workspace_user,
    run_workspace_git,
    write_workspace_file,
)
from roboco.utils.workspace_paths import clone_root_of as _resolve_clone_root
from roboco.utils.workspace_paths import worktree_path_of

logger = get_logger(__name__)

# Lockfile paths the dep-update probe inspects when a project sets no explicit
# dep_update_paths — the two RoboCo's stack uses.
_DEP_LOCK_DEFAULTS = ("uv.lock", "pnpm-lock.yaml")


_REFS_PRUNE_TIMEOUT_SECONDS = 60

# Run by the workspace git user (stdlib only): prints the sha256 over each
# present lockfile's name + bytes, or nothing when none is present.
_LOCKFILE_DIGEST_SCRIPT = """
import hashlib, os, sys
digest = hashlib.sha256()
found = False
for name in ("uv.lock", "pnpm-lock.yaml", "package-lock.json", "package.json"):
    path = os.path.join(sys.argv[1], name)
    if not os.path.isfile(path):
        continue
    found = True
    with open(path, "rb") as handle:
        digest.update(name.encode())
        digest.update(handle.read())
if found:
    print(digest.hexdigest())
"""

# Run by the workspace git user (stdlib only, `python -I -S`): argv[1] is the
# refs dir. A healthy loose ref holds an object id (sha1 = 40 hex, sha256 = 64
# hex) or a symbolic ref ("ref: refs/..."); anything else is debris left by
# interrupted recovery. os.walk never descends a symlinked dir; is_file() skips links to
# anything the git user cannot see anyway.
_PRUNE_BROKEN_REFS_SCRIPT = """
import os, re, sys
OK = re.compile(r"\\A[0-9a-f]{40}\\Z|\\A[0-9a-f]{64}\\Z")
for parent, _dirs, files in os.walk(sys.argv[1]):
    for name in files:
        path = os.path.join(parent, name)
        if os.path.islink(path):
            continue
        if name.endswith(".bak"):
            os.unlink(path)
            continue
        with open(path, encoding="utf-8", errors="replace") as handle:
            content = handle.read().strip()
        if not (OK.match(content) or content.startswith("ref: ")):
            os.unlink(path)
"""


def _uv_container_env(workspace: Path) -> dict[str, str]:
    """Env a uv command gets in the throwaway workspace container.

    Pins ``UV_PYTHON_INSTALL_DIR`` to ``<clone_root>/.uv-python`` so a non-system
    Python (e.g. 3.14) uv fetches lands INSIDE the workspace bind mount, where
    the agent container (same uid) can traverse it: the workspace
    ``.venv/bin/python`` symlinks into it. Without this every ``uv run`` died on
    ``Permission denied`` canonicalizing the venv symlink (live be-dev-1
    brick). Per-workspace → per-project isolation intact.

    Worktree-aware (F123): when the CWD is a per-task worktree, resolve up to the
    clone root so the shared ``.uv-python`` is reused instead of re-fetching a
    managed CPython per worktree. The image's own ``/app/.venv`` pins are
    dropped by the container's entry script.
    """
    return {"UV_PYTHON_INSTALL_DIR": str(_resolve_clone_root(workspace) / ".uv-python")}


# Thin wrapper around time.monotonic so tests can patch _monotonic without
# affecting asyncio's own use of time.monotonic (which runs during event-loop
# teardown and would exhaust a side_effect iterator if patched directly).
def _monotonic() -> float:
    return time.monotonic()


# Per (project_slug, agent_slug) async lock to serialize concurrent
# ensure_workspace calls in the same orchestrator process. Prevents two
# coroutines from both passing the ".git exists?" check and then both
# trying to clone into the same directory.
_ENSURE_WORKSPACE_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}

# Process-level guard so the conventions scaffold (opened on a project's first
# clone) is attempted at most once per project per process, even across agents.
_SCAFFOLD_ATTEMPTED: set[str] = set()

# Project-level read clones (the conventions standard) refresh at most this often
# on a healthy clone, so a burst of spawns / task creations doesn't fetch on
# every call. Keyed by workspace path; process-wide.
_READ_CLONE_FETCH_TTL_SECONDS = 30.0
_read_clone_synced: dict[str, float] = {}


def _ensure_lock_for(project_slug: str, agent_slug: str) -> asyncio.Lock:
    """Return the asyncio.Lock for a (project, agent) pair, creating lazily."""
    key = (project_slug, agent_slug)
    lock = _ENSURE_WORKSPACE_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _ENSURE_WORKSPACE_LOCKS[key] = lock
    return lock


class WorkspaceError(Exception):
    """Raised when workspace operations fail."""

    pass


def _safe_segment(value: str, label: str) -> str:
    """Return ``value`` if safe as one path segment under the workspace root.

    Slugs/teams are regex- or enum-validated at creation, but every workspace
    path is built here — reject traversal vectors (empty, ``.``/``..``,
    separators, NUL) at the chokepoint so the tree is safe by construction.
    """
    if (
        not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        raise WorkspaceError(f"unsafe {label} for a workspace path: {value!r}")
    return value


# Marker file recording the lockfile digest the dev-deps install last ran
# against. Lives under .git/ so it never shows up in `git status` (the agent's
# clean-tree checks would otherwise trip on it) and is wiped with the clone.
_DEP_INSTALL_MARKER = ".git/.roboco-dep-install"
# Records the interpreter the workspace was provisioned with + whether the
# project's suite can run there. Lives inside .git/ so it never lands in the
# target repo's tracked tree. JSON: {"python": "3.14", "status": "ok"}.
_TOOLCHAIN_MARKER = ".git/.roboco-toolchain"

# pytest exit codes the runnability smoke interprets. A collection error (2) is
# the interpreter-mismatch signature (imports fail under the wrong Python); 0/5
# mean the suite is runnable; anything else is inconclusive (never 'broken').
_PYTEST_OK = 0
_PYTEST_NO_TESTS_COLLECTED = 5
_PYTEST_COLLECTION_ERROR = 2


def _give_scratch_tree_to_agent_uid(root: Path) -> None:
    """Chown a scratch tree this process just built to the agent uid/gid.

    Only for trees under ``private_data_root``, which no agent container
    mounts: nothing in them can be swapped for a symlink, but ``lchown`` and
    ``followlinks=False`` keep that true even for links the clone itself
    carries. An unprivileged process already owns what it built.
    """
    if os.geteuid() != 0:
        return
    uid, gid = settings.agent_uid, settings.agent_gid
    os.lchown(root, uid, gid)
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in (*dirs, *files):
            os.lchown(Path(parent) / name, uid, gid)


def _lockfile_digest(workspace: Path) -> str | None:
    """Hash the dependency lockfiles present in the workspace.

    Returns a stable digest over whichever of `uv.lock` / `pnpm-lock.yaml` /
    `package-lock.json` / `package.json` exist, or None when none do (nothing
    to install). Used to make the post-clone install idempotent: if the
    digest matches the marker from the previous run, the install is skipped.
    Read by the workspace git user in one process: as root, a lockfile
    swapped for a symlink would be read from anywhere.
    """
    result = run_as_workspace_user(
        sys.executable,
        "-I",
        "-S",
        "-c",
        _LOCKFILE_DIGEST_SCRIPT,
        str(workspace),
        timeout=_REFS_PRUNE_TIMEOUT_SECONDS,
    )
    digest = result.stdout.strip()
    return digest if result.returncode == 0 and digest else None


def _detect_dep_commands(
    workspace: Path, target_python: str | None = None
) -> list[tuple[str, list[str]]]:
    """Return the dev-dependency install commands for this workspace.

    When ``target_python`` is given (toolchain matching enabled + the target
    declares a version), the Python ``uv sync`` is pinned to that interpreter
    via ``--python`` so uv fetches + uses it instead of the system 3.13.

    Detects project ecosystems by lockfile/manifest and returns
    ``(label, argv)`` tuples to run from the workspace root:

    - Python: `pyproject.toml` → ``uv sync --extra dev`` (installs the project
      plus its ``dev`` extra into a `.venv` next to the project, giving the
      agent its own ruff/mypy/xenon/pytest for the `make quality` gate). Plain
      ``uv sync`` would install only the default dependency group, leaving the
      lint/type/complexity tools — which live in the ``dev`` *extra* — absent,
      so `make quality` dies on ``ruff: command not found`` and the agent
      cannot gate its own work.
    - Node/TS: `pnpm-lock.yaml` → ``pnpm install``;
      ``package-lock.json`` → ``npm ci``; bare `package.json` → ``npm install``.

    A monorepo with both gets both commands. Empty list means nothing to do.
    """
    commands: list[tuple[str, list[str]]] = []

    if (workspace / "pyproject.toml").is_file():
        argv = ["uv", "sync", "--extra", "dev"]
        if target_python:
            argv += ["--python", target_python]
        commands.append(("uv sync --extra dev", argv))

    if (workspace / "pnpm-lock.yaml").is_file():
        commands.append(("pnpm install", ["pnpm", "install", "--frozen-lockfile"]))
    elif (workspace / "package-lock.json").is_file():
        commands.append(("npm ci", ["npm", "ci"]))
    elif (workspace / "package.json").is_file():
        commands.append(("npm install", ["npm", "install"]))

    return commands


class WorkspaceService:
    """
    Service for managing agent workspaces.

    Workspaces follow the structure:
        {workspaces_root}/{project_slug}/{team}/{agent_slug}/

    This allows:
    - Multiple agents to work on the same project in parallel
    - Each agent has their own git working tree
    - Agents can be on different branches simultaneously
    - No file locking conflicts between agents
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.root = Path(settings.workspaces_root)
        # TTL cache for refresh fetches. Dogfooding fired 9 refresh-fetch
        # warnings per run because each evidence() call triggered
        # ensure_workspace → fetch. The workspace doesn't
        # change in subseconds. 30s TTL eliminates the noise without
        # compromising freshness (commits land slower than 30s in practice;
        # force=True override exists for the rare need-fresh case).
        self._fetch_cache: dict[str, float] = {}

    def get_workspace_path(
        self,
        project_slug: str,
        team: Team | str,
        agent_slug: str,
    ) -> Path:
        """
        Compute the workspace path for an agent on a project.

        Args:
            project_slug: Project identifier (e.g., 'roboco')
            team: Agent's team (e.g., Team.BACKEND or 'backend')
            agent_slug: Agent identifier (e.g., 'be-dev-1')

        Returns:
            Path to the workspace directory

        Raises:
            WorkspaceError: If team is None (would produce a literal
                "None" segment otherwise — see agents_config.AGENT_TEAM_MAP
                for the canonical team for each agent).

        Example:
            >>> get_workspace_path('roboco', Team.BACKEND, 'be-dev-1')
            Path('/data/workspaces/roboco/backend/be-dev-1')
        """
        if team is None:
            raise WorkspaceError(
                f"Cannot resolve workspace path for {agent_slug}: team is None. "
                "Add the agent to AGENT_TEAM_MAP in roboco/agents_config.py."
            )
        team_str = team.value if isinstance(team, Team) else str(team)
        path = os.path.normpath(
            self.root
            / _safe_segment(project_slug, "project slug")
            / _safe_segment(team_str, "team")
            / _safe_segment(agent_slug, "agent slug")
        )
        # Containment on the normalized path, on top of the per-segment
        # checks: the one test every later file and git operation relies on.
        if not path.startswith(f"{os.path.normpath(self.root)}{os.sep}"):
            raise WorkspaceError(f"workspace path {path!r} is outside {self.root}")
        return Path(path)

    def get_clone_root_path(
        self,
        project_slug: str,
        team: Team | str,
        agent_slug: str,
    ) -> Path:
        """The persistent clone root for an agent on a project.

        Same path as ``get_workspace_path`` (the real ``.git`` object store +
        shared ``.venv`` / ``.uv-python`` live here). Named separately so the
        worktree code can express clone-root vs per-task-worktree intent.
        """
        return self.get_workspace_path(project_slug, team, agent_slug)

    def get_worktree_path(
        self,
        project_slug: str,
        team: Team | str,
        agent_slug: str,
        task_short_id: str,
    ) -> Path:
        """Per-task working tree: ``{clone_root}/.worktrees/{task_short_id}``.

        Each task/branch gets its own checkout via ``git worktree add`` so a
        coordinator PM holding multiple in_progress roots never clobbers one
        root's working tree by checking out another's branch (F123). The clone
        root (object store + venv) is shared underneath.
        """
        if not task_short_id:
            raise WorkspaceError(
                f"Cannot resolve worktree path for {agent_slug}: "
                "task_short_id is empty."
            )
        clone_root = self.get_clone_root_path(project_slug, team, agent_slug)
        try:
            return worktree_path_of(clone_root, task_short_id)
        except ValueError as exc:
            raise WorkspaceError(str(exc)) from exc

    @staticmethod
    def _worktree_git(
        clone_root: Path, args: list[str], check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        """Run ``git <args>`` in a clone or worktree as the workspace git user
        (sync, never a token — internal worktree plumbing only).

        ``check`` raises ``CalledProcessError``; a clone whose config could
        make git run a program raises ``UnsafeWorkspaceGitConfigError``.
        """
        return run_workspace_git(
            clone_root,
            args,
            timeout=settings.git_command_timeout_seconds,
            check=check,
        )

    @staticmethod
    def _clone_root_default_branch(clone_root: Path) -> str:
        # origin/HEAD points at the repo's default branch (set on clone).
        # Returns the bare branch name ("main"), or "" when unresolvable (no
        # remote, e.g. a test/local clone).
        res = WorkspaceService._worktree_git(
            clone_root,
            ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
            check=False,
        )
        if res.returncode != 0:
            return ""
        val = res.stdout.strip()
        return val.split("/", 1)[1] if val.startswith("origin/") else val

    @staticmethod
    def _park_clone_root_off_branch(clone_root: Path, branch: str) -> None:
        """Restore the F123 invariant before ``git worktree add <branch>``.

        The clone root parks on the default branch (or detached); the task
        branch lives in the worktree. A re-dispatch after the clone root drifted
        onto the task branch (a pre-F123 leftover / a missed checkout path) makes
        ``worktree add <branch>`` fatal ("already checked out at '<clone_root>'"),
        releasing the claim and re-dispatching into the same collision every
        tick. If the clone root's HEAD is the task branch, move it back to the
        default branch (via origin/HEAD); if that is unresolvable or the checkout
        is blocked, detach HEAD at the same commit — either frees the branch ref
        for the worktree. The clone root never carries task work under F123, so
        nothing is lost.
        """
        cur = WorkspaceService._worktree_git(
            clone_root, ["branch", "--show-current"], check=False
        )
        if cur.returncode != 0 or cur.stdout.strip() != branch:
            return
        default = WorkspaceService._clone_root_default_branch(clone_root)
        if default:
            moved = WorkspaceService._worktree_git(
                clone_root, ["checkout", default], check=False
            )
            if moved.returncode == 0:
                return
        # No resolvable default, or checkout blocked by a dirty tree: detach
        # at the same commit (no working-tree change) so the branch ref is free.
        WorkspaceService._worktree_git(
            clone_root, ["checkout", "--detach"], check=False
        )

    @staticmethod
    def _link_shared_venv(worktree: Path, clone_root: Path) -> None:
        """Symlink ``worktree/.venv -> ../../.venv`` (the clone-root venv).

        uv discovers ``.venv`` next to the worktree's ``pyproject.toml``; without
        the symlink it re-syncs a fresh venv per worktree. The relative target
        holds because every worktree sits at ``{clone_root}/.worktrees/{id}``
        (two levels deep). Idempotent: leaves an existing symlink/dir alone.
        Only links once the clone-root venv exists — otherwise the symlink
        dangles and uv errors or re-syncs a worktree-local venv that the
        lexists guard then can't replace. install_dev_deps provisions
        clone_root/.venv before the first worktree add on the fresh-claim path,
        so a later ensure (resume) self-heals the link.
        """
        link = worktree / ".venv"
        if os.path.lexists(link):
            return
        if not (clone_root / ".venv").exists():
            return
        # As the git user: the worktree is agent-writable, so a root mkdir or
        # symlink here could be steered anywhere by a planted link.
        run_as_workspace_user(
            "sh",
            "-c",
            'mkdir -p -- "$1" && ln -s ../../.venv "$1/.venv"',
            "sh",
            str(worktree),
            timeout=settings.git_command_timeout_seconds,
        )

    async def ensure_worktree(
        self, clone_root: Path, worktree: Path, branch: str, base: str
    ) -> None:
        """Create the per-task linked worktree on ``branch`` from ``base``.

        Idempotent: a present, registered worktree is left in place (re-claim,
        re-spawn). A new branch uses ``git worktree add -b <branch> <base>``; an
        already-existing branch (re-claim after rollback) reuses it with
        ``worktree add <branch>``. Then symlinks the shared clone-root venv.
        F123: replaces the shared-clone ``reset --hard`` + ``checkout -b`` that
        clobbered a still-active root.
        """
        if not (worktree.exists() and (worktree / ".git").is_file()):
            # Stale registrations (checkout dir deleted out-of-band while the
            # admin dir survives) make `worktree add` fatal — prune first.
            self._worktree_git(clone_root, ["worktree", "prune"], check=False)
            self._park_clone_root_off_branch(clone_root, branch)
            branch_exists = (
                self._worktree_git(
                    clone_root,
                    ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
                    check=False,
                ).returncode
                == 0
            )
            if branch_exists:
                add_args = ["worktree", "add", str(worktree), branch]
            else:
                add_args = ["worktree", "add", str(worktree), "-b", branch, base]
            res = self._worktree_git(clone_root, add_args, check=False)
            if res.returncode != 0:
                raise WorkspaceError(
                    f"git worktree add failed for {branch}: {res.stderr.strip()}"
                )
        self._link_shared_venv(worktree, clone_root)

    async def ensure_worktree_for_resume(
        self,
        clone_root: Path,
        worktree: Path,
        branch: str,
        project_slug: str | None = None,
    ) -> None:
        """Re-add a pruned/evicted worktree on resume (no ``-b`` — branch exists).

        Committed work survives in the branch ref; only the working tree was
        removed (reaper / cancel / disk pressure). Idempotent: a present
        worktree is a no-op. A missing local ``refs/heads/{branch}`` (cleaned
        up while the worktree was evicted) is recovered from ``origin`` first
        (token-aware when ``project_slug`` resolves), never fatals with
        "invalid reference". ``project_slug`` is optional: callers that know
        it get an authenticated fetch; the rest fall back to unauthenticated.
        """
        if not (worktree.exists() and (worktree / ".git").is_file()):
            # Stale registrations (checkout dir deleted out-of-band while the
            # admin dir survives) make `worktree add` fatal — prune first.
            self._worktree_git(clone_root, ["worktree", "prune"], check=False)
            self._park_clone_root_off_branch(clone_root, branch)
            add_args = ["worktree", "add", str(worktree), branch]
            local = self._worktree_git(
                clone_root,
                ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
                check=False,
            )
            if local.returncode != 0:
                # Live wedge (2026-08-31): a commit/claim-time re-add hit a
                # clone whose local branch ref was already deleted (`worktree
                # add` fataled "invalid reference"; the fresh-claim path
                # recreates refs, this path never did). Recover the SAME way
                # ensure_worktree_self_heal does: fetch + create the local ref
                # from origin/<branch>; a never-pushed branch falls back to -b
                # from origin/HEAD (no pushed work to lose).
                await self._fetch_branch_ref(clone_root, branch, project_slug)
                remote = self._worktree_git(
                    clone_root,
                    [
                        "rev-parse",
                        "--verify",
                        "--quiet",
                        f"refs/remotes/origin/{branch}",
                    ],
                    check=False,
                )
                if remote.returncode == 0:
                    self._worktree_git(
                        clone_root,
                        ["branch", branch, f"refs/remotes/origin/{branch}"],
                        check=False,
                    )
                else:
                    add_args = [
                        "worktree",
                        "add",
                        str(worktree),
                        "-b",
                        branch,
                        "origin/HEAD",
                    ]
            res = self._worktree_git(clone_root, add_args, check=False)
            if res.returncode != 0:
                raise WorkspaceError(
                    f"git worktree re-add failed for {branch}: {res.stderr.strip()}"
                )
        self._link_shared_venv(worktree, clone_root)

    async def _fetch_branch_ref(
        self, clone_root: Path, branch: str, project_slug: str | None
    ) -> None:
        """``git fetch origin <branch>`` into clone_root, via the relay.
        Best-effort.

        Never raises — a failed fetch (unknown project, branch not on origin,
        network) falls through to ``ensure_worktree``'s ``-b`` from
        ``origin/HEAD``.
        """
        if project_slug is None:
            return
        result = await self._origin_git(
            clone_root,
            project_slug,
            ["fetch", "--no-tags", "origin", branch],
            timeout=settings.workspace_refresh_fetch_timeout_seconds,
        )
        if result.returncode != 0:
            logger.warning(
                "ensure_worktree_self_heal: branch fetch returned non-zero",
                branch=branch,
                clone_root=str(clone_root),
                stderr=result.stderr.strip(),
            )

    async def _origin_git(
        self, repo: Path, project_slug: str, args: list[str], *, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        """Serve an ``origin`` git command for ``repo`` through the project's
        relay (``roboco.services.git_origin``): the token never enters a
        process that touches an agent-writable repository. Never raises."""
        relay = await self._relay_for(project_slug)
        if relay is None:
            return subprocess.CompletedProcess(
                args, 128, "", f"fatal: unknown project {project_slug!r}\n"
            )
        owned = is_dispatcher_owned(repo)

        def _local(local_args: Sequence[str]) -> subprocess.CompletedProcess[str]:
            runner = run_private_git if owned else run_workspace_git
            return runner(repo, local_args, timeout=timeout, check=False)

        ctx = OriginContext(
            relay=relay,
            repo=repo,
            local_git=_local,
            from_workspace=not owned,
            timeout=timeout,
        )
        try:
            return await asyncio.to_thread(run_origin_command, ctx, args)
        except (subprocess.SubprocessError, OSError, ValueError) as exc:
            return subprocess.CompletedProcess(args, 128, "", f"{exc}\n")

    async def _relay_for(self, project_slug: str) -> GitRelay | None:
        """The project's relay, from its database row; None if unknown."""
        from roboco.services.project import get_project_service

        project_service = get_project_service(self.session)
        project = await project_service.get_by_slug(project_slug)
        if project is None or not project.git_url:
            return None
        token = await self._read_clone_token(project_service, project_slug)
        return relay_for(project, token)

    async def ensure_worktree_self_heal(
        self,
        clone_root: Path,
        worktree: Path,
        branch: str,
        project_slug: str,
        *,
        can_author: bool,
    ) -> None:
        """Re-attach a per-task worktree, self-healing a vanished clone + ref.

        F123 spawn-time chokepoint. A resumed agent's clone can vanish (disk
        loss, a redeploy that wiped ``/data/workspaces``, manual cleanup) while
        the task's DB row keeps ``branch_name`` and the reaper-style claim
        release preserves ownership — so the next dispatch is a RESUME, not a
        fresh claim, and ``create_branch`` never re-runs to re-clone.
        ``ensure_worktree_for_resume`` then runs ``git -C <clone_root>`` against
        a missing directory and fatal-loops (claim released -> re-dispatched
        into the same missing clone -> repeat, every tick).

        The orchestrator calls ``ensure_workspace`` first when the clone is
        unhealthy (re-clone from default). This then recovers the task branch
        ref so the worktree re-attaches with the pushed work intact:

        1. A present, registered worktree is refreshed against origin — see
           :meth:`_refresh_present_worktree` — then venv-linked + chowned.
           Without this a worktree created once (a reviewer's first
           ``claim_review``) stayed frozen at that commit across every
           respawn even as new commits landed on origin (a QA/PR-gate round
           2+ reviewing its own stale round-1 checkout).
        2. If the local ``refs/heads/{branch}`` ref is absent (a re-clone has
           none), fetch ``origin <branch>`` (token-aware) and create the local
           ref from ``refs/remotes/origin/{branch}`` when origin has it —
           recovering the pushed commits. (``create_branch`` always pushes at
           claim time, so a claimed task's branch is on origin.) ``ensure_worktree``
           then reuses the recovered ref, or — if origin doesn't have it (a
           never-pushed branch) — re-creates it from ``origin/HEAD``; no pushed
           work is lost because none existed.
        3. If the local ref instead SURVIVED (the worktree itself was pruned —
           disk pressure, manual cleanup, a reaper evict — while the branch ref
           lived on), re-add via ``ensure_worktree`` and then run it through the
           SAME :meth:`_refresh_present_worktree` classification as a present
           worktree. Re-adding alone would resurrect whatever commit the local
           ref happened to point at, however far origin moved since (a
           reviewer's round-1 worktree evicted, then re-added by a round-2
           respawn into its own stale round-1 checkout — the present-worktree
           bug's absent-worktree twin).

        A transient fetch failure falls through to the ``origin/HEAD`` ``-b``
        rather than fatal-looping; a diverged author-role branch re-syncs on
        the agent's first ``sync_branch``.

        ``can_author`` (see ``foundation.identity.WORKTREE_AUTHOR_ROLES``) is
        the caller's role classification, not a task property — the SAME
        branch is a developer's own worktree in the dev's clone and a pure
        reader's worktree in QA's/the reviewer's own separate clone.
        """
        if worktree.exists() and (worktree / ".git").is_file():
            await self._refresh_present_worktree(
                clone_root, worktree, branch, project_slug, can_author=can_author
            )
            self._link_shared_venv(worktree, clone_root)
            return
        local = self._worktree_git(
            clone_root,
            ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
            check=False,
        )
        if local.returncode != 0:
            await self._fetch_branch_ref(clone_root, branch, project_slug)
            remote = self._worktree_git(
                clone_root,
                ["rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}"],
                check=False,
            )
            if remote.returncode == 0:
                self._worktree_git(
                    clone_root,
                    ["branch", branch, f"refs/remotes/origin/{branch}"],
                    check=False,
                )
            # Reuse refs/heads/{branch} if recovered; else -b from origin/HEAD.
            await self.ensure_worktree(clone_root, worktree, branch, "origin/HEAD")
            return
        # A surviving local ref means the WORKTREE was pruned, not that its
        # would-be checkout is current — re-add, then classify it exactly like
        # a present worktree instead of trusting the ref's stale commit.
        await self.ensure_worktree(clone_root, worktree, branch, "origin/HEAD")
        await self._refresh_present_worktree(
            clone_root, worktree, branch, project_slug, can_author=can_author
        )
        self._link_shared_venv(worktree, clone_root)

    @staticmethod
    def _worktree_is_dirty(worktree: Path) -> bool:
        """True iff the worktree has staged/unstaged/untracked changes.

        A failed ``git status`` (nonzero returncode, empty stdout) fails
        toward DIRTY, never clean — the caller uses this to decide whether an
        author's uncommitted edits are safe to reset out from under, and a
        false "clean" would discard them.
        """
        status = WorkspaceService._worktree_git(
            worktree, ["status", "--porcelain"], check=False
        )
        return status.returncode != 0 or bool(status.stdout.strip())

    @staticmethod
    def _worktree_on_task_branch(worktree: Path, branch: str) -> bool:
        """True iff the worktree's checked-out branch IS ``branch``.

        Guards every ``reset --hard origin/<branch>`` below: a detached HEAD
        (mid-rebase, a crashed checkout) or a worktree drifted onto a
        different ref must never have ``branch``'s ref reset out from under
        it.
        """
        current = WorkspaceService._worktree_git(
            worktree, ["branch", "--show-current"], check=False
        )
        return current.returncode == 0 and current.stdout.strip() == branch

    @classmethod
    def _safe_to_reset(cls, worktree: Path, branch: str) -> bool:
        if cls._worktree_on_task_branch(worktree, branch):
            return True
        logger.warning(
            "ensure_worktree_self_heal: worktree not on its task branch, "
            "skipping reset",
            worktree=str(worktree),
            branch=branch,
        )
        return False

    async def _refresh_present_worktree(
        self,
        clone_root: Path,
        worktree: Path,
        branch: str,
        project_slug: str,
        *,
        can_author: bool,
    ) -> None:
        """Re-sync an ALREADY-PRESENT worktree with origin before a respawn
        reuses it.

        A per-task worktree is created once (first claim, or a reviewer's
        first ``claim_review``/``claim_gate_review``) and, before this, never
        touched again — every later respawn saw whatever commit happened to
        be checked out at creation time, however far origin moved since (a
        QA/PR-gate reviewer bounced a task back for fixes, the dev pushed —
        routinely a force-pushed rebase — then respawned the reviewer into
        its OWN still-round-1 checkout: the root mechanism behind a live
        multi-round QA bounce loop).

        Best-effort throughout — an offline spawn must not break: a failed
        fetch or an unresolvable ``origin/<branch>`` leaves the worktree
        exactly as it was (the absent-worktree path already falls back to
        ``origin/HEAD`` in that case; a present one has nothing better to do
        than keep what it has).

        Classifies local HEAD (normally ``branch`` — the worktree is meant to
        be checked out on it) against ``origin/<branch>``:
          - behind-or-equal (origin has every local commit): fast-forward to
            origin. Safe content-wise for every role — nothing local is
            unique — but an author-capable role's UNCOMMITTED edits are never
            discarded to get there; a dirty author tree is left alone
            entirely rather than resetting under it.
          - strictly ahead (local has commits origin lacks): always leave
            alone — real unpushed work, author or not.
          - diverged: an author-capable role keeps its own history —
            ``sync_branch``'s patch-equivalence probe is the self-heal path
            for a diverged author. A pure reader's local history can only
            ever be a stale prior-round checkout (readers never hold the
            gateway's ``commit`` tool — see
            ``foundation.identity.WORKTREE_AUTHOR_ROLES`` — and a reader's
            durable outputs, e.g. QA's render previews, are written outside
            the worktree entirely), so it hard-resets to origin, discarding
            any uncommitted scratch along with it.

        Every reset above is additionally guarded by ``_safe_to_reset``: a
        detached HEAD or a worktree drifted onto a different branch (mid-rebase,
        a crashed checkout) is left untouched with a warning rather than have
        ``branch``'s ref moved under it.
        """
        await self._fetch_branch_ref(clone_root, branch, project_slug)
        origin_ref = f"origin/{branch}"
        if (
            self._worktree_git(
                clone_root,
                ["rev-parse", "--verify", "--quiet", origin_ref],
                check=False,
            ).returncode
            != 0
        ):
            return  # never pushed, or the fetch failed — nothing to compare to
        ahead = self._worktree_git(
            clone_root, ["rev-list", "--count", f"{origin_ref}..{branch}"], check=False
        )
        behind = self._worktree_git(
            clone_root, ["rev-list", "--count", f"{branch}..{origin_ref}"], check=False
        )
        if ahead.returncode != 0 or behind.returncode != 0:
            return
        local_ahead = ahead.stdout.strip()
        local_behind = behind.stdout.strip()
        if local_ahead == "0":
            if local_behind == "0":
                return  # already in sync
            if can_author and self._worktree_is_dirty(worktree):
                return  # never discard an author's uncommitted edits
            self._reset_worktree_if_safe(worktree, branch, origin_ref)
            return
        if local_behind == "0":
            return  # strictly ahead — someone's real unpushed work
        # Diverged: only a pure reader's copy is disposable.
        if not can_author:
            self._reset_worktree_if_safe(worktree, branch, origin_ref)

    def _reset_worktree_if_safe(
        self, worktree: Path, branch: str, origin_ref: str
    ) -> None:
        if self._safe_to_reset(worktree, branch):
            self._worktree_git(worktree, ["reset", "--hard", origin_ref], check=False)

    async def remove_worktree(self, clone_root: Path, worktree: Path) -> None:
        """Remove a per-task worktree (cancel / terminal / reaper evict).

        Best-effort ``git worktree remove --force`` then ``prune`` so no dangling
        admin dir collides with a future re-claim. No-op if the worktree is
        already gone.
        """
        self._worktree_git(
            clone_root, ["worktree", "remove", "--force", str(worktree)], check=False
        )
        self._worktree_git(clone_root, ["worktree", "prune"], check=False)

    async def delete_local_branch(
        self, clone_root: Path, branch: str, *, force: bool
    ) -> None:
        """Delete a local branch ref in a clone. Best-effort, never raises.

        ``remove_worktree`` only detaches the worktree — the local
        ``refs/heads/{branch}`` ref survives, so every task an agent ever
        claimed leaks a permanent branch ref in that agent's clone. Callers
        run this right after ``remove_worktree`` (the worktree must be gone
        first, or ``git branch -d/-D`` refuses a branch still checked out
        elsewhere in the clone).

        ``force=False`` uses ``-d`` (refuses an unmerged branch — a clean skip,
        not an error, via ``check=False``); ``force=True`` uses ``-D``. A
        missing branch is likewise a clean skip. Never touches main/master/
        develop or an empty branch name — mirrors
        ``GitService._delete_remote_branch_best_effort``'s default-branch guard.
        """
        if branch in ("main", "master", "develop", ""):
            return
        flag = "-D" if force else "-d"
        self._worktree_git(clone_root, ["branch", flag, branch], check=False)

    async def resolve_workspace(
        self,
        project_slug: str,
        agent_id: UUID | str,
    ) -> Path:
        """
        Resolve workspace path from project slug and agent ID.

        Looks up the agent to get team and slug, then computes path.

        Args:
            project_slug: Project identifier
            agent_id: Agent UUID or slug

        Returns:
            Path to the workspace directory

        Raises:
            WorkspaceError: If agent not found
        """
        from sqlalchemy import select

        # Look up agent
        agent_id_str = str(agent_id)

        # Try by UUID first, then by slug
        query = select(AgentTable)
        try:
            agent_uuid = UUID(agent_id_str)
            query = query.where(AgentTable.id == agent_uuid)
        except ValueError:
            query = query.where(AgentTable.slug == agent_id_str)

        result = await self.session.execute(query)
        agent = result.scalar_one_or_none()

        if not agent:
            raise WorkspaceError(f"Agent not found: {agent_id}")

        team = agent.team if agent.team else Team.BACKEND
        return self.get_workspace_path(project_slug, team, agent.slug)

    async def _lookup_agent_or_raise(self, agent_id: UUID | str) -> AgentTable:
        """Find an agent by UUID or slug; raise WorkspaceError if missing."""
        from sqlalchemy import select

        agent_id_str = str(agent_id)
        query = select(AgentTable)
        try:
            agent_uuid = UUID(agent_id_str)
            query = query.where(AgentTable.id == agent_uuid)
        except ValueError:
            query = query.where(AgentTable.slug == agent_id_str)

        result = await self.session.execute(query)
        agent = result.scalar_one_or_none()
        if not agent:
            raise WorkspaceError(f"Agent not found: {agent_id}")
        return agent

    @staticmethod
    def _is_workspace_healthy(workspace: Path) -> bool:
        """`.git` exists and has HEAD + objects (not a stub clone)."""
        git_dir = workspace / ".git"
        return (
            git_dir.exists()
            and (git_dir / "HEAD").exists()
            and (git_dir / "objects").exists()
        )

    @staticmethod
    def _prune_broken_refs(workspace: Path) -> None:
        """Drop debris loose refs before a fetch. Best-effort; never raises.

        Interrupted hard-stop recovery can leave ``.bak`` ref debris and
        truncated/garbage loose-ref files under ``.git/refs``. Git tolerates
        them but emits a "ignoring broken ref" warning on every ref-walking
        operation (fetch included), which pollutes logs and can wedge ref
        enumeration. Remove ``.bak`` debris and any loose ref whose contents are
        neither an object id nor a symref. Runs as the workspace git user: the
        refs dir is agent-writable, and a root walk would follow a planted
        link out of it.
        """
        refs_dir = workspace / ".git" / "refs"
        if not refs_dir.is_dir():
            return
        result = run_as_workspace_user(
            sys.executable,
            "-I",
            "-S",
            "-c",
            _PRUNE_BROKEN_REFS_SCRIPT,
            str(refs_dir),
            timeout=_REFS_PRUNE_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            logger.warning(
                "ensure_workspace: broken-ref prune failed",
                workspace=str(workspace),
                error=result.stderr.strip()[:500],
            )

    async def _fetch_origin_best_effort(
        self, workspace: Path, project_slug: str
    ) -> None:
        """Refresh `origin`'s refs into a healthy clone. Never raises.

        Called from `ensure_workspace`'s healthy short-circuit so that a
        respawned PM/Doc reads fresh `origin/<branch>` refs instead of
        whatever the previous spawn left on disk. The fetch is SCOPED to the
        workspace's current branch + the repo's default branch (with
        `--no-tags --prune`). An all-refs `git fetch origin` transfers every
        accumulated `feature/*` on a monorepo and blows past the timeout, after
        which the workspace silently keeps a stale base and the agent builds on
        it. The refs a workspace's `git diff/log origin/<branch>` readers need
        are its own branch and the default; the integration branch is refreshed
        at branch-creation time (`create_branch_for_task`), not here.

        Served through the project's relay, so a private repo refreshes too
        (the token stays in the relay). Timeout uses
        `workspace_refresh_fetch_timeout_seconds` (default 60s), NOT
        `workspace_clone_timeout` (300s) — a refresh transfers small deltas,
        so 300s of blocking on every spawn against a hung remote is
        operationally bad.
        """

        def _scoped_refs() -> list[str]:
            """The current branch + the repo's default branch, deduped."""
            current = self._worktree_git(
                workspace, ["rev-parse", "--abbrev-ref", "HEAD"], check=False
            ).stdout.strip()
            origin_head = self._worktree_git(
                workspace,
                ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
                check=False,
            ).stdout.strip()
            default = origin_head.split("/", 1)[1] if "/" in origin_head else "master"
            refs: list[str] = []
            for ref in (current, default):
                if ref and ref != "HEAD" and ref not in refs:
                    refs.append(ref)
            return refs or ["master"]

        try:
            refs = await asyncio.to_thread(_scoped_refs)
        except (UnsafeWorkspaceGitConfigError, subprocess.SubprocessError) as exc:
            logger.warning(
                "ensure_workspace: refresh fetch skipped",
                workspace=str(workspace),
                project=project_slug,
                error=str(exc),
            )
            return
        result = await self._origin_git(
            workspace,
            project_slug,
            ["fetch", "--no-tags", "--prune", "origin", *refs],
            timeout=settings.workspace_refresh_fetch_timeout_seconds,
        )
        if result.returncode != 0:
            logger.warning(
                "ensure_workspace: refresh fetch returned non-zero",
                workspace=str(workspace),
                project=project_slug,
                stderr=result.stderr.strip(),
            )

    @staticmethod
    async def _resolve_git_token(
        project_service: Any, project_slug: str, git_url: str
    ) -> str | None:
        """Decrypt the project's git token; raise WorkspaceError on failure."""
        from roboco.utils.crypto import EncryptionError

        try:
            git_token = await project_service.get_decrypted_token_by_slug(project_slug)
        except EncryptionError as e:
            raise WorkspaceError(
                f"Failed to decrypt git token for project '{project_slug}'. "
                "The ROBOCO_ENCRYPTION_KEY may have been rotated or the "
                "stored token is corrupted. Re-set the project token."
            ) from e

        if git_url.startswith("https://") and not git_token:
            raise WorkspaceError(
                f"Project '{project_slug}' requires a git token for HTTPS clone. "
                "Configure a GitHub PAT in the project settings."
            )
        return cast("str | None", git_token)

    async def ensure_workspace(
        self,
        project_slug: str,
        agent_id: UUID | str,
        git_url: str | None = None,
        default_branch: str = "main",
        force: bool = False,
        *,
        skip_refresh: bool = False,
    ) -> Path:
        """
        Ensure workspace exists, cloning if necessary.

        Protects against:
        - Partial clones (directory exists but `.git` does not) — cleans up
          the incomplete directory before re-cloning.
        - Concurrent callers — per (project, agent) asyncio.Lock serializes
          ensure_workspace calls so two coroutines can't both try to clone
          into the same directory.

        Args:
            project_slug: Project identifier
            agent_id: Agent UUID or slug
            git_url: Git URL to clone (fetched from project if not provided)
            default_branch: Default branch to checkout
            force: When True, bypass the 30s refresh-fetch TTL cache and
                always run ``git fetch origin`` on a healthy workspace.
                Defaults to False so existing callers are unaffected.
            skip_refresh: When True, skip the refresh-fetch entirely on a
                healthy workspace. Used by the supersede branch-cut path,
                which fetches ``refs/pull/{n}/head`` (not an existing local
                branch), so the 60s origin refresh is wasted I/O. The clone
                path (workspace does not exist) still runs.

        Returns:
            Path to the workspace directory

        Raises:
            WorkspaceError: If workspace creation fails
        """
        from roboco.services.project import get_project_service

        agent = await self._lookup_agent_or_raise(agent_id)
        team = agent.team if agent.team else Team.BACKEND
        workspace = self.get_workspace_path(project_slug, team, agent.slug)

        lock = _ensure_lock_for(project_slug, agent.slug)
        async with lock:
            # Healthy clone — refresh it and return.
            #
            # `.git` existing is necessary but NOT sufficient — a failed
            # clone can leave behind a stub .git/ with only FETCH_HEAD and
            # no HEAD/objects, which looks "healthy" to a naive check but
            # breaks every subsequent fetch/checkout ("origin/<branch> is
            # not a commit"). Require HEAD + objects/ as the real signal.
            if self._is_workspace_healthy(workspace):
                # Audit H26: a healthy clone short-circuit USED to return
                # immediately, so a respawned PM/Doc could be reading
                # arbitrarily stale refs (whatever was on disk from the
                # last spawn). Fetch every entry so `git diff origin/...`
                # reflects what's actually on the remote. Best-effort —
                # network blips and offline mode must not break workspace
                # setup; checkout is unchanged.
                #
                # 30s TTL cache keyed by workspace path. Dogfooding fired
                # this fetch 9x/run because every
                # evidence() call triggers ensure_workspace within the same
                # few seconds. Skip redundant fetches; force=True overrides.
                _FETCH_CACHE_TTL_SECONDS = 30.0
                now = _monotonic()
                # -math.inf as default means "never fetched" — guarantees
                # the first call always runs the fetch regardless of clock value.
                last_fetch = self._fetch_cache.get(str(workspace), -math.inf)
                if not skip_refresh and (
                    force or (now - last_fetch) >= _FETCH_CACHE_TTL_SECONDS
                ):
                    # Repair broken-ref debris first so the fetch (and the
                    # agent's later `git diff/log origin/...`) doesn't trip on a
                    # ref left corrupt by an interrupted recovery.
                    await asyncio.to_thread(self._prune_broken_refs, workspace)
                    await self._fetch_origin_best_effort(workspace, project_slug)
                    self._fetch_cache[str(workspace)] = _monotonic()
                # Ensure dev deps are present even for workspaces cloned
                # before this feature landed. Idempotent: the lockfile-digest
                # marker makes this a no-op once installed, so it costs only a
                # cheap hash on every healthy re-entry.
                await self.install_dev_deps(workspace)
                logger.debug(
                    "Workspace already exists",
                    workspace=str(workspace),
                    project=project_slug,
                )
                return workspace

            # Partial clone: directory exists but `.git` is missing or
            # a stub. git clone refuses to clone into a non-empty
            # directory, so remove it first instead of letting the next
            # clone fail.
            if workspace.exists():
                logger.warning(
                    "Removing partial/stub workspace before re-clone",
                    workspace=str(workspace),
                    project=project_slug,
                    had_git_dir=(workspace / ".git").exists(),
                )
                try:
                    remove_workspace_tree(workspace)
                except OSError as exc:
                    raise WorkspaceError(
                        f"Cannot remove partial workspace {workspace}: {exc}"
                    ) from exc

            project_service = get_project_service(self.session)
            project = await project_service.get_by_slug(project_slug)
            if not project:
                raise WorkspaceError(f"Project not found: {project_slug}")

            if not git_url:
                git_url = project.git_url
                default_branch = head_branch(project)

            # HTTPS needs the project's token (the relay carries it); fail
            # fast with the operator-facing message instead of an auth error.
            await self._resolve_git_token(project_service, project_slug, git_url)

            await self._clone_repo(
                workspace,
                git_url,
                default_branch,
                project_slug=project_slug,
                agent=agent,
            )
            await self._maybe_scaffold_conventions(project, project_slug, workspace)
            return workspace

    async def _maybe_scaffold_conventions(
        self, project: Any, project_slug: str, workspace: Path
    ) -> None:
        """Open the conventions scaffold PR on a project's first clone.

        Flag-gated, best-effort, once-per-process: fires only when the standard
        file is absent in the fresh clone and we have not already tried for this
        project, so a newly registered project gets a starter
        ``.roboco/conventions.yml`` — and any failure is swallowed so it can
        never affect the clone path. Imported lazily to avoid the
        workspace -> conventions -> git import chain.
        """
        from roboco.config import settings

        if not settings.conventions_enabled or project_slug in _SCAFFOLD_ATTEMPTED:
            return
        _SCAFFOLD_ATTEMPTED.add(project_slug)
        if (workspace / ".roboco" / "conventions.yml").exists():
            return
        try:
            from roboco.services.conventions import get_conventions_service

            await get_conventions_service(self.session).scaffold(
                project, workspace=workspace
            )
        except Exception as exc:
            logger.warning(
                "Conventions scaffold on first clone failed (non-fatal)",
                project=project_slug,
                error=str(exc),
            )

    async def ensure_read_clone(
        self, project_slug: str, *, force: bool = False
    ) -> Path:
        """Ensure a project-level read clone pinned to the head branch's HEAD.

        The architectural-conventions standard is read from the committed
        ``.roboco/conventions.yml`` plus a scan of the repo tree. Per-agent
        working clones are the wrong source — one may sit on a feature branch or
        be stale — so metadata reads use this dedicated clone instead. It lives
        under the private root (``read_clone_path``), which no agent container
        mounts, and is always hard-reset to the relay's ``<head_branch>``,
        which makes destructive refresh safe. It is checked out with
        ``core.symlinks=false``: the code reading it runs as root, so a symlink
        committed to the repo must stay a plain file here. This is what lets
        the standard work for a project created before the standard existed:
        no manually-configured ``workspace_path`` is required.

        ``force`` bypasses the 30s refresh TTL (conventions reads must reflect
        the current head-branch HEAD, not a stale one).

        The DB reads (project + token, for the relay) are followed by
        ``_release_pool_connection`` before the clone/fetch: the network work
        that follows can take a while, and without the release the connection
        this method's own DB read just checked out would sit held for the
        whole of it.
        """
        from roboco.services.project import get_project_service

        project = await get_project_service(self.session).get_by_slug(project_slug)
        if not project:
            raise WorkspaceError(f"Project not found: {project_slug}")
        default_branch = head_branch(project)
        workspace = read_clone_path(project_slug)

        lock = _ensure_lock_for(project_slug, "_meta-conventions")
        async with lock:
            healthy = self._is_workspace_healthy(workspace)
            now = _monotonic()
            last = _read_clone_synced.get(str(workspace), -math.inf)
            if healthy and not force and (now - last) < _READ_CLONE_FETCH_TTL_SECONDS:
                return workspace
            relay = await self._relay_for(project_slug)
            await self._release_pool_connection()
            if relay is None:
                raise WorkspaceError(f"Project not found: {project_slug}")
            if not healthy:
                if workspace.exists():
                    shutil.rmtree(workspace)
                await asyncio.to_thread(
                    self._create_read_clone, workspace, relay, default_branch
                )
            await asyncio.to_thread(
                self._sync_read_clone, workspace, relay, default_branch
            )
            _read_clone_synced[str(workspace)] = _monotonic()
            return workspace

    async def _release_pool_connection(self) -> None:
        """End the current transaction so its pool connection is returned.

        Mirrors content_actions.evidence()'s pool-release commit: the next
        read/write reopens a fresh transaction on demand. A poisoned session
        rolls back instead - ending the transaction is the point, either way
        works.
        """
        from sqlalchemy.exc import PendingRollbackError

        try:
            await self.session.commit()
        except PendingRollbackError:
            await self.session.rollback()

    @staticmethod
    async def _read_clone_token(project_service: Any, project_slug: str) -> str | None:
        """The project's decrypted git token, or ``None`` — never raises.

        The read-clone refresh must authenticate against a private origin, but a
        public repo legitimately has no token, so (unlike the clone path) we do
        not hard-require one here.
        """
        from roboco.utils.crypto import EncryptionError

        try:
            token = await project_service.get_decrypted_token_by_slug(project_slug)
        except EncryptionError:
            return None
        return cast("str | None", token)

    @staticmethod
    def _create_read_clone(
        workspace: Path, relay: GitRelay, default_branch: str
    ) -> None:
        """Clone the relay into the private read-clone path (root, trusted)."""
        timeout = settings.workspace_clone_timeout
        fetched = relay.fetch_from_origin(
            [f"+refs/heads/{default_branch}:refs/heads/{default_branch}"],
            timeout=timeout,
        )
        if fetched.returncode != 0:
            raise WorkspaceError(
                f"Failed to fetch {default_branch}: {fetched.stderr.strip()[:500]}"
            )
        workspace.parent.mkdir(parents=True, exist_ok=True)
        try:
            run_private_git(
                workspace.parent,
                [
                    "clone",
                    "--quiet",
                    "--no-tags",
                    "-c",
                    "core.symlinks=false",
                    "--branch",
                    default_branch,
                    str(relay.path),
                    str(workspace),
                ],
                timeout=timeout,
            )
        except subprocess.CalledProcessError as exc:
            shutil.rmtree(workspace, ignore_errors=True)
            raise WorkspaceError(f"Failed to clone read clone: {exc.stderr}") from exc

    @staticmethod
    def _sync_read_clone(workspace: Path, relay: GitRelay, default_branch: str) -> None:
        """Fetch origin's default branch + tags through the relay, then
        hard-reset the read clone to it. Best-effort: logs and returns on
        failure.

        Fetch tags: the release manager reads this same clone and derives
        "commits since last release" from the newest tag — a tagless clone
        makes git describe fail and it walks the entire history. --force
        because a re-published tag (v0.31.0 was clobbered upstream) makes a
        plain --tags fetch fail wholesale with "would clobber existing tag",
        freezing this read mirror at clone time — and this clone is
        hard-reset to FETCH_HEAD right below anyway, never pushed from, so
        tracking origin's tags exactly is the only correct state for it.
        """
        timeout = settings.workspace_clone_timeout
        fetched = relay.fetch_from_origin(
            [f"+refs/heads/{default_branch}:refs/heads/{default_branch}"],
            tags=True,
            timeout=timeout,
        )
        if fetched.returncode == 0:
            fetched = run_private_git(
                workspace,
                ["fetch", "--tags", "--force", str(relay.path), default_branch],
                timeout=timeout,
                check=False,
            )
        if fetched.returncode != 0:
            logger.warning(
                "conventions read-clone fetch failed",
                workspace=str(workspace),
                error=(fetched.stderr or fetched.stdout).strip()[:200],
            )
            return
        run_private_git(
            workspace, ["checkout", default_branch], timeout=timeout, check=False
        )
        run_private_git(
            workspace, ["reset", "--hard", "FETCH_HEAD"], timeout=timeout, check=False
        )

    async def _clone_repo(
        self,
        workspace: Path,
        git_url: str,
        default_branch: str,
        *,
        project_slug: str,
        agent: AgentTable | None = None,
    ) -> None:
        """
        Clone a project into an agent workspace, through the project's relay.

        The relay fetches every branch from origin (with the token), then the
        workspace git user clones the relay with ``--no-local`` — a real
        transfer, so nothing is hardlinked to the root-owned relay — which
        means the workspace is created, owned and written by the git user, in
        the agent group, from its first byte. ``remote.origin.url`` is then
        set to the project's URL for display: workspace git never talks to a
        network, every ``origin`` round-trip goes through the relay.

        Do NOT pass --single-branch. Agents work on feature branches pushed by
        peers; QA and documenter need to fetch those branches after a dev
        pushes. --no-tags keeps the clone light (the read clone pulls tags
        separately via _sync_read_clone).

        Args:
            workspace: Target directory
            git_url: The project's git URL (from the database)
            default_branch: Branch to checkout
            project_slug: The project whose relay serves the clone
            agent: Agent for git identity (name/email in commits)

        Raises:
            WorkspaceError: If clone fails
        """
        relay = await self._relay_for(project_slug)
        if relay is None:
            raise WorkspaceError(f"Project not found: {project_slug}")
        logger.info(
            "Cloning repository",
            workspace=str(workspace),
            git_url=git_url,
            branch=default_branch,
        )
        name = agent.name if agent else "RoboCo Agent"
        slug = agent.slug if agent else "agent"
        timeout = settings.workspace_clone_timeout

        def _do_clone() -> None:
            fetched = relay.fetch_from_origin(
                ["+refs/heads/*:refs/heads/*"], prune=True, timeout=timeout
            )
            if fetched.returncode != 0:
                raise subprocess.CalledProcessError(
                    fetched.returncode, ["git", "fetch"], fetched.stdout, fetched.stderr
                )
            prepare_clone_parent(workspace)
            # From "/": no agent-owned directory is ever the working
            # directory a clone could discover a planted repository in.
            run_workspace_git(
                Path("/"),
                [
                    "clone",
                    "--quiet",
                    "--no-local",
                    "--no-tags",
                    "--branch",
                    default_branch,
                    str(relay.path),
                    str(workspace),
                ],
                timeout=timeout,
            )
            for args in (
                ["remote", "set-url", "origin", git_url],
                ["config", "user.name", name],
                ["config", "user.email", f"{slug}@roboco.tech"],
                # Disable filesystem mode tracking. The workspace volumes live
                # on the NAS (`/volume1/...`), which has POSIX ACL inheritance
                # that gives every cloned file the executable bit. With the
                # default `core.fileMode = true`, git treats every tracked
                # file as modified the moment it's cloned, and `task_start`'s
                # clean-tree check refuses to checkout the feature branch.
                ["config", "core.fileMode", "false"],
            ):
                run_workspace_git(workspace, args, timeout=timeout)

        try:
            await asyncio.to_thread(_do_clone)
            logger.info("Repository cloned successfully", workspace=str(workspace))
        except subprocess.CalledProcessError as e:
            # A half-configured workspace must not linger: the next
            # ensure_workspace re-clones from scratch.
            remove_workspace_tree(workspace, ignore_errors=True)
            raise WorkspaceError(
                f"Failed to clone repository: {e.stderr or e.stdout}"
            ) from e
        except subprocess.TimeoutExpired as e:
            remove_workspace_tree(workspace, ignore_errors=True)
            raise WorkspaceError(
                f"Clone timed out after {settings.workspace_clone_timeout}s"
            ) from e
        except OSError as e:
            # A directory the clone needs could not be made or opened: the
            # relay's under the private root, or one above the clone
            # (prepare_clone_parent refuses a project/team dir an agent
            # swapped for a symlink).
            remove_workspace_tree(workspace, ignore_errors=True)
            raise WorkspaceError(f"Failed to clone repository: {e}") from e

        # Install the project's dev dependencies into the workspace's own
        # environment so the agent has ruff/mypy/pytest (Python) or the TS
        # toolchain available for the `make quality` gate without
        # re-downloading per task. Best-effort + idempotent. A clone is still
        # usable if the install fails (the agent can install on the fly), so
        # this must NOT abort workspace setup.
        await self.install_dev_deps(workspace)

    async def install_dev_deps(self, workspace: Path) -> bool:
        """Install the project's dev dependencies into `workspace`.

        Idempotent: hashes the lockfiles and skips the install when they
        match the marker written by the previous successful run. Detects the
        ecosystem (Python `uv sync`, Node/TS `pnpm install`/`npm ci`) and
        runs each install from the workspace root, in a throwaway container as
        the agent uid: an install runs the clone's build backend and package
        scripts. Best-effort — failures are logged, never raised, so a missing
        toolchain, a container that cannot start or a flaky registry can't
        break the agent's clone.

        Returns True when an install ran (and at least one command
        succeeded), False when skipped (cache hit, disabled, or nothing to
        install).
        """
        if not settings.workspace_install_dev_deps:
            return False

        # When toolchain matching is on, provision against the target project's
        # declared Python (uv resolves + fetches it) instead of the system 3.13.
        target_python = await asyncio.to_thread(
            self._resolve_toolchain_target, workspace
        )

        commands = _detect_dep_commands(workspace, target_python=target_python)
        if not commands:
            return False

        digest = await asyncio.to_thread(_lockfile_digest, workspace)
        if await asyncio.to_thread(self._dep_install_cache_hit, workspace, digest):
            logger.debug(
                "Dev-deps install skipped (lockfiles unchanged)",
                workspace=str(workspace),
            )
            # Stamp the toolchain marker the first time it's missing so a
            # workspace provisioned before the flag flipped still records it.
            await self._record_toolchain(workspace, target_python, only_if_missing=True)
            return False

        any_ok = False
        for label, argv in commands:
            ok = await self._run_dep_install(workspace, label, argv)
            any_ok = any_ok or ok

        # Record the digest so a re-entry with the same lockfiles is a no-op.
        # Only write on success — a failed install should retry next time.
        if any_ok and digest is not None:
            with contextlib.suppress(OSError):
                await asyncio.to_thread(
                    write_workspace_file, workspace / _DEP_INSTALL_MARKER, digest
                )

        await self._record_toolchain(workspace, target_python)
        return any_ok

    @staticmethod
    def _resolve_toolchain_target(workspace: Path) -> str | None:
        """The Python version to provision with, or None (flag off / nothing
        declared → today's behavior)."""
        if not settings.toolchain_match_enabled:
            return None
        resolved = resolve_target_python(workspace, read_workspace_file)
        return resolved.version if resolved else None

    async def _record_toolchain(
        self, workspace: Path, python: str | None, *, only_if_missing: bool = False
    ) -> None:
        """Run the runnability smoke and write the toolchain marker (best-effort).

        Inert when ``python`` is None (flag off / no target version).
        """
        if python is None:
            return
        marker = workspace / _TOOLCHAIN_MARKER
        if only_if_missing:
            existing = await asyncio.to_thread(read_workspace_file, marker)
            if existing is not None:
                return
        status = await self._run_toolchain_smoke(workspace, python)
        with contextlib.suppress(OSError):
            await asyncio.to_thread(
                write_workspace_file,
                marker,
                json.dumps({"python": python, "status": status}),
            )

    @staticmethod
    async def _run_toolchain_smoke(workspace: Path, python: str) -> str:
        """Can the project's suite be collected under ``python``?

        Returns ``ok`` (collected, or no tests), ``broken`` (collection/import
        error — the interpreter-mismatch signature), or ``unknown`` (pytest
        absent, tool missing, timeout, no container — never block on these).
        Precision over recall: only a genuine collection error reports
        ``broken``. Collection imports the clone's own conftest/tests, so it
        runs in a throwaway container as the agent uid.
        """
        argv = [
            "uv",
            "run",
            "--python",
            python,
            "python",
            "-m",
            "pytest",
            "--collect-only",
            "-q",
        ]

        try:
            result = await run_in_workspace_container(
                workspace,
                argv,
                timeout=settings.workspace_dep_install_timeout_seconds,
                env=_uv_container_env(workspace),
            )
        except (WorkspaceContainerError, OSError):
            return "unknown"
        if result.timed_out:
            return "unknown"
        if result.returncode in (_PYTEST_OK, _PYTEST_NO_TESTS_COLLECTED):
            return "ok"
        if result.returncode == _PYTEST_COLLECTION_ERROR:
            return "broken"
        return "unknown"

    @staticmethod
    def read_toolchain_status(workspace: Path) -> tuple[str | None, str | None]:
        """Read ``(python, status)`` from the workspace toolchain marker.

        ``(None, None)`` when no marker exists (flag off, not yet provisioned,
        or unreadable) — callers must treat that as 'do not block'. Read as
        the workspace git user (blocking; call off the event loop).
        """
        text = read_workspace_file(workspace / _TOOLCHAIN_MARKER)
        if text is None:
            return (None, None)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return (None, None)
        if not isinstance(data, dict):
            return (None, None)
        return (data.get("python"), data.get("status"))

    @staticmethod
    def _dep_install_cache_hit(workspace: Path, digest: str | None) -> bool:
        """True when the lockfile digest matches the marker from a prior run.

        Read as the workspace git user (blocking; call off the event loop).
        """
        if digest is None:
            return False
        marker = read_workspace_file(workspace / _DEP_INSTALL_MARKER)
        return marker is not None and marker.strip() == digest

    @staticmethod
    async def _run_dep_install(workspace: Path, label: str, argv: list[str]) -> bool:
        """Run one dep-install command in a throwaway workspace container.

        The install executes the clone's build backend / package scripts,
        which any agent can rewrite, so it never runs here as root. Logs and
        swallows every failure; returns True only when it exited 0.
        """
        logger.info(
            "Installing workspace dev dependencies",
            workspace=str(workspace),
            command=label,
        )
        try:
            result = await run_in_workspace_container(
                workspace,
                argv,
                timeout=settings.workspace_dep_install_timeout_seconds,
                env=_uv_container_env(workspace),
            )
        except (WorkspaceContainerError, OSError) as exc:
            logger.warning(
                "Dev-deps install container could not start; skipping",
                workspace=str(workspace),
                command=label,
                error=str(exc),
            )
            return False

        if result.returncode != 0:
            logger.warning(
                "Dev-deps install returned non-zero",
                workspace=str(workspace),
                command=label,
                timed_out=result.timed_out,
                stderr=result.stderr.strip()[:2000],
            )
            return False

        logger.info(
            "Dev-deps install complete",
            workspace=str(workspace),
            command=label,
        )
        return True

    async def dry_upgrade_changes_lockfile(self, project: Any) -> bool:
        """Read-only probe: would a dependency upgrade change this repo's lockfiles?

        Clones the project's read clone into a throwaway dir, runs
        ``project.dep_update_command`` there, and reports whether any lockfile
        path is dirty. The read clone is never mutated and nothing is committed
        or pushed. Returns False (don't originate) on a null command or any
        probe/command error — fail-safe — and logs loudly. The throwaway is
        always removed.

        The local ``git clone --local`` from the read clone runs UNDER the
        project's read-clone lock (the same lock ``ensure_read_clone`` syncs
        under) so a concurrent ``ensure_read_clone`` → ``_sync_read_clone``
        (fetch + hard-reset to origin's default branch) cannot mutate the read
        clone mid-clone. The lock is released before the upgrade runs: the
        upgrade operates on the independent local copy and never touches the
        read clone, so holding the lock past the clone would only block
        conventions reads for the upgrade duration.

        The upgrade resolves (and may build) the project's dependencies, and
        its ``git status`` reads a ``.git`` that code just ran in, so both run
        in a throwaway workspace container as the agent uid. The throwaway
        clone lives under ``private_data_root`` (orchestrator-only, and a path
        the Docker daemon can bind) and is handed to that uid first.
        """
        command = str(getattr(project, "dep_update_command", None) or "").strip()
        if not command:
            return False
        slug = str(getattr(project, "slug", "") or "")
        try:
            read_clone = await self.ensure_read_clone(slug)
        except WorkspaceError as exc:
            logger.warning(
                "dep-update probe: read clone unavailable",
                project=slug,
                error=str(exc),
            )
            return False
        lock_paths = list(
            getattr(project, "dep_update_paths", None) or _DEP_LOCK_DEFAULTS
        )
        scratch_root = Path(settings.private_data_root) / "scratch"
        scratch_root.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix="dep-probe-", dir=scratch_root))
        try:
            clone_dir = tmp / "repo"
            timeout = settings.workspace_dep_install_timeout_seconds
            lock = _ensure_lock_for(slug, "_meta-conventions")
            async with lock:
                await asyncio.to_thread(
                    self._clone_local_into, read_clone, clone_dir, timeout
                )
            await asyncio.to_thread(_give_scratch_tree_to_agent_uid, tmp)
            return await self._probe_lockfile_on_clone(clone_dir, command, lock_paths)
        except Exception as exc:
            logger.warning(
                "dep-update probe failed; not originating",
                project=slug,
                error=str(exc),
            )
            return False
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @staticmethod
    def _clone_local_into(read_clone: Path, clone_dir: Path, timeout: float) -> None:
        """Local clone of the read clone into ``clone_dir`` (run in a thread).

        ``--no-hardlinks`` forces a full object copy so the clone is an
        independent repo that can be mutated (the upgrade) without touching the
        read clone. Caller holds the read-clone lock so ``_sync_read_clone``
        cannot mutate the source mid-clone.
        """
        run_private_git(
            clone_dir.parent,
            ["clone", "--local", "--no-hardlinks", str(read_clone), str(clone_dir)],
            timeout=timeout,
        )

    @staticmethod
    async def _probe_lockfile_on_clone(
        clone_dir: Path, command: str, lock_paths: list[str]
    ) -> bool:
        """Run the upgrade on the independent local clone + report dirty.

        Both steps run in a throwaway workspace container as the agent uid.
        The upgrade runs with no shell (``shlex.split``); a non-zero upgrade
        yields False (fail-safe, don't originate on a broken probe). The lock
        is NOT held here — the clone is a full independent copy and the
        upgrade never touches the read clone. A container that cannot start
        raises; the caller logs it and does not originate.
        """
        timeout = settings.workspace_dep_install_timeout_seconds
        upgrade = await run_in_workspace_container(
            clone_dir,
            shlex.split(command),
            timeout=timeout,
            env=_uv_container_env(clone_dir),
        )
        if upgrade.returncode != 0:
            logger.warning(
                "dep-update probe: upgrade command non-zero; not originating",
                command=command,
                timed_out=upgrade.timed_out,
                stderr=upgrade.stderr.strip()[:2000],
            )
            return False
        status = await run_in_workspace_container(
            clone_dir,
            ["git", "status", "--porcelain", "--", *lock_paths],
            timeout=timeout,
        )
        return bool(status.stdout.strip())

    async def workspace_exists(
        self,
        project_slug: str,
        agent_id: UUID | str,
    ) -> bool:
        """Check if a workspace exists for the given project and agent."""
        try:
            workspace = await self.resolve_workspace(project_slug, agent_id)
            return (workspace / ".git").exists()
        except WorkspaceError:
            return False

    async def list_workspaces(self, project_slug: str) -> list[dict]:
        """
        List all workspaces for a project.

        Returns:
            List of workspace info dicts with team, agent, and path
        """
        project_dir = self.root / project_slug
        if not project_dir.exists():
            return []

        workspaces = []
        for team_dir in project_dir.iterdir():
            if not team_dir.is_dir():
                continue
            for agent_dir in team_dir.iterdir():
                if not agent_dir.is_dir():
                    continue
                if (agent_dir / ".git").exists():
                    workspaces.append(
                        {
                            "team": team_dir.name,
                            "agent": agent_dir.name,
                            "path": str(agent_dir),
                            "exists": True,
                        }
                    )
        return workspaces

    # =========================================================================
    # GATEWAY (CONTENT_ACTIONS) BACKFILL
    # =========================================================================

    async def _resolve_branch_to_project_slug(self, branch_name: str) -> str:
        """Look up the task that owns `branch_name` and return its project slug.

        Raises WorkspaceError when no task references the branch or the
        project record is missing — fetching a phantom branch would
        silently no-op otherwise.
        """
        from sqlalchemy import select

        from roboco.db.tables import TaskTable
        from roboco.services.project import get_project_service

        result = await self.session.execute(
            select(TaskTable).where(TaskTable.branch_name == branch_name).limit(1)
        )
        task = result.scalar_one_or_none()
        if task is None:
            raise WorkspaceError(f"No task references branch {branch_name!r}")
        project_service = get_project_service(self.session)
        project = await project_service.get(UUID(str(task.project_id)))
        if project is None:
            raise WorkspaceError(
                f"Task {task.id} for branch {branch_name!r} has no project"
            )
        return str(project.slug)

    async def fetch_branch_for_inspection(
        self,
        *,
        agent_id: UUID,
        branch_name: str,
        subprocess_timeout: float | None = None,
    ) -> Path:
        """Fetch `branch_name` into the inspecting agent's workspace.

        QA / Documenter / PM agents need to read a developer's branch from
        their own workspace before diffing. This adapter:

        1. Resolves the project from the branch (via the owning task).
        2. Ensures a healthy workspace for `agent_id` on that project
           (clones if missing — same path as the agent's first claim). This
           CLONE-CREATION step always keeps its own `workspace_clone_timeout`
           (300s) budget regardless of `subprocess_timeout` below — a fresh
           clone genuinely needs that much room.
        3. Runs `git fetch origin <branch>` through the project's relay so
           the branch ref is locally available for `git diff`.

        `subprocess_timeout` overrides ONLY the fetch subprocess in step 3
        (default `settings.workspace_clone_timeout`, 300s, for every
        existing caller that omits it). The advisory `evidence()` call site
        passes its own leg budget so a hung fetch self-terminates near that
        window instead of occupying a thread on the shared DEFAULT asyncio
        executor (`asyncio.to_thread` here, NOT git.py's dedicated
        `_GIT_EXECUTOR`) for up to 300s after the caller already gave up
        waiting on it.

        Returns the workspace path so the caller can chain checkout/diff
        operations if needed.
        """
        project_slug = await self._resolve_branch_to_project_slug(branch_name)
        workspace = await self.ensure_workspace(
            project_slug=project_slug,
            agent_id=agent_id,
        )
        effective_timeout = (
            subprocess_timeout
            if subprocess_timeout is not None
            else settings.workspace_clone_timeout
        )
        relay = await self._relay_for(project_slug)
        if relay is None:
            raise WorkspaceError(f"Project not found: {project_slug}")

        def _local(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
            return run_workspace_git(
                workspace, args, timeout=effective_timeout, check=False
            )

        ctx = OriginContext(
            relay=relay,
            repo=workspace,
            local_git=_local,
            from_workspace=True,
            timeout=effective_timeout,
        )
        try:
            result = await asyncio.to_thread(
                run_origin_command, ctx, ["fetch", "origin", branch_name]
            )
        except subprocess.TimeoutExpired as exc:
            # Mirrors _run_git's own TimeoutExpired -> GitTimeoutError
            # conversion (git.py) so a bounded caller (run_bounded_leg)
            # catches this the same way as every other git-touching leg.
            raise GitTimeoutError(
                f"fetch origin {branch_name}", int(effective_timeout)
            ) from exc
        if result.returncode != 0:
            logger.warning(
                "fetch_branch_for_inspection: fetch returned non-zero",
                branch=branch_name,
                workspace=str(workspace),
                stderr=result.stderr.strip(),
            )
        return workspace

    async def export_branch_motion(self, project: Any, branch: str) -> Path:
        """Export ``branch``'s ``motion/`` subtree into a fresh scratch dir —
        request_render's QA source. Read-only by construction: fetches
        ``origin/<branch>`` through the relay into the project's private READ
        CLONE (never a per-agent working tree) and ``git archive``s the commit
        directly —
        no checkout, so the read clone's own pinned HEAD is undisturbed for
        every other conventions-standard reader of that same clone. Caller
        owns cleanup of the returned scratch dir.

        Raises WorkspaceError only when the branch itself can't be fetched
        (bad ref, auth failure, network). A branch that fetches fine but has
        no ``motion/`` at that commit yields an EMPTY scratch dir — the
        caller's ``motion/compositions/<id>/`` existence check handles that
        uniformly alongside the ordinary "missing composition" case, rather
        than a second distinct error path here.
        """
        workspace = await self.ensure_read_clone(project.slug)
        relay = await self._relay_for(project.slug)
        if relay is None:
            raise WorkspaceError(f"Project not found: {project.slug}")
        timeout = settings.workspace_clone_timeout

        def _export() -> Path:
            fetched = relay.fetch_from_origin(
                [f"+refs/heads/{branch}:refs/heads/{branch}"], timeout=timeout
            )
            if fetched.returncode == 0:
                fetched = run_private_git(
                    workspace,
                    ["fetch", "--no-tags", str(relay.path), branch],
                    timeout=timeout,
                    check=False,
                )
            if fetched.returncode != 0:
                raise WorkspaceError(
                    f"export_branch_motion: fetch of {branch!r} failed: "
                    f"{fetched.stderr.strip()}"
                )
            sha = run_private_git(
                workspace, ["rev-parse", "FETCH_HEAD"], timeout=timeout, check=False
            ).stdout.strip()
            scratch = Path(tempfile.mkdtemp(prefix="render-src-"))
            if not sha:
                return scratch
            tarball = scratch / ".motion.tar"
            archive = run_private_git(
                workspace,
                ["archive", f"--output={tarball}", sha, "--", "motion"],
                timeout=timeout,
                check=False,
            )
            if archive.returncode != 0:
                # Most commonly: no motion/ tree at this commit. Leave the
                # scratch dir empty so the caller's composition-path check
                # reports the clean "missing composition" rejection instead
                # of a second distinct error here.
                logger.warning(
                    "export_branch_motion: git archive found no motion/ tree",
                    branch=branch,
                    stderr=archive.stderr.strip()[:200],
                )
                return scratch
            with tarfile.open(tarball) as tar:
                tar.extractall(scratch, filter="data")
            tarball.unlink()
            return scratch

        return await asyncio.to_thread(_export)

    async def delete_workspace(
        self,
        project_slug: str,
        agent_id: UUID | str,
    ) -> bool:
        """
        Delete a workspace (use with caution).

        Args:
            project_slug: Project identifier
            agent_id: Agent UUID or slug

        Returns:
            True if deleted, False if didn't exist
        """
        workspace = await self.resolve_workspace(project_slug, agent_id)
        if not workspace.exists():
            return False

        logger.warning(
            "Deleting workspace",
            workspace=str(workspace),
        )
        await asyncio.to_thread(remove_workspace_tree, workspace)
        return True


def get_workspace_service(session: AsyncSession) -> WorkspaceService:
    """Factory function to get workspace service."""
    return WorkspaceService(session)
