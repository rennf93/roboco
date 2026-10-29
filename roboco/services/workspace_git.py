"""Run git — and every other process — in agent workspaces as the git user.

Every agent container mounts every clone read-write, so an agent (possibly
prompt-injected by an external PR) controls every byte of every clone at any
moment: ``.git/config``, ``.git/hooks``, attributes, the working tree. Git run
here as root used to execute whatever that content pointed at (hooks,
fsmonitor, filter drivers, ``core.sshCommand``, ...) as root in the container
holding the Docker socket.

Workspace git now runs:

- as ``settings.git_user_uid`` with primary gid ``settings.agent_gid`` and no
  other groups, no capabilities, ``no_new_privs`` (the set-id helper
  ``as-git-user``, ``docker/as-git-user/``), umask 002, under an in-command
  ``timeout`` — never root, and never the agent uid (the host user owning the
  provider credentials mounted into the orchestrator). With
  ``workspace_git_drop=required`` (the orchestrator image) a missing or
  refusing helper raises ``GitUserDropError``: nothing falls back to running
  as this process;
- with an environment built from scratch: no secret of this process reaches
  git or anything git starts;
- with ``-c`` overrides for every setting that makes git start a program
  (command-line config beats every config file, so a clone cannot re-enable
  them): hooks only from the root-owned ``git_support_dir/hooks``, no
  fsmonitor, no ssh/askpass/editor/pager programs, no network protocol at all
  (only local ``file`` transport, which the relay uses), no submodule
  recursion, no signing programs;
- ``--no-ext-diff --no-textconv`` on diff/log/show;
- on the repository the layout says (``GIT_DIR`` / ``GIT_COMMON_DIR`` /
  ``GIT_WORK_TREE``), never one git discovers by following a file an agent
  can rewrite: a worktree's ``.git`` gitfile, any git dir's ``commondir``
  (which would move config, objects and attributes to a directory of the
  agent's choosing, past the audit below), or a ``.git`` above the clone.

What an override cannot neutralize — filter/diff/merge drivers, ``url``
rewrites, ``include`` files, alternates, ``core.worktree`` — the config audit
refuses before the command runs. Checking first leaves a window (the agent can
rewrite the file in between), which is why the privilege drop, not the audit,
is the boundary: the worst a winning race buys is code running as the git
user, which holds no token and reaches no secret. Tokens never enter these
processes at all: every origin round-trip goes through the dispatcher-owned
relay (``roboco.services.git_relay``).
"""

from __future__ import annotations

import contextlib
import math
import os
import shutil
import signal
import stat
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, Literal, Protocol

from roboco.config import settings
from roboco.logging import get_logger
from roboco.utils.workspace_paths import clone_root_of

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = get_logger(__name__)

SAFE_PATH = "/usr/local/bin:/usr/bin:/bin"

# Settings that start a program, pinned harmless on the command line.
_PROGRAM_OVERRIDES = (
    "core.fsmonitor=false",
    "core.sshCommand=false",
    "core.askPass=false",
    "core.editor=true",
    "core.pager=cat",
    "core.alternateRefsCommand=true",
    "core.attributesFile=/dev/null",
    "sequence.editor=true",
    "credential.helper=",
    "gpg.program=false",
    "gpg.ssh.program=false",
    "gpg.x509.program=false",
    "commit.gpgSign=false",
    "tag.gpgSign=false",
    "log.showSignature=false",
    "merge.verifySignatures=false",
    "submodule.recurse=false",
    "fetch.recurseSubmodules=false",
    "push.recurseSubmodules=no",
    "safe.bareRepository=explicit",
)

# Workspace git never talks to a network: origin round-trips go through the
# relay. Only the local file transport (fetching FROM the relay) stays open.
_TRANSPORT_OVERRIDES = (
    "protocol.allow=never",
    "protocol.file.allow=always",
    "protocol.https.allow=never",
    "protocol.http.allow=never",
    "protocol.ssh.allow=never",
    "protocol.git.allow=never",
    "protocol.ext.allow=never",
)

# Verbs whose output runs external diff programs / textconv filters by default.
_DIFF_VERBS = frozenset({"diff", "log", "show"})

_PERMISSION_DENIED = "Permission denied"


class UnsafeWorkspaceGitConfigError(RuntimeError):
    """A clone's git config carries a setting no override can neutralize."""


def workspace_git_hooks_dir() -> Path:
    """The only hooks directory orchestrator-side git ever runs."""
    return Path(settings.git_support_dir) / "hooks"


def workspace_git_overrides() -> list[str]:
    """``-c`` arguments for every workspace git command."""
    return [
        "-c",
        f"core.hooksPath={workspace_git_hooks_dir()}",
        *_overrides_except_hooks_path(),
    ]


def _overrides_except_hooks_path() -> list[str]:
    pairs = (*_PROGRAM_OVERRIDES, *_TRANSPORT_OVERRIDES)
    return [arg for pair in pairs for arg in ("-c", pair)]


