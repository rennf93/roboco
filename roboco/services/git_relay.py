"""The relay: every origin round-trip, and the only use of a project's token.

Each project gets a bare repository under ``<private_data_root>/relays/``,
owned by root and mounted into no agent container. All traffic with origin
happens there, against the project's URL from the database:

- fetch: origin → relay (token), then relay → workspace as the git user (no
  token; local transport);
- push: the commit is taken OUT of the workspace by ``upload-pack`` running
  as the git user (root never opens the clone), staged under
  ``refs/roboco/incoming/``, then pushed relay → origin (token);
- ls-remote: relay → origin (token).

So the token never meets anything an agent can write: not a clone's
``.git/config`` (no ``url.insteadOf`` can redirect it), not a hook, not a
process an agent's config can start. It travels as ``GIT_CONFIG_*``
environment entries scoped to the origin host (``http.<origin>/.extraheader``)
— never argv, never a file — of root-run git in the relay only.

``run_private_git`` runs git in the other dispatcher-owned repositories under
``private_data_root`` (the read clone, the release clone, scratch clones):
trusted content, so root, with the same environment hygiene and no token.
"""

from __future__ import annotations

import base64
import contextlib
import fcntl
import os
import shlex
import subprocess
import threading
import uuid
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from roboco.config import settings
from roboco.logging import get_logger
from roboco.models.env_branches import head_branch
from roboco.services.workspace_git import (
    SAFE_PATH,
    remove_workspace_tree,
    workspace_git_env,
    workspace_git_overrides,
    workspace_repository,
    workspace_repository_env,
    workspace_user_argv,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from typing import Any

logger = get_logger(__name__)

INCOMING_REF_PREFIX = "refs/roboco/incoming/"

# Program settings pinned on root-run git too: a hook or helper here would run
# as root. Hooks are off entirely in dispatcher-owned repos.
_PRIVATE_PROGRAM_OVERRIDES = (
    "core.hooksPath=/dev/null",
    "core.fsmonitor=false",
    "core.sshCommand=false",
    "core.askPass=false",
    "core.pager=cat",
    "credential.helper=",
    "submodule.recurse=false",
    "fetch.recurseSubmodules=false",
    "push.recurseSubmodules=no",
    "commit.gpgSign=false",
    "tag.gpgSign=false",
)

_relay_thread_locks: dict[str, threading.Lock] = {}
_relay_thread_locks_guard = threading.Lock()


def private_root() -> Path:
    """The orchestrator-only data root (never mounted into an agent)."""
    return Path(settings.private_data_root)


def is_dispatcher_owned(path: Path) -> bool:
    """True for repositories under the private root (relay, read, release)."""
    try:
        Path(os.path.normpath(path)).relative_to(private_root())
    except ValueError:
        return False
    return True


def read_clone_path(project_slug: str) -> Path:
    """The project's conventions / release-readiness read clone."""
    return private_root() / "read" / project_slug


def release_clone_path(project_slug: str) -> Path:
    """The project's writable release clone."""
    return private_root() / "release" / project_slug


_PRE_MOVE_COPIES_REMOVED = "pre-move-copies-removed"


def remove_pre_move_copies() -> None:
    """Delete, once, the read and release clones the old layout kept under
    the workspaces root (``<slug>/_meta/conventions``, ``_release/<slug>``).

    Both now live under the private root, so nothing reads the old copies.
    Agents write the workspaces root, so a tree counts as an old copy only
    when it is still exactly that layout of real directories holding a
    ``.git``, and it is removed with ``remove_workspace_tree`` (no symlink
    followed). A marker in the private root records a pass that removed
    everything it found, so a tree created at those paths later is never
    touched; the lock keeps a second dispatcher (blue-green) out of a pass
    already running. Best-effort: a pass that fails is logged and retried on
    the next start.
    """
    try:
        _remove_pre_move_copies_once(private_root() / _PRE_MOVE_COPIES_REMOVED)
    except OSError as e:
        logger.warning("removing pre-move copies failed", error=str(e))


def _remove_pre_move_copies_once(marker: Path) -> None:
    if marker.exists():
        return
    marker.parent.mkdir(parents=True, exist_ok=True)
    with marker.with_name(f"{marker.name}.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        if marker.exists():
            return
        if _remove_trees(_pre_move_copies(Path(settings.workspaces_root))):
            marker.touch()


def _pre_move_copies(workspaces_root: Path) -> list[Path]:
    """``<slug>/_meta`` holding only a ``conventions`` clone, and
    ``_release`` holding only clones; anything else there is left alone."""
    if not _is_real_dir(workspaces_root):
        return []
    copies = [
        project / "_meta"
        for project in sorted(workspaces_root.iterdir())
        if _is_real_dir(project)
        and _holds_only_clones(project / "_meta", names={"conventions"})
    ]
    release = workspaces_root / "_release"
    if _holds_only_clones(release, names=None):
        copies.append(release)
    return copies


def _holds_only_clones(directory: Path, *, names: set[str] | None) -> bool:
    if not _is_real_dir(directory):
        return False
    entries = list(directory.iterdir())
    if not entries or (names is not None and {e.name for e in entries} != names):
        return False
    return all(_is_real_dir(e) and _is_real_dir(e / ".git") for e in entries)


def _is_real_dir(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink()


def _remove_trees(trees: list[Path]) -> bool:
    """Remove each tree; False when one could not be (retried next start)."""
    removed_all = True
    for tree in trees:
        try:
            remove_workspace_tree(tree)
        except OSError as e:
            removed_all = False
            logger.warning("pre-move copy not removed", path=str(tree), error=str(e))
        else:
            logger.info("pre-move copy removed", path=str(tree))
    return removed_all


def _private_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    global_config = Path(settings.git_support_dir) / "private.gitconfig"
    env = {
        "PATH": SAFE_PATH,
        "HOME": "/nonexistent",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        # safe.directory=* lives here: the private root is shared by both
        # deploy colors, so its repositories need not be owned by the uid
        # running git. Root-owned in the image; no agent and not the git
        # user can write it.
        "GIT_CONFIG_GLOBAL": str(global_config)
        if global_config.is_file()
        else "/dev/null",
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "false",
        "GIT_EDITOR": "true",
        "GIT_PAGER": "cat",
    }
    env.update(extra or {})
    return env


def _private_overrides(*, network_scheme: str | None = None) -> list[str]:
    pairs = [*_PRIVATE_PROGRAM_OVERRIDES, "protocol.allow=never"]
    pairs.append("protocol.file.allow=always")
    if network_scheme in {"https", "http"}:
        pairs.append(f"protocol.{network_scheme}.allow=always")
    return [arg for pair in pairs for arg in ("-c", pair)]


def run_private_git(
    cwd: Path,
    args: Sequence[str],
    *,
    timeout: float,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run ``git <args>`` in a dispatcher-owned repository (root, no network).

    Only the local ``file`` transport is open: these repositories reach
    origin through the relay alone.
    """
    return _run_root_git(
        cwd,
        [*_private_overrides(), *args],
        timeout=timeout,
        check=check,
        env=_private_env(),
    )


def _run_root_git(
    cwd: Path,
    git_args: Sequence[str],
    *,
    timeout: float,
    check: bool,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    argv = ["git", *git_args]
    result = subprocess.run(
        argv,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        umask=0o022,
    )
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, argv, result.stdout, result.stderr
        )
    return result


class GitRelay:
    """One project's bare relay repository and its origin round-trips.

    Methods block; callers run them off the event loop. Mutations of the
    relay's refs are serialized per project across threads and processes
    (the orchestrator and the dispatcher share it); a push to origin only
    reads the relay and runs unlocked.
    """

    def __init__(
        self,
        project_slug: str,
        git_url: str,
        token: str | None,
        *,
        default_branch: str | None = None,
    ) -> None:
        self.project_slug = project_slug
        self.git_url = git_url
        self._token = token
        self._default_branch = default_branch
        parsed = urlparse(git_url)
        self._scheme = parsed.scheme or None
        self._origin_base = (
            f"{parsed.scheme}://{parsed.netloc}/" if parsed.netloc else None
        )

    @property
    def path(self) -> Path:
        """The bare repository."""
        return private_root() / "relays" / f"{self.project_slug}.git"

    # -- origin round-trips (token) ---------------------------------------

    def fetch_from_origin(
        self,
        refspecs: Sequence[str],
        *,
        prune: bool = False,
        tags: bool = False,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        """``git fetch <origin> <refspecs>`` into the relay."""
        args = ["fetch", "--prune" if prune else "--no-prune"]
        args += ["--tags", "--force"] if tags else ["--no-tags"]
        with self._locked():
            self._ensure_repository(timeout)
            return self._origin_git([*args, self.git_url, *refspecs], timeout)

    def ls_remote(
        self, args: Sequence[str], *, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        """``git ls-remote <args...> <origin> [patterns]`` (patterns last)."""
        flags = [a for a in args if a.startswith("-")]
        patterns = [a for a in args if not a.startswith("-")]
        with self._locked():
            self._ensure_repository(timeout)
        return self._origin_git(["ls-remote", *flags, self.git_url, *patterns], timeout)

    def push_to_origin(
        self,
        refspecs: Sequence[str],
        *,
        leases: Sequence[str] = (),
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        """``git push [--force-with-lease=<ref>:<sha>...] <origin> <refspecs>``."""
        lease_args = [f"--force-with-lease={lease}" for lease in leases]
        return self._origin_git(["push", *lease_args, self.git_url, *refspecs], timeout)

    # -- moving commits between a repository and the relay (no token) -----

    def take_ref(
        self, repo: Path, src_ref: str, *, from_workspace: bool, timeout: float
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        """Copy ``src_ref`` of ``repo`` into ``refs/roboco/incoming/<id>``.

        For an agent workspace (``from_workspace``) the far end,
        ``upload-pack``, runs as the git user with the workspace env and
        overrides: root receives a pack stream and never opens the clone.
        Every object received is fsck'd: the pack holds only what the relay
        lacks, i.e. the commits an agent just made, so a crafted object (a
        ``.gitmodules`` URL that is an option, a malformed tree) is refused
        before it reaches root's repositories. Origin fetches are not
        fsck'd: real histories carry legacy errors (bad timezones, ...) that
        would make a project unclonable.
        Returns the fetch result and the staging ref (drop it when done).
        """
        incoming = f"{INCOMING_REF_PREFIX}{uuid.uuid4().hex}"
        args = ["-c", "fetch.fsckObjects=true", "fetch", "--no-tags"]
        args.append("--no-write-fetch-head")
        source = repo
        if from_workspace:
            # The repository by layout (its admin dir, so a worktree's own
            # HEAD), never by the clone's gitfile / commondir.
            source = workspace_repository(repo).git_dir
            upload_pack = _workspace_upload_pack(repo, timeout=timeout)
            args.append(f"--upload-pack={upload_pack}")
        args += [str(source), f"+{src_ref}:{incoming}"]
        with self._locked():
            self._ensure_repository(timeout)
            result = run_private_git(self.path, args, timeout=timeout, check=False)
        return result, incoming

    def set_ref(self, ref: str, sha: str, *, timeout: float) -> None:
        """Point a relay ref at ``sha`` (e.g. a branch just pushed)."""
        with self._locked():
            run_private_git(self.path, ["update-ref", ref, sha], timeout=timeout)

    def drop_ref(self, ref: str, *, timeout: float) -> None:
        """Delete a relay ref; best-effort."""
        with self._locked():
            run_private_git(
                self.path, ["update-ref", "-d", ref], timeout=timeout, check=False
            )

    # -- internals ---------------------------------------------------------

    def _origin_git(
        self, args: Sequence[str], timeout: float
    ) -> subprocess.CompletedProcess[str]:
        """Root git in the relay with origin's one transport and the token."""
        return _run_root_git(
            self.path,
            [*_private_overrides(network_scheme=self._scheme), *args],
            timeout=timeout,
            check=False,
            env=_private_env(self._token_env()),
        )

    def _token_env(self) -> dict[str, str]:
        """The token as host-scoped env config: never argv, never a file.

        GitHub smart HTTP authenticates with Basic ``x-access-token:<token>``
        (Gitea / GitLab accept the same header); scoping the key to the origin
        host means a redirect elsewhere never carries it.
        """
        if not self._token or self._origin_base is None:
            return {}
        basic = base64.b64encode(f"x-access-token:{self._token}".encode()).decode()
        return {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": f"http.{self._origin_base}.extraheader",
            "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
        }

    def _ensure_repository(self, timeout: float) -> None:
        if not (self.path / "HEAD").is_file():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            run_private_git(
                self.path.parent,
                ["init", "--bare", "--quiet", self.path.name],
                timeout=timeout,
            )
        if self._default_branch:
            # Clones of the relay get origin/HEAD from this.
            run_private_git(
                self.path,
                ["symbolic-ref", "HEAD", f"refs/heads/{self._default_branch}"],
                timeout=timeout,
            )

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        with _thread_lock_for(self.project_slug):
            lock_path = self.path.parent / f"{self.project_slug}.lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            with lock_path.open("a") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)


def relay_for(project: Any, token: str | None) -> GitRelay:
    """The relay of a project row: its slug, its URL from the database, its
    head branch as the relay's HEAD (clones get ``origin/HEAD`` from it)."""
    return GitRelay(
        str(project.slug),
        str(project.git_url),
        token,
        default_branch=head_branch(project),
    )


def _thread_lock_for(project_slug: str) -> threading.Lock:
    with _relay_thread_locks_guard:
        lock = _relay_thread_locks.get(project_slug)
        if lock is None:
            lock = threading.Lock()
            _relay_thread_locks[project_slug] = lock
        return lock


def _workspace_upload_pack(repo: Path, *, timeout: float) -> str:
    """``--upload-pack`` command: upload-pack as the git user, clean env,
    ``repo``'s common dir pinned (``GIT_COMMON_DIR`` wins over a planted
    ``commondir`` file), bounded by the fetch's own ``timeout``.

    git runs it through the shell with the repository path appended.
    """
    env = workspace_git_env()
    pinned = workspace_repository_env(repo)
    if "GIT_COMMON_DIR" in pinned:
        env["GIT_COMMON_DIR"] = pinned["GIT_COMMON_DIR"]
    env_pairs = [f"{key}={value}" for key, value in env.items()]
    argv = workspace_user_argv(
        ["env", "-i", *env_pairs, "git", *workspace_git_overrides(), "upload-pack"],
        timeout=timeout,
    )
    return shlex.join(argv)
