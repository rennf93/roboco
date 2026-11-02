"""Run a workspace's own code in a throwaway container, never as root here.

A clone's Makefile, test suite, build backend and ``package.json`` scripts are
written by agents, and every agent container mounts every clone read-write.
Run here they ran as root in the container that holds the Docker socket, i.e.
as root on the host. They now run in a container started from this
orchestrator's own image (the same toolchain they always used), as the agent
uid, with no capabilities, no Docker socket, only the one clone mounted, and on
``workspace_container_network`` (plain ``bridge`` by default: package
registries yes, RoboCo's networks no).

"Only the one clone" is checked, not assumed: the Docker daemon resolves the
bind source on the host, following any symlink an agent planted in place of
the clone or a directory above it. The clone's device and inode are taken
here through an ``O_NOFOLLOW`` descriptor walk, and the container compares
them with its own mount before running anything; a mismatch exits 125, which
callers already treat as "could not start".

A caller may add one more directory, read-only (``ReadOnlyMount``: the
Entire step's agent transcript folder), checked the same way.

A container that does not run the command raises ``WorkspaceContainerError``
with the reason (the docker CLI's own error, the entry script's, the exit
status); callers skip the step (logged), they never fall back to running the
code here. The entry script prints ``STARTED_MARKER`` on stderr right before
it runs the command, so a failed run tells "the command failed" (it started:
its own exit status and output) apart from "the container could not run it"
(the docker CLI's 125/126/127/1, a mount check or ``HOME`` that failed, a
kill before the start).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from roboco.config import settings
from roboco.services.workspace_git import workspace_dir_identity
from roboco.utils.docker_env import docker_cli_env, docker_options_without_env_values
from roboco.utils.workspace_paths import clone_root_of

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


WORKSPACE_CONTAINER_LABEL = "roboco.workspace-container=1"

# Container names read `roboco-wsx-<owner>-<purpose>-<8 hex>`; each segment is
# cut to what docker names allow and to a length `docker ps` stays legible at.
_NAME_SEGMENT_DISALLOWED = re.compile(r"[^a-z0-9_.-]+")
_NAME_SEGMENT_MAX_CHARS = 40
# /data/workspaces/<project>/<team>/<agent>: the agent slug is the third part.
_AGENT_PATH_DEPTH = 3

# `docker run` exits 125 when the daemon could not create/start the container
# (bad image, unreachable daemon, invalid option); the entry script's own
# checks use it too. A command that started may exit 125 itself.
_DOCKER_RUN_FAILED = 125
_TIMED_OUT = 124
_KILL_TIMEOUT_SECONDS = 30.0
# How much of the container's stderr a WorkspaceContainerError carries.
_ERROR_CHARS = 2000

# Printed on stderr by the entry script right before it runs the command
# (shell-safe as a bare word); removed from what callers get back.
STARTED_MARKER = "roboco-wsx:command-started"

# Grace between the in-container timeout's TERM and its KILL.
_KILL_GRACE_SECONDS = 10

_OPEN_DIRECTORY_NO_FOLLOW = (
    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
)


# Best effort, never fails: a task worktree's .venv is a relative symlink to the
# clone's shared venv, and uv writes the path it was invoked through into every
# console script's shebang. Run from a worktree, that bakes a path which breaks
# once the worktree is reaped, so those shebangs are pointed back at the
# canonical venv. Only the first line of a script that needs it is rewritten.
_CANONICAL_VENV_SCRIPT = (
    "v=; if [ -L .venv ]; then v=$(readlink -f .venv 2>/dev/null); "
    'case "$v" in "$ROBOCO_WORKSPACE_MOUNT"/*) ;; *) v= ;; esac; fi; '
    'if [ -n "$v" ]; then for f in "$v"/bin/*; do '
    '[ -f "$f" ] && [ ! -L "$f" ] || continue; '
    'IFS= read -r l < "$f" 2>/dev/null || continue; '
    'case "$l" in "#!"*/.worktrees/*/.venv/bin/*) ;; *) continue ;; esac; '
    "t=$(mktemp 2>/dev/null) || continue; "
    '{ printf \'%s\\n\' "#!$v/bin/${l##*/}"; tail -n +2 -- "$f"; } > "$t" '
    '&& cat "$t" > "$f"; rm -f "$t"; done; fi'
)


def _entry_script(timeout: float, *, merge_stderr: bool) -> str:
    """The container's ``sh -c`` script; the command follows as ``$@``.

    It first checks that the mount is the directory identified here
    (``ROBOCO_WORKSPACE_MOUNT_ID``), and so is the read-only extra mount when
    there is one, else exits 125 without running anything. ``STARTED_MARKER``
    goes to stderr once everything before the command succeeded.
    umask 002 keeps every file the command writes group-writable for the
    agent group. VIRTUAL_ENV / UV_PROJECT_ENVIRONMENT are this image's own
    /app/.venv pins: unset, uv resolves the clone's .venv instead.
    ROBOCO_WORKSPACE_GIT_DROP is the orchestrator's own setting: unset, a
    project's code (RoboCo's own test suite, when it is the project) runs
    with the setting's default, and the git-user helper refuses here anyway
    (no-new-privileges). The in-container ``timeout`` bounds the run even
    when no orchestrator is left to kill it (a restart or a blue/green
    switch mid-gate), so an orphaned container always ends on its own and
    ``--rm`` removes it.

    When the clone's ``.venv`` is a symlink to a venv under the mount (a task
    worktree), the shebangs that name a worktree's ``.venv`` are first
    rewritten to the canonical venv path, and the command runs with
    ``UV_PROJECT_ENVIRONMENT`` set to it so uv writes no worktree path again.
    A failure of that repair never stops the command.
    """
    script = (
        '[ "$(stat -c %d:%i -- "$ROBOCO_WORKSPACE_MOUNT")" = '
        '"$ROBOCO_WORKSPACE_MOUNT_ID" ] || { echo "the mounted workspace is not '
        f'the expected directory" >&2; exit {_DOCKER_RUN_FAILED}; }}; '
        '[ -z "$ROBOCO_EXTRA_MOUNT" ] || [ "$(stat -c %d:%i -- '
        '"$ROBOCO_EXTRA_MOUNT")" = "$ROBOCO_EXTRA_MOUNT_ID" ] || { echo "the '
        'read-only mount is not the expected directory" >&2; '
        f"exit {_DOCKER_RUN_FAILED}; }}; "
        'umask 002 && mkdir -p "$HOME" && '
        f"{{ {_CANONICAL_VENV_SCRIPT}; :; }} && "
        f"echo {STARTED_MARKER} >&2 && "
        f"exec timeout -k {_KILL_GRACE_SECONDS} {max(1, int(timeout))} "
        "env -u VIRTUAL_ENV -u UV_PROJECT_ENVIRONMENT -u ROBOCO_WORKSPACE_GIT_DROP "
        '${v:+"UV_PROJECT_ENVIRONMENT=$v"} "$@"'
    )
    return script + (" 2>&1" if merge_stderr else "")


# Process-wide cache of the resolved own-image id (immutable for the process).
_own_image: dict[str, str] = {}


class WorkspaceContainerError(RuntimeError):
    """The throwaway workspace container did not run the command."""


@dataclass(frozen=True)
class ReadOnlyMount:
    """One more directory for the container, read-only at ``target``.

    ``local`` is that directory as seen here, below the trusted ``root``
    (every component under it is agent-writable, so it is reached by an
    ``O_NOFOLLOW`` walk); ``host`` is what the Docker daemon binds.
    """

    root: Path
    local: Path
    host: str
    target: str


@dataclass(frozen=True)
class WorkspaceCommand:
    """What a throwaway container runs: ``argv``, and the ``purpose`` its name
    carries (``codegen``, ``dev-deps``, ``hook-pre-commit``, ...)."""

    argv: Sequence[str]
    purpose: str


@dataclass(frozen=True)
class _Run:
    """How one throwaway container runs (all but its cwd and command)."""

    timeout: float
    env: Mapping[str, str]
    merge_stderr: bool
    network: str
    read_only_mount: ReadOnlyMount | None = None


@dataclass(frozen=True)
class WorkspaceContainerResult:
    """Outcome of one command run in a throwaway workspace container.

    ``stderr`` is the command's own (unless merged into ``stdout``) plus
    whatever the docker CLI printed; on a timeout it ends with the deadline.
    """

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def output(self) -> str:
        """Everything the run printed: stdout, then stderr."""
        parts = (self.stdout.rstrip("\n"), self.stderr.strip())
        return "\n".join(part for part in parts if part)


def host_path_for(path: Path) -> str:
    """The host path the Docker daemon must bind for ``path`` (as seen here).

    Only the two data roots the compose files mount from the host data dir
    are translatable; anything else would be a path inside this container,
    which the daemon cannot see.
    """
    host_root = settings.host_data_dir.rstrip("/")
    normalized = Path(os.path.normpath(path))
    if not host_root:
        return str(normalized)
    for container_root, host_subdir in (
        (settings.workspaces_root, "workspaces"),
        (settings.private_data_root, "private"),
    ):
        with contextlib.suppress(ValueError):
            relative = normalized.relative_to(container_root)
            return str(Path(host_root, host_subdir, relative))
    raise WorkspaceContainerError(f"{path} is not under a host-mounted data dir")


async def run_in_workspace_container(
    cwd: Path,
    command: WorkspaceCommand,
    *,
    timeout: float,
    env: Mapping[str, str] | None = None,
    merge_stderr: bool = False,
) -> WorkspaceContainerResult:
    """Run ``command`` in ``cwd`` inside a throwaway container as the agent uid.

    The container is named after the workspace's owner and the command's
    purpose (``workspace_container_name``).

    The clone root of ``cwd`` (so a worktree still reaches the shared
    ``.venv`` / ``.uv-python``) is bind-mounted at the same path, so absolute
    paths inside the clone mean the same thing in there as here and in the
    agent containers. ``env`` must carry no secret: it is the
    only environment the command gets beyond the image's own. A timeout kills
    the container and reports ``timed_out`` with returncode 124.
    """
    run = _Run(timeout, env or {}, merge_stderr, settings.workspace_container_network)
    return await _run_container(cwd, command, run)


async def run_offline_in_workspace_container(
    cwd: Path,
    command: WorkspaceCommand,
    *,
    timeout: float,
    env: Mapping[str, str] | None = None,
    read_only_mount: ReadOnlyMount | None = None,
) -> WorkspaceContainerResult:
    """``run_in_workspace_container`` with no network at all (stdout and
    stderr merged), plus ``read_only_mount`` when given."""
    run = _Run(timeout, env or {}, True, "none", read_only_mount)
    return await _run_container(cwd, command, run)


async def _run_container(
    cwd: Path, command: WorkspaceCommand, run: _Run
) -> WorkspaceContainerResult:
    name = workspace_container_name(cwd, command.purpose)
    proc = await _start_container(name, cwd, command, run)
    try:
        stdout, stderr, timed_out = await _collect_output(proc, name, run.timeout)
    except asyncio.CancelledError:
        await _stop_workspace_container(name, proc)
        raise
    return _result_of_run(proc.returncode, stdout, stderr, timed_out, run.timeout)


async def _start_container(
    name: str, cwd: Path, command: WorkspaceCommand, run: _Run
) -> asyncio.subprocess.Process:
    """``docker run`` the entry script and ``command`` (the CLI process)."""
    mount = clone_root_of(Path(os.path.normpath(cwd)))
    try:
        identity = await asyncio.to_thread(workspace_dir_identity, mount)
    except OSError as exc:
        raise WorkspaceContainerError(f"{mount} cannot be mounted: {exc}") from exc
    options = _container_options(name, mount, cwd, identity, run)
    if run.read_only_mount is not None:
        options.extend(await _read_only_mount_options(run.read_only_mount))
    script = _entry_script(run.timeout, merge_stderr=run.merge_stderr)
    options, env_values = docker_options_without_env_values(options)
    image = await workspace_container_image()
    return await asyncio.create_subprocess_exec(
        *options,
        image,
        "-c",
        script,
        "sh",
        *command.argv,
        env=docker_cli_env(env_values),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def _collect_output(
    proc: asyncio.subprocess.Process, name: str, timeout: float
) -> tuple[bytes, bytes, bool]:
    """``(stdout, stderr, timed_out)`` of the CLI. At the deadline the
    container is killed and what it printed until then is kept."""
    reading = asyncio.ensure_future(proc.communicate())
    try:
        stdout, stderr = await asyncio.wait_for(asyncio.shield(reading), timeout)
    except TimeoutError:
        await _stop_workspace_container(name, proc)
        stdout, stderr = await _output_after_stop(reading)
        return stdout, stderr, True
    except asyncio.CancelledError:
        reading.cancel()
        raise
    return stdout, stderr, False


async def _output_after_stop(
    reading: asyncio.Future[tuple[bytes, bytes]],
) -> tuple[bytes, bytes]:
    """What the killed run printed: its pipes close once the CLI is reaped."""
    try:
        return await asyncio.wait_for(reading, _KILL_TIMEOUT_SECONDS)
    except TimeoutError:
        return b"", b""


def _result_of_run(
    returncode: int | None,
    stdout: bytes,
    stderr: bytes,
    timed_out: bool,
    timeout: float,
) -> WorkspaceContainerResult:
    """The command's result, or ``WorkspaceContainerError`` when the run
    failed before the command started (no ``STARTED_MARKER``)."""
    out = stdout.decode(errors="replace")
    started, err = _split_started_marker(stderr.decode(errors="replace"))
    failed = timed_out or returncode != 0
    if failed and not started:
        raise WorkspaceContainerError(
            _not_run_reason(returncode, err, timed_out, timeout)
        )
    if timed_out:
        note = f"timed out after {timeout}s"
        return WorkspaceContainerResult(
            _TIMED_OUT, out, f"{err}{note}\n", timed_out=True
        )
    # No recorded exit code (the CLI was ended out-of-band) is a failure: an
    # unknown status must never pass a gate.
    return WorkspaceContainerResult(
        returncode if returncode is not None else 1, out, err
    )


def _split_started_marker(stderr: str) -> tuple[bool, str]:
    """Whether the entry script reached the command, and stderr without its
    marker line."""
    lines = stderr.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.rstrip("\n") == STARTED_MARKER:
            return True, "".join(lines[:index] + lines[index + 1 :])
    return False, stderr


def _not_run_reason(
    returncode: int | None, stderr: str, timed_out: bool, timeout: float
) -> str:
    """Why the container did not run the command, for a log line or a gate."""
    said = stderr.strip()[-_ERROR_CHARS:] or "no output"
    if timed_out:
        return f"the container did not start the command within {timeout}s: {said}"
    return (
        f"the container did not run the command (docker run exit {returncode}): {said}"
    )


def workspace_container_name(cwd: Path, purpose: str) -> str:
    """``roboco-wsx-<owner>-<purpose>-<8 hex>``: whose workspace and what runs
    in it, readable in ``docker ps``; the random suffix keeps concurrent runs
    of the same purpose apart."""
    owner = _workspace_owner(Path(os.path.normpath(cwd)))
    segments = (_name_segment(owner), _name_segment(purpose), uuid4().hex[:8])
    return "roboco-wsx-" + "-".join(segments)


def _workspace_owner(path: Path) -> str:
    """The agent slug of ``/data/workspaces/<project>/<team>/<agent>/...``, the
    last segment of a shallower workspace path, ``private`` for the
    orchestrator-only data root, else ``workspace``."""
    with contextlib.suppress(ValueError):
        parts = path.relative_to(settings.workspaces_root).parts
        if len(parts) >= _AGENT_PATH_DEPTH:
            return parts[_AGENT_PATH_DEPTH - 1]
        if parts:
            return parts[-1]
    with contextlib.suppress(ValueError):
        path.relative_to(settings.private_data_root)
        return "private"
    return "workspace"


def _name_segment(text: str) -> str:
    segment = _NAME_SEGMENT_DISALLOWED.sub("-", text.lower()).strip("-.")
    return segment[:_NAME_SEGMENT_MAX_CHARS].rstrip("-.") or "x"


async def _read_only_mount_options(mount: ReadOnlyMount) -> list[str]:
    try:
        dev, ino = await asyncio.to_thread(_dir_identity_below, mount.root, mount.local)
    except (OSError, ValueError) as exc:
        raise WorkspaceContainerError(
            f"{mount.local} cannot be mounted: {exc}"
        ) from exc
    return [
        "-v",
        f"{mount.host}:{mount.target}:ro",
        "-e",
        f"ROBOCO_EXTRA_MOUNT={mount.target}",
        "-e",
        f"ROBOCO_EXTRA_MOUNT_ID={dev}:{ino}",
    ]


def _dir_identity_below(root: Path, directory: Path) -> tuple[int, int]:
    """``(st_dev, st_ino)`` of ``directory``, opened one component at a time
    below ``root`` with ``O_NOFOLLOW`` (a symlink raises ``OSError``)."""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for name in directory.relative_to(root).parts:
            child = os.open(name, _OPEN_DIRECTORY_NO_FOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        st = os.fstat(fd)
    finally:
        os.close(fd)
    return st.st_dev, st.st_ino


def _container_options(
    name: str,
    mount: Path,
    cwd: Path,
    identity: tuple[int, int],
    run: _Run,
) -> list[str]:
    """The ``docker run`` options (everything before the image)."""
    options = [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--label",
        WORKSPACE_CONTAINER_LABEL,
        # The image's HEALTHCHECK probes the orchestrator's own /health; a
        # throwaway run serves nothing, and each probe would be one more
        # process against --pids-limit and --memory below.
        "--no-healthcheck",
        "--user",
        f"{settings.agent_uid}:{settings.agent_gid}",
        "--network",
        run.network,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        str(settings.workspace_container_pids_limit),
        "--memory",
        settings.workspace_container_memory,
        "--cpus",
        settings.workspace_container_cpus,
        "-v",
        f"{host_path_for(mount)}:{os.path.normpath(mount)}",
        "-w",
        os.path.normpath(cwd),
        "-e",
        "HOME=/tmp/home",
        "-e",
        "UV_CACHE_DIR=/tmp/uv-cache",
        "-e",
        f"ROBOCO_WORKSPACE_MOUNT={os.path.normpath(mount)}",
        "-e",
        f"ROBOCO_WORKSPACE_MOUNT_ID={identity[0]}:{identity[1]}",
        "--entrypoint",
        "/bin/sh",
    ]
    for key, value in run.env.items():
        options.extend(["-e", f"{key}={value}"])
    return options


async def workspace_container_image() -> str:
    """The configured image, else the image this orchestrator itself runs.

    The own image is read once from this container (its hostname is the
    container id) by image id, so a later re-tag cannot swap the toolchain
    under a running orchestrator.
    """
    if settings.workspace_container_image:
        return settings.workspace_container_image
    if "id" in _own_image:
        return _own_image["id"]
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "container",
        "inspect",
        "--format",
        "{{.Image}}",
        socket.gethostname(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    image = stdout.decode().strip()
    if proc.returncode != 0 or not image:
        raise WorkspaceContainerError(
            "cannot resolve the orchestrator's own image "
            f"({stderr.decode(errors='replace').strip()[:200]}); "
            "set ROBOCO_WORKSPACE_CONTAINER_IMAGE"
        )
    _own_image["id"] = image
    return image


async def _stop_workspace_container(
    name: str, proc: asyncio.subprocess.Process
) -> None:
    """Kill a still-running container (``--rm`` removes it) and reap the CLI."""
    killer = await asyncio.create_subprocess_exec(
        "docker",
        "kill",
        name,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(killer.wait(), timeout=_KILL_TIMEOUT_SECONDS)
    with contextlib.suppress(ProcessLookupError):
        proc.kill()
    await proc.wait()