def workspace_git_env() -> dict[str, str]:
    """The complete environment of a workspace git process (no inheritance)."""
    global_config = Path(settings.git_support_dir) / "workspace.gitconfig"
    return {
        "PATH": SAFE_PATH,
        "HOME": "/nonexistent",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        # safe.directory=* lives here: the git user runs in clones the agent
        # uid owns, and git only honors safe.directory from protected config.
        "GIT_CONFIG_GLOBAL": str(global_config)
        if global_config.is_file()
        else "/dev/null",
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "false",
        "GIT_EDITOR": "true",
        "GIT_SEQUENCE_EDITOR": "true",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
        # An agent can plant refs/replace/* to make a diff show content other
        # than what is pushed; reviewers must always see the real objects.
        "GIT_NO_REPLACE_OBJECTS": "1",
    }


@dataclass(frozen=True)
class WorkspaceRepository:
    """Where a workspace's repository is, by layout alone."""

    common_dir: Path  # <clone>/.git
    git_dir: Path  # <clone>/.git, or <clone>/.git/worktrees/<id> for a worktree
    work_tree: Path


def workspace_repository(path: Path) -> WorkspaceRepository:
    """The repository of a clone root or of its per-task worktree at
    ``<clone>/.worktrees/<id>`` (git names the admin dir after the worktree's
    basename). Nothing on disk is read."""
    path = Path(os.path.normpath(path))
    clone_root = clone_root_of(path)
    common = clone_root / ".git"
    git_dir = common if clone_root == path else common / "worktrees" / path.name
    return WorkspaceRepository(common_dir=common, git_dir=git_dir, work_tree=path)


def workspace_repository_env(path: Path) -> dict[str, str]:
    """Environment pinning git to ``path``'s repository (agent-writable paths).

    An operator-configured path outside the workspaces root is left to git's
    own discovery: it may legitimately be a linked worktree with a gitfile.
    Under the root, a path with no ``.git`` is not a repository and git gets
    none: ``GIT_CEILING_DIRECTORIES`` stops discovery from climbing into the
    agent-owned project and team directories above it.
    """
    path = Path(os.path.normpath(path))
    if not _is_agent_writable(path):
        return {}
    repo = workspace_repository(path)
    if not os.path.lexists(repo.common_dir):
        return {"GIT_CEILING_DIRECTORIES": str(path.parent)}
    return {
        "GIT_DIR": str(repo.git_dir),
        "GIT_COMMON_DIR": str(repo.common_dir),
        "GIT_WORK_TREE": str(repo.work_tree),
    }


# --------------------------------------------------------------------------- #
# Running as the git user
# --------------------------------------------------------------------------- #

# Set-user-id and set-group-id to the git user (docker/as-git-user/), mode
# 6750: only root and the agent group may run it.
GIT_USER_HELPER = "/usr/local/libexec/roboco/as-git-user"
# The in-command ``timeout`` sends TERM at the deadline, KILL this much later.
_KILL_GRACE_SECONDS = 10
# A caller waits this much past deadline + grace before it gives up itself.
_WAIT_MARGIN_SECONDS = 5
# GNU timeout exits 124 when TERM ended the command; when KILL was needed it
# also kills itself (it sends KILL to its own process group).
_TIMED_OUT_STATUSES = frozenset({124, -signal.SIGKILL})
# The helper's own refusal (126) or a missing ``timeout`` (127), told apart
# from the command's exit by the helper's stderr prefix.
_HELPER_FAILED_STATUSES = frozenset({126, 127})
_HELPER_PREFIX = "as-git-user: "


class GitUserDropError(RuntimeError):
    """The drop to the git user is required and cannot happen."""


class _Killable(Protocol):
    def kill(self) -> None: ...


def git_user_drop_required() -> bool:
    """Whether workspace processes must run as the git user (the orchestrator
    image) rather than as this process (``off``: dev and CI on the host)."""
    return settings.workspace_git_drop == "required"


def workspace_user_argv(argv: Sequence[str], *, timeout: float) -> list[str]:
    """``argv`` as the git user, under an in-command ``timeout``.

    With the drop required: ``[as-git-user, timeout, -k, grace, secs, *argv]``.
    ``timeout`` runs as the git user, so it ends the command and everything
    it started even when this process may not signal them (a non-root parent
    of a git-user child). A missing helper raises ``GitUserDropError``.
    Off: ``argv`` unchanged, bounded by the caller's own timeout.
    """
    if not git_user_drop_required():
        return list(argv)
    return [
        _git_user_helper(),
        "timeout",
        "-k",
        f"{_KILL_GRACE_SECONDS}s",
        f"{_in_command_seconds(timeout)}s",
        *argv,
    ]


def workspace_user_executable(program: str) -> str:
    """The program ``workspace_user_argv([program, ...])`` starts: the helper
    when the drop is required, else ``program`` itself."""
    return _git_user_helper() if git_user_drop_required() else program


