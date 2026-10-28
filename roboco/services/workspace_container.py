"""Run a workspace's own code in a throwaway container, never as root here.

A clone's Makefile, test suite, build backend and ``package.json`` scripts are
written by agents, and every agent container mounts every clone read-write.
Run here they ran as root in the container that holds the Docker socket, i.e.
as root on the host. They now run in a container started from this
orchestrator's own image (the same toolchain they always used), as the agent
uid, with no capabilities, no Docker socket, only the one clone mounted, and on
``workspace_container_network`` (plain ``bridge`` by default: package
registries yes, RoboCo's networks no).

A container that cannot be started raises ``WorkspaceContainerError``; callers
skip the step (logged), they never fall back to running the code here.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from roboco.config import settings
from roboco.utils.docker_env import docker_cli_env, docker_options_without_env_values
from roboco.utils.workspace_paths import clone_root_of

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


WORKSPACE_CONTAINER_LABEL = "roboco.workspace-container=1"

# `docker run` exits 125 when the daemon could not create/start the container
# (bad image, unreachable daemon, invalid option) — not the command's status.
_DOCKER_RUN_FAILED = 125
_TIMED_OUT = 124
_KILL_TIMEOUT_SECONDS = 30.0

# Grace between the in-container timeout's TERM and its KILL.
_KILL_GRACE_SECONDS = 10


def _entry_script(timeout: float, *, merge_stderr: bool) -> str:
    """The container's ``sh -c`` script; the command follows as ``$@``.

    umask 002 keeps every file the command writes group-writable for the
    agent group. VIRTUAL_ENV / UV_PROJECT_ENVIRONMENT are this image's own
    /app/.venv pins: unset, uv resolves the clone's .venv instead. The
    in-container ``timeout`` bounds the run even when no orchestrator is left
    to kill it (a restart or a blue/green switch mid-gate), so an orphaned
    container always ends on its own and ``--rm`` removes it.
    """
    script = (
        'umask 002 && mkdir -p "$HOME" && '
        f"exec timeout -k {_KILL_GRACE_SECONDS} {max(1, int(timeout))} "
        'env -u VIRTUAL_ENV -u UV_PROJECT_ENVIRONMENT "$@"'
    )
    return script + (" 2>&1" if merge_stderr else "")


# Process-wide cache of the resolved own-image id (immutable for the process).
_own_image: dict[str, str] = {}


class WorkspaceContainerError(RuntimeError):
    """The throwaway workspace container could not be started."""


@dataclass(frozen=True)
class WorkspaceContainerResult:
    """Outcome of one command run in a throwaway workspace container."""

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


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
    argv: Sequence[str],
    *,
    timeout: float,
    env: Mapping[str, str] | None = None,
    merge_stderr: bool = False,
) -> WorkspaceContainerResult:
    """Run ``argv`` in ``cwd`` inside a throwaway container as the agent uid.

    The clone root of ``cwd`` (so a worktree still reaches the shared
    ``.venv`` / ``.uv-python``) is bind-mounted at the same path, so absolute
    paths inside the clone mean the same thing in there as here and in the
    agent containers. ``env`` must carry no secret: it is the
    only environment the command gets beyond the image's own. A timeout kills
    the container and reports ``timed_out`` with returncode 124.
    """
    name = f"roboco-wsx-{uuid4().hex[:12]}"
    options = _container_options(name, clone_root_of(cwd), cwd, env or {})
    script = _entry_script(timeout, merge_stderr=merge_stderr)
    options, env_values = docker_options_without_env_values(options)
    image = await workspace_container_image()
    proc = await asyncio.create_subprocess_exec(
        *options,
        image,
        "-c",
        script,
        "sh",
        *argv,
        env=docker_cli_env(env_values),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        await _stop_workspace_container(name, proc)
        return WorkspaceContainerResult(
            _TIMED_OUT, "", f"timed out after {timeout}s", timed_out=True
        )
    except asyncio.CancelledError:
        await _stop_workspace_container(name, proc)
        raise
    out = stdout.decode(errors="replace")
    err = stderr.decode(errors="replace")
    if proc.returncode == _DOCKER_RUN_FAILED:
        raise WorkspaceContainerError(f"docker run failed: {err.strip()[:500]}")
    # No recorded exit code (the CLI was ended out-of-band) is a failure: an
    # unknown status must never pass a gate.
    returncode = proc.returncode if proc.returncode is not None else 1
    return WorkspaceContainerResult(returncode, out, err)


def _container_options(
    name: str, mount: Path, cwd: Path, env: Mapping[str, str]
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
        "--user",
        f"{settings.agent_uid}:{settings.agent_gid}",
        "--network",
        settings.workspace_container_network,
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
        "--entrypoint",
        "/bin/sh",
    ]
    for key, value in env.items():
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
