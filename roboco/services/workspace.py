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
    /data/workspaces/roboco/backend/be-dev-1/
    /data/workspaces/roboco/backend/be-dev-2/
    /data/workspaces/roboco/frontend/fe-dev-1/
"""

import asyncio
import contextlib
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from roboco.config import settings
from roboco.db.tables import AgentTable
from roboco.logging import get_logger
from roboco.models.base import Team
from roboco.services.toolchain import resolve_target_python

logger = get_logger(__name__)

# Lockfile paths the dep-update probe inspects when a project sets no explicit
# dep_update_paths — the two RoboCo's stack uses.
_DEP_LOCK_DEFAULTS = ("uv.lock", "pnpm-lock.yaml")

# A healthy loose ref file holds either an object id (sha1 = 40 hex, sha256 = 64
# hex) or a symbolic ref ("ref: refs/..."). Anything else is debris — used to
# detect broken loose refs left by interrupted recovery before a fetch.
_REF_OBJECT_ID_RE = re.compile(r"\A[0-9a-f]{40}\Z|\A[0-9a-f]{64}\Z")

# Agent container runs the `agent` user created in agent-base.Dockerfile.
# Debian's `useradd -m` defaults to uid 1000 when that uid is free.
# Overridable via env so operators can customize if they rebuild agent-base
# with a different id.
_AGENT_UID = int(os.environ.get("ROBOCO_AGENT_UID", "1000"))
_AGENT_GID = int(os.environ.get("ROBOCO_AGENT_GID", "1000"))

# Large, gitignored, agent-regenerated trees we never need to chown — they are
# either absent or already agent-owned (the agent created them), and walking
# node_modules alone cost 2.7-15.5s per git op. Pruning them keeps the
# ownership walk fast while still handing the agent every tracked file + .git.
_PRUNE_DIRS = frozenset(
    {
        "node_modules",
        ".venv",
        "venv",
        "dist",
        "build",
        ".next",
        ".turbo",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)


def _chown_entry(entry: str) -> bool:
    """Chown a single entry; return True on success (or already correct)."""
    try:
        st = Path(entry).stat()
        if st.st_uid != _AGENT_UID or st.st_gid != _AGENT_GID:
            os.chown(entry, _AGENT_UID, _AGENT_GID)
    except OSError:
        return False
    return True


def _make_owner_and_group_rw(entry: str) -> None:
    """Best-effort chmod ensuring owner+group have rw (+x for dirs).

    NAS volumes with POSIX ACL inheritance can land cloned files with
    owner=0 (e.g. `.git/config` arriving as `----rw----`). POSIX permission
    rules check the OWNER bits when the caller IS the owner — group bits
    only apply to non-owners — so an agent-owned file with empty owner
    perms is unreadable to the agent even though group has rw. We must
    set owner perms explicitly. chmod always respects the caller's
    capabilities; if chown failed earlier (we're not root), we still
    can't chmod files we don't own, so this is best-effort by design.
    """
    import stat as _stat

    try:
        st = Path(entry).stat()
        new_mode = (
            st.st_mode | _stat.S_IRUSR | _stat.S_IWUSR | _stat.S_IRGRP | _stat.S_IWGRP
        )
        if _stat.S_ISDIR(st.st_mode):
            new_mode |= _stat.S_IXUSR | _stat.S_IXGRP
        if new_mode != st.st_mode:
            Path(entry).chmod(new_mode)
    except OSError:
        pass


def _own_and_grant_rw(entry: str) -> int:
    """Chown + grant owner/group rw on one entry; return 1 if the chown failed."""
    failed = 0 if _chown_entry(entry) else 1
    _make_owner_and_group_rw(entry)
    return failed


def _iter_ownable_entries(workspace: Path) -> Iterator[str]:
    """Yield the workspace root then every entry, pruning the heavy trees.

    os.walk yields a directory's *contents*, not the directory entry itself, so
    the root is yielded explicitly — the agent must be able to create new
    top-level files in it. ``_PRUNE_DIRS`` are dropped in place so os.walk never
    descends into them: that is the speed the old ``.git``-only walk bought,
    without giving up working-tree writability.
    """
    yield str(workspace)
    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in _PRUNE_DIRS]
        for name in (*dirs, *files):
            yield str(Path(root) / name)


def _ensure_agent_owned(workspace: Path) -> None:
    """Chown + group-write the agent's workspace so uid 1000 can read AND write.

    Orchestrator runs as root, so everything it clones is root-owned. Agent
    containers run as uid 1000 and must be able to WRITE working-tree files
    (create/edit source, design docs) AND .git internals (index.lock, refs,
    packed-refs, objects) — otherwise writes fail with "Permission denied".
    Called after clone and on every ensure_workspace so legacy workspaces get
    repaired.

    We walk the WHOLE workspace (so the working tree is writable) but prune the
    large, gitignored, agent-regenerated trees in ``_PRUNE_DIRS`` so the walk
    stays fast. Restricting the walk to ``.git`` only (the previous approach)
    was fast but left the working tree root-owned — agents couldn't write any
    file. If the workspace doesn't exist yet, we no-op.

    Two cheap, idempotent defenses per entry (see ``_own_and_grant_rw``):
    1. chown to (AGENT_UID, AGENT_GID). If the chown is rejected (rootless /
       userns hosts) we log the failure instead of swallowing it, so a
       still-failing agent write is diagnosable rather than silent.
    2. chmod owner+group rw. Belt + suspenders for ACL-inheriting NAS volumes.
    """
    if not workspace.exists():
        return

    failed_chowns = sum(
        _own_and_grant_rw(entry) for entry in _iter_ownable_entries(workspace)
    )

    if failed_chowns:
        logger.warning(
            "Some chowns failed during ensure_agent_owned — "
            "agent writes may still fail. Check docker user-namespace "
            "config or run agents as root on this host.",
            workspace=str(workspace),
            failures=failed_chowns,
        )


def _resolve_clone_root(workspace: Path) -> Path:
    """The clone root for a workspace or one of its linked worktrees.

    ``.venv`` and ``.uv-python`` live at the clone root and are shared by every
    worktree under ``{clone_root}/.worktrees/{id}/``. Given a worktree path,
    return its clone root; given the clone root itself, return it unchanged.
    Pure path logic keyed on the ``.worktrees`` layout from ``get_worktree_path``
    — no git call needed.
    """
    if workspace.parent.name == ".worktrees":
        return workspace.parent.parent
    return workspace


def _uv_subprocess_env(workspace: Path) -> dict[str, str]:
    """Env for a uv subprocess run by the orchestrator (root).

    Pins ``UV_PYTHON_INSTALL_DIR`` to ``<clone_root>/.uv-python`` so a non-system
    Python (e.g. 3.14) uv fetches lands INSIDE the workspace bind mount — not in
    ``/root/.local/share/uv/python`` (root-owned, ``/root`` is 0700, outside the
    mount). The workspace ``.venv/bin/python`` then symlinks to an agent-owned
    CPython on the shared volume, which ``_ensure_agent_owned`` chowns (``.uv-python``
    is not in ``_PRUNE_DIRS``), so the agent (uid 1000) can traverse it. Without
    this every ``uv run`` died on ``Permission denied`` canonicalizing the venv
    symlink (live be-dev-1 brick). Per-workspace → per-project isolation intact.

    Worktree-aware (F123): when the CWD is a per-task worktree, resolve up to the
    clone root so the shared ``.uv-python`` is reused instead of re-fetching a
    managed CPython per worktree.
    """
    env = dict(os.environ)
    clone_root = _resolve_clone_root(workspace)
    env["UV_PYTHON_INSTALL_DIR"] = str(clone_root / ".uv-python")
    return env


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


def _inject_token_into_url(git_url: str, token: str | None) -> str:
    """
    Inject GitHub PAT into HTTPS git URL for authentication.

    Args:
        git_url: Original git URL (SSH or HTTPS)
        token: GitHub PAT (if None, returns original URL)

    Returns:
        URL with embedded token for HTTPS, or original URL for SSH

    Example:
        https://github.com/org/repo.git -> https://TOKEN@github.com/org/repo.git
    """
    if not token:
        return git_url

    # Only inject for HTTPS URLs
    if not git_url.startswith("https://"):
        return git_url

    # Check if token already present
    if "@" in git_url.split("//")[1].split("/", maxsplit=1)[0]:
        return git_url

    # Inject token: https://github.com -> https://TOKEN@github.com
    return re.sub(r"^https://", f"https://{token}@", git_url)


class WorkspaceError(Exception):
    """Raised when workspace operations fail."""

    pass


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


def _lockfile_digest(workspace: Path) -> str | None:
    """Hash the dependency lockfiles present in the workspace.

    Returns a stable digest over whichever of `uv.lock` / `pnpm-lock.yaml` /
    `package-lock.json` exist, or None when none do (nothing to install).
    Used to make the post-clone install idempotent: if the digest matches
    the marker from the previous run, the install is skipped.
    """
    import hashlib

    lockfiles = ("uv.lock", "pnpm-lock.yaml", "package-lock.json", "package.json")
    h = hashlib.sha256()
    found = False
    for name in lockfiles:
        path = workspace / name
        if not path.is_file():
            continue
        found = True
        try:
            h.update(name.encode())
            h.update(path.read_bytes())
        except OSError:
            return None
    return h.hexdigest() if found else None


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
        return self.root / project_slug / team_str / agent_slug

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
        return clone_root / ".worktrees" / task_short_id

    @staticmethod
    def _worktree_git(
        clone_root: Path, args: list[str], check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(clone_root), *args],
            capture_output=True,
            text=True,
            check=check,
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
        worktree.mkdir(parents=True, exist_ok=True)
        link.symlink_to("../../.venv")

    async def ensure_worktree(
        self, clone_root: Path, worktree: Path, branch: str, base: str
    ) -> None:
        """Create the per-task linked worktree on ``branch`` from ``base``.

        Idempotent: a present, registered worktree is left in place (re-claim,
        re-spawn). A new branch uses ``git worktree add -b <branch> <base>``; an
        already-existing branch (re-claim after rollback) reuses it with
        ``worktree add <branch>``. Then symlinks the shared clone-root venv and
        chowns BOTH the worktree and the clone root (shared ``.git/worktrees`` /
        ``.venv`` / ``.uv-python``). F123: replaces the shared-clone
        ``reset --hard`` + ``checkout -b`` that clobbered a still-active root.
        """
        if not (worktree.exists() and (worktree / ".git").is_file()):
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
        await asyncio.to_thread(_ensure_agent_owned, worktree)
        await asyncio.to_thread(_ensure_agent_owned, clone_root)

    async def ensure_worktree_for_resume(
        self, clone_root: Path, worktree: Path, branch: str
    ) -> None:
        """Re-add a pruned/evicted worktree on resume (no ``-b`` — branch exists).

        Committed work survives in the branch ref; only the working tree was
        removed (reaper / cancel / disk pressure). Idempotent: a present
        worktree is a no-op.
        """
        if not (worktree.exists() and (worktree / ".git").is_file()):
            res = self._worktree_git(
                clone_root, ["worktree", "add", str(worktree), branch], check=False
            )
            if res.returncode != 0:
                raise WorkspaceError(
                    f"git worktree re-add failed for {branch}: {res.stderr.strip()}"
                )
        self._link_shared_venv(worktree, clone_root)
        await asyncio.to_thread(_ensure_agent_owned, worktree)
        await asyncio.to_thread(_ensure_agent_owned, clone_root)

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
        neither an object id nor a symref. Reads files only — no per-ref
        subprocess — so it stays cheap even on a many-branch monorepo clone.
        """
        refs_dir = workspace / ".git" / "refs"
        if not refs_dir.is_dir():
            return
        try:
            for ref_file in refs_dir.rglob("*"):
                if not ref_file.is_file():
                    continue
                if ref_file.suffix == ".bak":
                    ref_file.unlink(missing_ok=True)
                    continue
                content = ref_file.read_text(encoding="utf-8", errors="replace").strip()
                if not (
                    _REF_OBJECT_ID_RE.match(content) or content.startswith("ref: ")
                ):
                    logger.debug(
                        "ensure_workspace: pruning broken loose ref",
                        ref=str(ref_file),
                    )
                    ref_file.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(
                "ensure_workspace: broken-ref prune failed",
                workspace=str(workspace),
                error=str(exc),
            )

    @staticmethod
    async def _fetch_origin_best_effort(workspace: Path, project_slug: str) -> None:
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

        No `-c http.extraheader=…` token injection: the orchestrator did
        the original clone with a token but `_configure_git()` already
        scrubbed it from `.git/config`, and the *fetch* path here runs
        from inside the orchestrator container against the credential-
        stripped remote URL. Public repos and refresh-only fetches succeed
        without auth; auth-protected refreshes will surface their stderr
        in the warning log without aborting workspace setup.

        Timeout uses `workspace_refresh_fetch_timeout_seconds` (default
        60s), NOT `workspace_clone_timeout` (300s) — a refresh transfers
        small deltas, so 300s of blocking on every spawn against a hung
        remote is operationally bad.
        """

        def _git(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["git", *args],
                cwd=str(workspace),
                capture_output=True,
                text=True,
                check=False,
            )

        def _scoped_refs() -> list[str]:
            """The current branch + the repo's default branch, deduped."""
            current = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
            origin_head = _git(
                "symbolic-ref", "--short", "refs/remotes/origin/HEAD"
            ).stdout.strip()
            default = origin_head.split("/", 1)[1] if "/" in origin_head else "master"
            refs: list[str] = []
            for ref in (current, default):
                if ref and ref != "HEAD" and ref not in refs:
                    refs.append(ref)
            return refs or ["master"]

        def _do_fetch() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["git", "fetch", "--no-tags", "--prune", "origin", *_scoped_refs()],
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=settings.workspace_refresh_fetch_timeout_seconds,
                check=False,
            )

        try:
            result = await asyncio.to_thread(_do_fetch)
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.warning(
                "ensure_workspace: refresh fetch failed",
                workspace=str(workspace),
                project=project_slug,
                error=str(exc),
            )
            return

        if result.returncode != 0:
            stderr = result.stderr.strip()
            # The credential-less refresh fetch is expected to fail for private
            # repos — see this method's docstring. Downgrade the known-benign
            # auth-failure signature to DEBUG so it doesn't pollute every
            # monitor / log scrape during a smoke run. Genuine failures
            # (network errors, broken remotes) still surface at WARNING.
            is_expected_auth_fail = (
                "could not read Username" in stderr
                or "Authentication failed" in stderr
                or "remote: Repository not found" in stderr
            )
            log = logger.debug if is_expected_auth_fail else logger.warning
            log(
                "ensure_workspace: refresh fetch returned non-zero",
                workspace=str(workspace),
                project=project_slug,
                stderr=stderr,
                expected_auth_fail=is_expected_auth_fail,
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
            # Healthy clone — nothing to do except make sure it's still
            # owned by the agent user. Orchestrator restarts or older
            # clones (pre-ownership-fix) may leave root-owned trees that
            # break every subsequent write from inside the agent container.
            #
            # `.git` existing is necessary but NOT sufficient — a failed
            # clone can leave behind a stub .git/ with only FETCH_HEAD and
            # no HEAD/objects, which looks "healthy" to a naive check but
            # breaks every subsequent fetch/checkout ("origin/<branch> is
            # not a commit"). Require HEAD + objects/ as the real signal.
            if self._is_workspace_healthy(workspace):
                await asyncio.to_thread(_ensure_agent_owned, workspace)
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
                if force or (now - last_fetch) >= _FETCH_CACHE_TTL_SECONDS:
                    # Repair broken-ref debris first so the fetch (and the
                    # agent's later `git diff/log origin/...`) doesn't trip on a
                    # ref left corrupt by an interrupted recovery.
                    await asyncio.to_thread(self._prune_broken_refs, workspace)
                    await self._fetch_origin_best_effort(workspace, project_slug)
                    self._fetch_cache[str(workspace)] = _monotonic()
                # Re-chown so the agent user can still write into .git
                # after our root-side fetch updated refs/objects. Mirrors
                # the pattern in `fetch_branch_for_inspection` — without
                # this, new pack files under .git/objects/pack/ and ref
                # updates under .git/refs/remotes/origin/ land root-owned
                # and undo the chown we just ran above.
                await asyncio.to_thread(_ensure_agent_owned, workspace)
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
                shutil.rmtree(workspace)

            project_service = get_project_service(self.session)
            project = await project_service.get_by_slug(project_slug)
            if not project:
                raise WorkspaceError(f"Project not found: {project_slug}")

            if not git_url:
                git_url = project.git_url
                default_branch = project.default_branch or default_branch

            git_token = await self._resolve_git_token(
                project_service, project_slug, git_url
            )

            await self._clone_repo(
                workspace,
                git_url,
                default_branch,
                git_token,
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

    async def ensure_read_clone(self, project_slug: str) -> Path:
        """Ensure a project-level read clone pinned to the default branch's HEAD.

        The architectural-conventions standard is read from the committed
        ``.roboco/conventions.yml`` plus a scan of the repo tree. Per-agent
        working clones are the wrong source — one may sit on a feature branch or
        be stale — so metadata reads use this dedicated clone instead. It is
        never mounted into an agent container and is always hard-reset to
        ``origin/<default_branch>``, which makes destructive refresh safe. This
        is what lets the standard work for a project created before the standard
        existed: no manually-configured ``workspace_path`` is required.
        """
        from roboco.services.project import get_project_service

        project_service = get_project_service(self.session)
        project = await project_service.get_by_slug(project_slug)
        if not project:
            raise WorkspaceError(f"Project not found: {project_slug}")
        default_branch = project.default_branch or "master"
        git_url = project.git_url
        workspace = self.root / project_slug / "_meta" / "conventions"

        lock = _ensure_lock_for(project_slug, "_meta-conventions")
        async with lock:
            if self._is_workspace_healthy(workspace):
                now = _monotonic()
                last = _read_clone_synced.get(str(workspace), -math.inf)
                if (now - last) >= _READ_CLONE_FETCH_TTL_SECONDS:
                    token = await self._read_clone_token(project_service, project_slug)
                    await asyncio.to_thread(self._prune_broken_refs, workspace)
                    await asyncio.to_thread(
                        self._sync_read_clone,
                        workspace,
                        git_url,
                        default_branch,
                        token,
                    )
                    _read_clone_synced[str(workspace)] = _monotonic()
                return workspace
            if workspace.exists():
                shutil.rmtree(workspace)
            git_token = await self._resolve_git_token(
                project_service, project_slug, git_url
            )
            await self._clone_repo(
                workspace, git_url, default_branch, git_token, agent=None
            )
            _read_clone_synced[str(workspace)] = _monotonic()
            return workspace

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
    def _sync_read_clone(
        workspace: Path,
        git_url: str,
        default_branch: str,
        git_token: str | None,
    ) -> None:
        """Token-authenticated fetch + hard-reset of the read clone to origin's
        default branch. Best-effort: logs and returns on failure.

        ``_clone_repo`` scrubs the token from ``.git/config`` (the secret-exfil
        mitigation), so a plain ``git fetch origin`` cannot authenticate against
        a PRIVATE repo — the refresh silently fails and the clone stays frozen at
        clone-time, never seeing commits merged afterwards. The read clone runs
        orchestrator-side and is never mounted into an agent container, so the
        token is injected transiently into the fetch argv (mirroring the clone)
        to keep a private repo current.
        """
        auth_url = _inject_token_into_url(git_url, git_token)

        def _git(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["git", *args],
                cwd=str(workspace),
                capture_output=True,
                text=True,
                check=False,
            )

        fetched = _git("fetch", "--no-tags", auth_url, default_branch)
        if fetched.returncode != 0:
            logger.warning(
                "conventions read-clone fetch failed",
                workspace=str(workspace),
                error=(fetched.stderr or fetched.stdout).strip()[:200],
            )
            return
        _git("checkout", default_branch)
        _git("reset", "--hard", "FETCH_HEAD")

    async def _clone_repo(
        self,
        workspace: Path,
        git_url: str,
        default_branch: str,
        git_token: str | None = None,
        agent: AgentTable | None = None,
    ) -> None:
        """
        Clone a git repository to the workspace.

        Args:
            workspace: Target directory
            git_url: Git URL to clone
            default_branch: Branch to checkout
            git_token: GitHub PAT for authentication (per-project)
            agent: Agent for git identity (name/email in commits)

        Raises:
            WorkspaceError: If clone fails
        """
        # Create parent directories
        workspace.parent.mkdir(parents=True, exist_ok=True)

        # Inject project-specific token for HTTPS URLs
        auth_url = _inject_token_into_url(git_url, git_token)

        # Log without exposing token
        logger.info(
            "Cloning repository",
            workspace=str(workspace),
            git_url=git_url,  # Log original URL, not auth URL
            branch=default_branch,
            using_token=bool(git_token and auth_url != git_url),
        )

        def _do_clone() -> subprocess.CompletedProcess[str]:
            # Do NOT pass --single-branch. Agents work on feature branches
            # pushed by peers; QA and documenter need to `git fetch` those
            # branches after a dev pushes. --single-branch locks the remote
            # refspec to `+refs/heads/{default_branch}:refs/remotes/origin/
            # {default_branch}`, so subsequent fetches silently ignore every
            # other branch. The symptom is QA doing `checkout origin/
            # feature/...` and seeing "not a commit" even though the branch
            # is on GitHub. --no-tags keeps the clone light.
            return subprocess.run(
                [
                    "git",
                    "clone",
                    "--branch",
                    default_branch,
                    "--no-tags",
                    auth_url,
                    str(workspace),
                ],
                capture_output=True,
                text=True,
                timeout=settings.workspace_clone_timeout,
                check=True,
            )

        def _configure_git() -> None:
            """Configure git author info + scrub embedded PAT from remote URL.

            The clone URL carries the PAT for authentication (`https://TOKEN@
            github.com/...`), which `git clone` then writes into
            `.git/config`. Leaving it there lets anyone with read access to
            the workspace — including the agent inside its container — read
            the token and exfiltrate or use it directly against GitHub,
            bypassing the orchestrator's git service.

            We keep push/fetch working by letting the orchestrator inject
            the token just-in-time at the subprocess level (`-c
            http.extraheader='Authorization: bearer TOKEN'`) when it needs
            to hit origin; see GitService.
            """
            name = agent.name if agent else "RoboCo Agent"
            slug = agent.slug if agent else "agent"

            subprocess.run(
                ["git", "config", "user.name", name],
                cwd=str(workspace),
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.email", f"{slug}@roboco.tech"],
                cwd=str(workspace),
                check=True,
                capture_output=True,
            )
            # Disable filesystem mode tracking. The workspace volumes live
            # on the NAS (`/volume1/...`), which has POSIX ACL inheritance
            # that gives every cloned file the executable bit. With the
            # default `core.fileMode = true`, git treats every tracked
            # file as modified the moment it's cloned, and `task_start`'s
            # clean-tree check refuses to checkout the feature branch.
            subprocess.run(
                ["git", "config", "core.fileMode", "false"],
                cwd=str(workspace),
                check=True,
                capture_output=True,
            )

            # Scrub embedded credentials from the remote URL.
            if git_token and auth_url != git_url:
                subprocess.run(
                    ["git", "remote", "set-url", "origin", git_url],
                    cwd=str(workspace),
                    check=True,
                    capture_output=True,
                )

        def _assert_no_pat_leak() -> None:
            """Fail-fast if a PAT ended up anywhere under .git/ on disk.

            Belt-and-suspenders: if the scrub above ever regresses (e.g. a
            refactor skips `remote set-url`, or git starts writing the auth
            URL to a new file), this catches it before the agent container
            gets mounted on the workspace. The whole workspace is removed
            on failure — a leaked workspace is unrecoverable.
            """
            git_dir = workspace / ".git"
            if not git_dir.exists():
                return
            leaked_in: list[Path] = []
            for path in git_dir.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    data = path.read_bytes()
                except OSError:
                    continue
                # Token shapes: classic (ghp_…), fine-grained (github_pat_…),
                # x-access-token URL pattern. Checking bytes avoids UTF-8
                # decode errors on pack files / binary blobs.
                if (
                    b"ghp_" in data
                    or b"github_pat_" in data
                    or b"x-access-token:" in data
                ):
                    leaked_in.append(path.relative_to(workspace))
            if leaked_in:
                shutil.rmtree(workspace, ignore_errors=True)
                raise WorkspaceError(
                    f"PAT leak detected under .git/ after clone: {leaked_in}. "
                    "Workspace destroyed. Check _configure_git() scrub step."
                )

        try:
            await asyncio.to_thread(_do_clone)
            await asyncio.to_thread(_configure_git)
            await asyncio.to_thread(_assert_no_pat_leak)
            # Transfer ownership to the agent user so the agent can write
            # into .git/ and the working tree from inside its container.
            await asyncio.to_thread(_ensure_agent_owned, workspace)
            logger.info(
                "Repository cloned successfully",
                workspace=str(workspace),
            )
        except subprocess.CalledProcessError as e:
            # a failure anywhere in clone/configure/leakcheck/own leaves a
            # half-configured workspace whose .git/config may still carry the
            # tokenized auth URL (the project PAT). Destroy it so the next
            # ensure_workspace re-clones from scratch — fail-closed against
            # PAT exfiltration.
            shutil.rmtree(workspace, ignore_errors=True)
            raise WorkspaceError(
                f"Failed to clone repository: {e.stderr or e.stdout}"
            ) from e
        except subprocess.TimeoutExpired as e:
            # Same PAT-leak hygiene as the CalledProcessError branch.
            shutil.rmtree(workspace, ignore_errors=True)
            raise WorkspaceError(
                f"Clone timed out after {settings.workspace_clone_timeout}s"
            ) from e

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
        runs each install from the workspace root. Best-effort — failures are
        logged, never raised, so a missing toolchain on the host or a flaky
        registry can't break the agent's clone.

        Returns True when an install ran (and at least one command
        succeeded), False when skipped (cache hit, disabled, or nothing to
        install).
        """
        if not settings.workspace_install_dev_deps:
            return False

        # When toolchain matching is on, provision against the target project's
        # declared Python (uv resolves + fetches it) instead of the system 3.13.
        target_python = self._resolve_toolchain_target(workspace)

        commands = _detect_dep_commands(workspace, target_python=target_python)
        if not commands:
            return False

        digest = _lockfile_digest(workspace)
        if self._dep_install_cache_hit(workspace, digest):
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
                (workspace / _DEP_INSTALL_MARKER).write_text(digest)

        # The install runs as root (orchestrator); hand the freshly written
        # .venv / node_modules back to the agent user.
        await asyncio.to_thread(_ensure_agent_owned, workspace)
        await self._record_toolchain(workspace, target_python)
        return any_ok

    @staticmethod
    def _resolve_toolchain_target(workspace: Path) -> str | None:
        """The Python version to provision with, or None (flag off / nothing
        declared → today's behavior)."""
        if not settings.toolchain_match_enabled:
            return None
        resolved = resolve_target_python(workspace)
        return resolved.version if resolved else None

    async def _record_toolchain(
        self, workspace: Path, python: str | None, *, only_if_missing: bool = False
    ) -> None:
        """Run the runnability smoke and write the toolchain marker (best-effort).

        Inert when ``python`` is None (flag off / no target version).
        """
        if python is None:
            return
        if only_if_missing and (workspace / _TOOLCHAIN_MARKER).is_file():
            return
        status = await self._run_toolchain_smoke(workspace, python)
        with contextlib.suppress(OSError):
            (workspace / _TOOLCHAIN_MARKER).write_text(
                json.dumps({"python": python, "status": status})
            )

    @staticmethod
    async def _run_toolchain_smoke(workspace: Path, python: str) -> str:
        """Can the project's suite be collected under ``python``?

        Returns ``ok`` (collected, or no tests), ``broken`` (collection/import
        error — the interpreter-mismatch signature), or ``unknown`` (pytest
        absent, tool missing, timeout — never block on these). Precision over
        recall: only a genuine collection error reports ``broken``.
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

        def _run() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                argv,
                cwd=str(workspace),
                env=_uv_subprocess_env(workspace),
                capture_output=True,
                text=True,
                timeout=settings.workspace_dep_install_timeout_seconds,
                check=False,
            )

        try:
            result = await asyncio.to_thread(_run)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
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
        or unreadable) — callers must treat that as 'do not block'.
        """
        marker = workspace / _TOOLCHAIN_MARKER
        if not marker.is_file():
            return (None, None)
        try:
            data = json.loads(marker.read_text())
        except (OSError, json.JSONDecodeError):
            return (None, None)
        return (data.get("python"), data.get("status"))

    @staticmethod
    def _dep_install_cache_hit(workspace: Path, digest: str | None) -> bool:
        """True when the lockfile digest matches the marker from a prior run."""
        if digest is None:
            return False
        marker = workspace / _DEP_INSTALL_MARKER
        if not marker.is_file():
            return False
        try:
            return marker.read_text().strip() == digest
        except OSError:
            return False

    @staticmethod
    async def _run_dep_install(workspace: Path, label: str, argv: list[str]) -> bool:
        """Run one dep-install command; log and swallow all failures.

        Returns True only when the tool exists and exited 0.
        """

        def _run() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                argv,
                cwd=str(workspace),
                env=_uv_subprocess_env(workspace),
                capture_output=True,
                text=True,
                timeout=settings.workspace_dep_install_timeout_seconds,
                check=False,
            )

        logger.info(
            "Installing workspace dev dependencies",
            workspace=str(workspace),
            command=label,
        )
        try:
            result = await asyncio.to_thread(_run)
        except FileNotFoundError:
            # The tool (uv/pnpm/npm) isn't on the orchestrator's PATH. Log and
            # continue — the agent can still install on the fly inside its
            # container, which has the toolchain.
            logger.warning(
                "Dev-deps install tool not found on host; skipping",
                workspace=str(workspace),
                command=label,
            )
            return False
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.warning(
                "Dev-deps install failed",
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
        tmp = Path(tempfile.mkdtemp(prefix="dep-probe-"))
        try:
            clone_dir = tmp / "repo"
            timeout = settings.workspace_dep_install_timeout_seconds
            lock = _ensure_lock_for(slug, "_meta-conventions")
            async with lock:
                await asyncio.to_thread(
                    self._clone_local_into, read_clone, clone_dir, timeout
                )
            return await asyncio.to_thread(
                self._probe_lockfile_on_clone, clone_dir, command, lock_paths
            )
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
        subprocess.run(
            [
                "git",
                "clone",
                "--local",
                "--no-hardlinks",
                str(read_clone),
                str(clone_dir),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )

    @staticmethod
    def _probe_lockfile_on_clone(
        clone_dir: Path, command: str, lock_paths: list[str]
    ) -> bool:
        """Run the upgrade on the independent local clone + report dirty (in a thread).

        Runs the upgrade with no shell (``shlex.split``); a non-zero upgrade
        yields False (fail-safe, don't originate on a broken probe). The lock is
        NOT held here — the clone is a full independent copy and the upgrade
        never touches the read clone.
        """
        timeout = settings.workspace_dep_install_timeout_seconds
        upgrade = subprocess.run(
            shlex.split(command),
            cwd=str(clone_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if upgrade.returncode != 0:
            logger.warning(
                "dep-update probe: upgrade command non-zero; not originating",
                command=command,
                stderr=upgrade.stderr.strip()[:2000],
            )
            return False
        status = subprocess.run(
            ["git", "status", "--porcelain", "--", *lock_paths],
            cwd=str(clone_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
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
    ) -> Path:
        """Fetch `branch_name` into the inspecting agent's workspace.

        QA / Documenter / PM agents need to read a developer's branch from
        their own workspace before diffing. This adapter:

        1. Resolves the project from the branch (via the owning task).
        2. Ensures a healthy workspace for `agent_id` on that project
           (clones if missing — same path as the agent's first claim).
        3. Runs `git fetch origin <branch>` with the project token so the
           branch ref is locally available for `git diff`.

        Returns the workspace path so the caller can chain checkout/diff
        operations if needed.
        """
        from roboco.services.project import get_project_service

        project_slug = await self._resolve_branch_to_project_slug(branch_name)
        workspace = await self.ensure_workspace(
            project_slug=project_slug,
            agent_id=agent_id,
        )

        from roboco.utils.crypto import EncryptionError

        project_service = get_project_service(self.session)
        project = await project_service.get_by_slug(project_slug)
        git_token: str | None = None
        if project is not None:
            try:
                git_token = await project_service.get_decrypted_token_by_slug(
                    project_slug
                )
            except EncryptionError:
                # Token-decrypt failure (rotated key / corrupted record) is
                # non-fatal here: a public branch fetch still works without
                # auth, and a real auth failure surfaces from git below.
                git_token = None

        prefix: list[str] = []
        if git_token:
            import base64

            basic = base64.b64encode(f"x-access-token:{git_token}".encode()).decode()
            prefix = ["-c", f"http.extraheader=Authorization: Basic {basic}"]

        def _do_fetch() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["git", *prefix, "fetch", "origin", branch_name],
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=settings.workspace_clone_timeout,
                check=False,
            )

        result = await asyncio.to_thread(_do_fetch)
        if result.returncode != 0:
            logger.warning(
                "fetch_branch_for_inspection: fetch returned non-zero",
                branch=branch_name,
                workspace=str(workspace),
                stderr=result.stderr.strip(),
            )
        # Re-chown so the agent user can still write into .git after our
        # root-side fetch updated refs/objects.
        await asyncio.to_thread(_ensure_agent_owned, workspace)
        return workspace

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
        import shutil

        workspace = await self.resolve_workspace(project_slug, agent_id)
        if not workspace.exists():
            return False

        logger.warning(
            "Deleting workspace",
            workspace=str(workspace),
        )

        def _do_delete() -> None:
            shutil.rmtree(workspace)

        await asyncio.to_thread(_do_delete)
        return True


def get_workspace_service(session: AsyncSession) -> WorkspaceService:
    """Factory function to get workspace service."""
    return WorkspaceService(session)