def workspace_user_wait_seconds(timeout: float) -> float:
    """How long to wait for a ``workspace_user_argv(..., timeout=timeout)``
    process: with the drop required, past the in-command deadline and its
    kill grace, so the process is gone before the caller gives up on it."""
    if not git_user_drop_required():
        return timeout
    return _in_command_seconds(timeout) + _KILL_GRACE_SECONDS + _WAIT_MARGIN_SECONDS


def workspace_user_timed_out(
    returncode: int | None, elapsed: float, timeout: float
) -> bool:
    """Whether the in-command ``timeout`` ended a ``workspace_user_argv``
    process (``elapsed`` measured from before it started)."""
    return (
        git_user_drop_required()
        and returncode in _TIMED_OUT_STATUSES
        and elapsed >= _in_command_seconds(timeout)
    )


def kill_workspace_user_process(process: _Killable) -> None:
    """Kill a process that outlived its wait, when this process may. A
    non-root parent may not signal a git-user child (``PermissionError``):
    its in-command ``timeout`` ends it instead."""
    with contextlib.suppress(PermissionError, ProcessLookupError):
        process.kill()


def _in_command_seconds(timeout: float) -> int:
    """Whole seconds for ``timeout``, at least 1 (``timeout 0`` never ends)."""
    return max(1, math.ceil(timeout))


def _git_user_helper() -> str:
    if not os.access(GIT_USER_HELPER, os.X_OK):
        raise GitUserDropError(
            f"{GIT_USER_HELPER} is missing or not executable and "
            "workspace_git_drop is required: refusing to run a workspace "
            "process as this process"
        )
    return GIT_USER_HELPER


def run_as_workspace_user(
    program: str,
    *args: str,
    cwd: Path | None = None,
    timeout: float,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``program args`` as the git user (umask 002, workspace env). No check.

    The program is named apart from its arguments and passed as
    ``executable``: arguments (paths, refs) never decide what runs. Without
    ``cwd`` the command runs from ``/``: the caller's own working directory
    may be one the git user cannot even stat. A timeout raises
    ``TimeoutExpired``; a missing or refusing helper ``GitUserDropError``.
    """
    command = _GitUserCommand(
        program=program,
        args=args,
        cwd=str(cwd) if cwd is not None else "/",
        env=env if env is not None else workspace_git_env(),
        timeout=timeout,
    )
    return _run_as_git_user(
        command, input_data=input_text, stdout=subprocess.PIPE, text=True
    )


@dataclass(frozen=True)
class _GitUserCommand:
    """One process to start as the git user (``workspace_user_argv``)."""

    program: str
    args: Sequence[str]
    cwd: str
    env: dict[str, str]
    timeout: float


def _run_as_git_user(
    command: _GitUserCommand,
    *,
    input_data: str | None,
    stdout: int | IO[bytes],
    text: bool,
) -> subprocess.CompletedProcess[Any]:
    """``subprocess.run`` for ``workspace_user_argv`` processes: waits
    ``workspace_user_wait_seconds``, never blocks on a child it may not kill,
    and reports the in-command timeout as ``TimeoutExpired``."""
    argv = workspace_user_argv(
        [command.program, *command.args], timeout=command.timeout
    )
    started = time.monotonic()
    process = subprocess.Popen(
        argv,
        executable=workspace_user_executable(command.program),
        cwd=command.cwd,
        env=command.env,
        stdin=subprocess.PIPE if input_data is not None else None,
        stdout=stdout,
        stderr=subprocess.PIPE,
        text=text,
        umask=0o002,
    )
    try:
        out, err = process.communicate(
            input_data, timeout=workspace_user_wait_seconds(command.timeout)
        )
    except BaseException:
        _give_up_on(process)
        raise
    if workspace_user_timed_out(
        process.returncode, time.monotonic() - started, command.timeout
    ):
        raise subprocess.TimeoutExpired(argv, command.timeout, output=out, stderr=err)
    _raise_if_the_helper_failed(process.returncode, err)
    return subprocess.CompletedProcess(argv, process.returncode, out, err)


def _give_up_on(process: subprocess.Popen[Any]) -> None:
    """Kill (when permitted) and reap a process that outlived its wait, then
    close this end of its pipes: a descendant that outlives it can hold them
    open. One this process may not kill is left to its in-command
    ``timeout``."""
    kill_workspace_user_process(process)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=_KILL_GRACE_SECONDS)
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            stream.close()


def _raise_if_the_helper_failed(returncode: int, stderr: str | bytes | None) -> None:
    if not git_user_drop_required() or returncode not in _HELPER_FAILED_STATUSES:
        return
    message = (
        stderr.decode(errors="replace") if isinstance(stderr, bytes) else stderr
    ) or ""
    if message.startswith(_HELPER_PREFIX):
        raise GitUserDropError(message.strip()[:300])


_IDLE_CAPABILITIES = "0" * 16
_SELF_TEST_TIMEOUT_SECONDS = 30


def verify_git_user_drop() -> None:
    """Startup self-test of the drop (no-op when it is off).

    Reads ``/proc/self/status`` of a process started through the helper: it
    must be the git user (real, effective, saved and filesystem ids), with no
    supplementary group but the agent group, ``no_new_privs`` and no
    capability. Raises ``GitUserDropError`` otherwise.
    """
    if not git_user_drop_required():
        return
    result = run_as_workspace_user(
        "cat", "/proc/self/status", timeout=_SELF_TEST_TIMEOUT_SECONDS
    )
    if result.returncode != 0:
        raise GitUserDropError(
            f"the git-user self-test exited {result.returncode}: "
            f"{result.stderr.strip()[:200]}"
        )
    problems = _identity_problems(_status_fields(result.stdout))
    if problems:
        raise GitUserDropError(
            "a process started as the git user is not one: " + "; ".join(problems)
        )
    logger.info(
        "workspace processes drop to the git user",
        uid=settings.git_user_uid,
        gid=settings.agent_gid,
    )


def _status_fields(status: str) -> dict[str, str]:
    fields = (line.partition(":") for line in status.splitlines())
    return {name: value.strip() for name, _, value in fields}


def _identity_problems(status: dict[str, str]) -> list[str]:
    uid, gid = str(settings.git_user_uid), str(settings.agent_gid)
    problems = []
    if status.get("Uid", "").split() != [uid] * 4:
        problems.append(f"Uid {status.get('Uid')!r}, expected {uid}")
    if status.get("Gid", "").split() != [gid] * 4:
        problems.append(f"Gid {status.get('Gid')!r}, expected {gid}")
    if not set(status.get("Groups", "").split()) <= {gid}:
        problems.append(f"Groups {status.get('Groups')!r}, expected at most {gid}")
    if status.get("NoNewPrivs") != "1":
        problems.append(f"NoNewPrivs {status.get('NoNewPrivs')!r}, expected 1")
    problems.extend(
        f"{name} {status.get(name)!r}, expected none"
        for name in ("CapInh", "CapPrm", "CapEff", "CapAmb")
        if status.get(name) != _IDLE_CAPABILITIES
    )
    return problems


def workspace_git_argv(args: Sequence[str]) -> list[str]:
    """``git <overrides> <args>``, with diff programs disabled on diff verbs."""
    return ["git", *_workspace_git_arguments(args)]


def _workspace_git_arguments(args: Sequence[str]) -> list[str]:
    """What follows ``git`` in ``workspace_git_argv``."""
    args = list(args)
    if args and args[0] in _DIFF_VERBS:
        args = [args[0], "--no-ext-diff", "--no-textconv", *args[1:]]
    return [*workspace_git_overrides(), *args]


def run_workspace_git(
    cwd: Path, args: Sequence[str], *, timeout: float, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run ``git <args>`` in a workspace as the git user.

    Refuses (``UnsafeWorkspaceGitConfigError``) a clone whose config carries a
    setting no override neutralizes. Before its first command in this process
    a clone gets the group-write repair: directories agents created before
    their images set umask 002 would otherwise make a checkout "succeed" with
    files it could not unlink. A "Permission denied" failure on anything
    created since is repaired once and retried. ``check`` raises
    ``CalledProcessError`` like ``subprocess.run``; a timeout raises
    ``TimeoutExpired``.
    """
    clone_root = clone_root_of(Path(os.path.normpath(cwd)))
    audit_workspace_git_config(cwd)
    _regroup_clone_once(clone_root)
    git_args = _workspace_git_arguments(args)
    env = workspace_git_env() | workspace_repository_env(cwd)
    result = run_as_workspace_user("git", *git_args, cwd=cwd, timeout=timeout, env=env)
    if (
        result.returncode != 0
        and _PERMISSION_DENIED in result.stderr
        and _repair_clone_permissions(clone_root)
    ):
        result = run_as_workspace_user(
            "git", *git_args, cwd=cwd, timeout=timeout, env=env
        )
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, ["git", *git_args], result.stdout, result.stderr
        )
    return result


_FILE_IO_TIMEOUT_SECONDS = 30


def workspace_hook_path(cwd: Path, name: str) -> Path | None:
    """The project's own ``name`` hook that plain git would run in ``cwd``.

    Located by git itself (``rev-parse --git-path hooks/<name>`` honors the
    repo's ``core.hooksPath``, e.g. husky's ``.husky/_``), as the git user,
    with every program-starting setting pinned except the hooks path — the
    one this reads. Nothing runs here: the hook runs in a throwaway
    container (``roboco.services.project_hooks``). None when git would not
    run one (absent, or not an executable file).
    """
    audit_workspace_git_config(cwd)
    env = workspace_git_env() | workspace_repository_env(cwd)
    located = run_as_workspace_user(
        "git",
        *_overrides_except_hooks_path(),
        "rev-parse",
        "--git-path",
        f"hooks/{name}",
        cwd=cwd,
        timeout=_FILE_IO_TIMEOUT_SECONDS,
        env=env,
    )
    relative = located.stdout.strip()
    if located.returncode != 0 or not relative:
        return None
    hook = Path(os.path.normpath(Path(cwd) / relative))
    runnable = run_as_workspace_user(
        "test",
        "-f",
        str(hook),
        "-a",
        "-x",
        str(hook),
        timeout=_FILE_IO_TIMEOUT_SECONDS,
    )
    return hook if runnable.returncode == 0 else None


_ARCHIVE_TIMEOUT_SECONDS = 300


def archive_workspace_ref(repo: Path, ref: str, archive: Path) -> tuple[bool, str]:
    """``git archive --format=tar.gz <ref>`` of a workspace into ``archive``.

    This process opens ``archive`` (so it may be a root-only temp file); git
    runs as the git user and only inherits the open descriptor as stdout.
    Returns ``(ok, stderr)``.
    """
    audit_workspace_git_config(repo)
    with archive.open("wb") as handle:
        command = _GitUserCommand(
            program="git",
            args=_workspace_git_arguments(["archive", "--format=tar.gz", ref]),
            cwd=str(repo),
            env=workspace_git_env() | workspace_repository_env(repo),
            timeout=_ARCHIVE_TIMEOUT_SECONDS,
        )
        result = _run_as_git_user(command, input_data=None, stdout=handle, text=False)
    return result.returncode == 0, result.stderr.decode(errors="replace").strip()[:300]


class WorkspaceFileError(OSError):
    """A file in a workspace could not be read or written as the git user."""


def read_workspace_file(path: Path) -> str | None:
    """A workspace file's text, read as the git user; None when absent.

    Root reading an agent-controlled path follows any planted symlink (e.g.
    to /proc/1/environ); the git user can only read what agents could too.
    """
    result = run_as_workspace_user(
        "cat", "--", str(path), timeout=_FILE_IO_TIMEOUT_SECONDS
    )
    if result.returncode != 0:
        return None
    return result.stdout


def write_workspace_file(path: Path, content: str) -> None:
    """Write ``content`` to a workspace file (parents created) as the git user.

    Like ``run_workspace_git``, the clone holding ``path`` gets its
    group-write repair before the first write in this process, and once
    more on "Permission denied": a clone made before the git user existed
    can still carry another group (``admin`` on the NAS) the git user is not
    in.
    """
    clone_root = _clone_root_containing(path)
    if clone_root is not None:
        _regroup_clone_once(clone_root)
    result = _write_as_workspace_user(path, content)
    if (
        result.returncode != 0
        and _PERMISSION_DENIED in result.stderr
        and clone_root is not None
        and _repair_clone_permissions(clone_root)
    ):
        result = _write_as_workspace_user(path, content)
    if result.returncode != 0:
        raise WorkspaceFileError(f"cannot write {path}: {result.stderr.strip()[:200]}")


def _write_as_workspace_user(
    path: Path, content: str
) -> subprocess.CompletedProcess[str]:
    script = 'mkdir -p -- "$(dirname -- "$1")" && cat > "$1"'
    return run_as_workspace_user(
        "sh",
        "-c",
        script,
        "sh",
        str(path),
        timeout=_FILE_IO_TIMEOUT_SECONDS,
        input_text=content,
    )


_CLONE_DEPTH = 3  # <slug>/<team>/<agent> under the workspaces root


def _clone_root_containing(path: Path) -> Path | None:
    """The clone a path under the workspaces root belongs to, by layout."""
    normalized = Path(os.path.normpath(path))
    if not _is_agent_writable(normalized):
        return None
    parts = normalized.relative_to(settings.workspaces_root).parts
    if len(parts) <= _CLONE_DEPTH:
        return None
    return Path(settings.workspaces_root, *parts[:_CLONE_DEPTH])


# --------------------------------------------------------------------------- #
# Config audit
# --------------------------------------------------------------------------- #

GitConfigClass = Literal["allowed", "neutralized", "dangerous", "unknown"]

# What RoboCo and `git clone` themselves write — read off all 112 clones on
# the NAS, 2026-10-28 — plus platform keys git sets on some filesystems.
_ALLOWED_KEYS = frozenset(
    {
        "core.repositoryformatversion",
        "core.filemode",
        "core.bare",
        "core.logallrefupdates",
        "core.symlinks",
        "core.ignorecase",
        "core.precomposeunicode",
        "remote.origin.url",
        "remote.origin.fetch",
        "remote.origin.tagopt",
        "user.name",
        "user.email",
    }
)
# ``section.<subsection>.field`` keys, by section: `branch.<b>.remote` etc.
_ALLOWED_SUBSECTION_FIELDS = {"branch": frozenset({"remote", "merge"})}

# Present in the file but pinned by workspace_git_overrides() on every run.
_NEUTRALIZED_KEYS = frozenset(
    {
        "core.hookspath",  # husky's `prepare` sets it in real clones
        "core.fsmonitor",
        "core.sshcommand",
        "core.askpass",
        "core.editor",
        "core.pager",
        "core.attributesfile",
        "core.gitproxy",
        "sequence.editor",
        "commit.gpgsign",
        "tag.gpgsign",
        "log.showsignature",
        "merge.verifysignatures",
    }
)
_NEUTRALIZED_SECTIONS = frozenset(
    {"credential", "protocol", "gpg", "submodule", "http", "pager", "safe"}
)

# Start a program or redirect git elsewhere, and no override can pin them
# (driver/section names are arbitrary, or the value is a path/rewrite).
_DANGEROUS_SECTIONS = frozenset({"filter", "url", "include", "includeif", "uploadpack"})
_DANGEROUS_KEYS = frozenset(
    {
        "diff.external",
        "core.alternaterefscommand",
        "core.alternaterefsprefixes",
        "core.worktree",
    }
)
_DANGEROUS_SUBSECTION_FIELDS = {
    "diff": frozenset({"textconv", "command"}),
    "merge": frozenset({"driver"}),
    "remote": frozenset({"uploadpack", "receivepack", "vcs", "proxy"}),
}

_MIN_SUBSECTION_PARTS = 3


def classify_git_config_key(key: str) -> GitConfigClass:
    """Classify one ``git config --list`` key (case-insensitive)."""
    key = key.lower()
    parts = key.split(".")
    section = parts[0]
    # The last field of a ``section.<subsection>.field`` key; None without one.
    subsection_field = parts[-1] if len(parts) >= _MIN_SUBSECTION_PARTS else None
    if (
        key in _DANGEROUS_KEYS
        or section in _DANGEROUS_SECTIONS
        or subsection_field in _DANGEROUS_SUBSECTION_FIELDS.get(section, ())
    ):
        return "dangerous"
    if key in _ALLOWED_KEYS or subsection_field in _ALLOWED_SUBSECTION_FIELDS.get(
        section, ()
    ):
        return "allowed"
    if key in _NEUTRALIZED_KEYS or section in _NEUTRALIZED_SECTIONS:
        return "neutralized"
    return "unknown"


@dataclass(frozen=True)
class _AuditVerdict:
    dangerous: tuple[str, ...]
    neutralized: tuple[str, ...]
    unknown: tuple[str, ...]


# clone root -> (config file signature, verdict). A changed signature re-audits.
_audit_cache: dict[str, tuple[tuple[int, ...], _AuditVerdict]] = {}

_AUDIT_TIMEOUT_SECONDS = 30


def audit_workspace_git_config(path: Path) -> None:
    """Refuse a clone whose git metadata could make git run a program here.

    ``path`` is a clone root or one of its per-task worktrees. Only clones
    under the workspaces root are audited: that is the tree every agent
    container mounts read-write (an operator-configured path elsewhere may
    legitimately be a linked worktree with a ``.git`` file). A ``.git`` (or
    worktree admin dir) that is not a real directory, or object alternates,
    are refused outright. Otherwise the config files git reads for the
    pinned repository (``workspace_repository_env``) are listed — by the git
    user, never read by root — and classified: ``dangerous`` keys refuse the
    command, ``neutralized`` / ``unknown`` ones are logged once per config
    change. A path with no ``.git`` yet (a clone target) has nothing to audit.
    """
    path = Path(os.path.normpath(path))
    if not _is_agent_writable(path):
        return
    repo = workspace_repository(path)
    if not os.path.lexists(repo.common_dir):
        return
    for git_dir in {repo.common_dir, repo.git_dir}:
        # A missing worktree admin dir is not a redirect: git itself then
        # reports "not a git repository" for the pinned GIT_DIR.
        if git_dir.is_symlink() or (os.path.lexists(git_dir) and not git_dir.is_dir()):
            raise UnsafeWorkspaceGitConfigError(
                f"{git_dir} is not a directory: refusing a redirected repository"
            )
    if os.path.lexists(repo.common_dir / "commondir"):
        # Only a worktree's admin dir has one; in a clone's own .git it
        # would move config and objects elsewhere (git is pinned past it,
        # but its presence is tampering).
        raise UnsafeWorkspaceGitConfigError(
            f"{repo.common_dir} has a commondir file: refusing a redirected repository"
        )
    if os.path.lexists(repo.common_dir / "objects" / "info" / "alternates"):
        raise UnsafeWorkspaceGitConfigError(
            f"{path} borrows objects from another repository (alternates)"
        )
    verdict = _cached_verdict(repo)
    if verdict.dangerous:
        logger.warning(
            "workspace git config refused",
            clone=str(path),
            dangerous_keys=list(verdict.dangerous),
        )
        raise UnsafeWorkspaceGitConfigError(
            f"{path} git config sets {', '.join(verdict.dangerous)}: "
            "no RoboCo flow writes these; remove them from .git/config"
        )


def _cached_verdict(repo: WorkspaceRepository) -> _AuditVerdict:
    files = [
        repo.common_dir / "config",
        repo.common_dir / "config.worktree",
        repo.git_dir / "config.worktree",
    ]
    files = list(dict.fromkeys(files))
    signature = tuple(value for path in files for value in _stat_signature(path))
    key = str(repo.git_dir)
    cached = _audit_cache.get(key)
    if cached is not None and cached[0] == signature:
        return cached[1]
    keys = [k for path in files if os.path.lexists(path) for k in _config_keys(path)]
    verdict = _AuditVerdict(
        dangerous=_keys_of(keys, "dangerous"),
        neutralized=_keys_of(keys, "neutralized"),
        unknown=_keys_of(keys, "unknown"),
    )
    if verdict.neutralized or verdict.unknown:
        logger.warning(
            "workspace git config carries settings RoboCo does not write",
            git_dir=str(repo.git_dir),
            neutralized_keys=list(verdict.neutralized),
            unknown_keys=list(verdict.unknown),
        )
    _audit_cache[key] = (signature, verdict)
    return verdict


def _stat_signature(path: Path) -> tuple[int, ...]:
    try:
        st = path.lstat()
    except OSError:
        return (0, 0, 0, 0)
    return (st.st_ino, st.st_mtime_ns, st.st_size, st.st_mode)


def _config_keys(path: Path) -> list[str]:
    """Keys of one config file, listed by the git user (no includes followed)."""
    result = run_as_workspace_user(
        "git",
        "config",
        "--file",
        str(path),
        "--no-includes",
        "--list",
        "--null",
        timeout=_AUDIT_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise UnsafeWorkspaceGitConfigError(
            f"cannot list {path}: {result.stderr.strip()[:200]}"
        )
    return [entry.split("\n", 1)[0] for entry in result.stdout.split("\0") if entry]


def _keys_of(keys: list[str], wanted: GitConfigClass) -> tuple[str, ...]:
    return tuple(dict.fromkeys(k for k in keys if classify_git_config_key(k) == wanted))


# --------------------------------------------------------------------------- #
# Permission repair (drop required, fd-safe)
# --------------------------------------------------------------------------- #

_OPEN_NO_FOLLOW = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_OPEN_DIRECTORY_NO_FOLLOW = _OPEN_NO_FOLLOW | os.O_DIRECTORY
_REPAIR_SKIPPED_TREES = frozenset(
    {
        "node_modules",
        ".venv",
        "venv",
        ".uv-python",
        ".next",
        ".turbo",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)


_regrouped_clones: set[str] = set()
_regrouped_clones_guard = threading.Lock()


def _regroup_clone_once(clone_root: Path) -> None:
    """Run the group-write repair on a clone the first time this process
    touches it (drop required only, workspaces root only, no ``.git`` yet =
    no-op)."""
    key = str(clone_root)
    with _regrouped_clones_guard:
        if key in _regrouped_clones:
            return
        _regrouped_clones.add(key)
    if os.path.lexists(clone_root / ".git"):
        _repair_clone_permissions(clone_root)


def _is_agent_writable(path: Path) -> bool:
    """True for paths under the workspaces root (mounted into every agent)."""
    return Path(os.path.normpath(path)).is_relative_to(settings.workspaces_root)


def _repair_clone_permissions(clone_root: Path) -> bool:
    """Give the agent group rw on a clone under the workspaces root, once.

    Returns whether a repair ran (so the caller retries). Only with the drop
    required (the orchestrator image), and only inside the workspaces root:
    an operator-configured path elsewhere is never touched.
    """
    if not git_user_drop_required() or not _is_agent_writable(clone_root):
        return False
    make_clone_group_writable(clone_root)
    return True


def prepare_clone_parent(clone_root: Path) -> None:
    """Create the directories above a new clone, writable by the git user.

    ``<workspaces_root>/<slug>/<team>/`` is agent-writable like the clones in
    it, and on a long-lived install a host user made it (mode 755, a group
    the git user is not in). So root creates and regroups it one component
    at a time by descriptor: ``mkdir`` relative to the parent's descriptor,
    then an ``O_NOFOLLOW`` open — a component an agent swapped for a symlink
    is refused (``OSError``), never followed. Each directory gets group
    ``agent_gid`` with rwx, which the git user needs to create the clone;
    agents already own these directories. With the drop off (dev and CI on
    the host): a plain ``mkdir -p``.
    """
    parent = Path(os.path.normpath(clone_root)).parent
    if not git_user_drop_required():
        parent.mkdir(parents=True, exist_ok=True)
        return
    if not _is_agent_writable(parent):
        raise WorkspaceFileError(f"{parent} is outside the workspaces root")
    fd = _open_workspace_dir(parent, create=True)
    os.close(fd)


def remove_workspace_tree(path: Path, *, ignore_errors: bool = False) -> None:
    """``rm -rf`` a directory under the workspaces root without following a
    symlink anywhere on its path.

    ``shutil.rmtree`` never follows a link inside the tree, but it resolves
    the path's ancestors like any open: an agent that swaps its project or
    team directory for a symlink would point root's delete at a same-named
    directory anywhere. With the drop required (the orchestrator image), the
    ancestors are opened by descriptor with ``O_NOFOLLOW`` (a symlink is
    refused, ``OSError``) and the tree is removed relative to its parent's
    descriptor; the tree itself being a symlink is refused by ``rmtree``.
    With the drop off (dev and CI on the host), or outside the workspaces
    root (the private root, which no agent mounts): a plain
    ``shutil.rmtree``.
    """
    normalized = Path(os.path.normpath(path))
    if not git_user_drop_required() or not _is_agent_writable(normalized.parent):
        shutil.rmtree(normalized, ignore_errors=ignore_errors)
        return
    try:
        fd = _open_workspace_dir(normalized.parent, create=False)
    except OSError:
        if ignore_errors:
            return
        raise
    try:
        shutil.rmtree(normalized.name, ignore_errors=ignore_errors, dir_fd=fd)
    finally:
        os.close(fd)


def workspace_dir_identity(directory: Path) -> tuple[int, int]:
    """``(st_dev, st_ino)`` of a directory, reached without following a link.

    Under the workspaces root every component is opened by descriptor with
    ``O_NOFOLLOW`` (a symlink raises ``OSError``); a dispatcher-owned path
    elsewhere is trusted and stat'ed directly.
    """
    normalized = Path(os.path.normpath(directory))
    if not _is_agent_writable(normalized):
        st = normalized.stat()
        return st.st_dev, st.st_ino
    fd = _open_workspace_dir(normalized, create=False)
    try:
        st = os.fstat(fd)
    finally:
        os.close(fd)
    return st.st_dev, st.st_ino


def _open_workspace_dir(directory: Path, *, create: bool) -> int:
    """A descriptor of ``directory`` (under the workspaces root), reached one
    component at a time with ``O_NOFOLLOW``. ``create`` makes missing
    components and gives every component group ``agent_gid`` with rwx."""
    relative = directory.relative_to(settings.workspaces_root)
    fd = os.open(settings.workspaces_root, _OPEN_DIRECTORY_NO_FOLLOW)
    try:
        for name in relative.parts:
            if create:
                with contextlib.suppress(FileExistsError):
                    os.mkdir(name, 0o775, dir_fd=fd)
            child = os.open(name, _OPEN_DIRECTORY_NO_FOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            if create:
                _regroup(fd, settings.agent_gid)
    except BaseException:
        os.close(fd)
        raise
    return fd


def make_clone_group_writable(clone_root: Path) -> None:
    """Ensure group ``agent_gid`` with rw (+x on dirs) on every entry of a clone.

    The tree is agent-controlled, so the walk never follows a symlink, not
    even through a race: directories are walked by descriptor (``fwalk``),
    every entry is opened with ``O_NOFOLLOW`` relative to its parent's
    descriptor, and mode/group are changed on that descriptor. Symlinks,
    sockets and anything else that is not a regular file or directory are
    skipped.
    """
    gid = settings.agent_gid
    with contextlib.suppress(OSError):
        root_fd = os.open(clone_root, _OPEN_DIRECTORY_NO_FOLLOW)
        try:
            _regroup(root_fd, gid)
        finally:
            os.close(root_fd)
    for _parent, dirs, files, dir_fd in os.fwalk(clone_root, follow_symlinks=False):
        for name in (*dirs, *files):
            _regroup_entry(name, dir_fd, gid)
        # Fix the entry, never descend: git does not touch these ignored,
        # tool-regenerated trees, and node_modules alone is minutes on a NAS.
        dirs[:] = [d for d in dirs if d not in _REPAIR_SKIPPED_TREES]


def _regroup_entry(name: str, dir_fd: int, gid: int) -> None:
    try:
        fd = os.open(name, _OPEN_NO_FOLLOW, dir_fd=dir_fd)
    except OSError:
        return  # a symlink (ELOOP), a socket, or gone: nothing to repair
    try:
        _regroup(fd, gid)
    finally:
        os.close(fd)


def _regroup(fd: int, gid: int) -> None:
    st = os.fstat(fd)
    is_dir = stat.S_ISDIR(st.st_mode)
    if not (is_dir or stat.S_ISREG(st.st_mode)):
        return
    wanted = st.st_mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP
    if is_dir:
        wanted |= stat.S_IXUSR | stat.S_IXGRP
    with contextlib.suppress(OSError):
        if st.st_gid != gid:
            os.fchown(fd, -1, gid)
        if stat.S_IMODE(wanted) != stat.S_IMODE(st.st_mode):
            os.fchmod(fd, stat.S_IMODE(wanted))
