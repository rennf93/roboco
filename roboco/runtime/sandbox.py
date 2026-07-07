"""Per-agent-spawn sandbox provisioner: throwaway engine containers.

Orchestrator-side sibling containers to the agent container — never
docker-in-agent (the socket/CLI stay structurally absent from agent images).
Lifetime tracks the agent container 1:1: provisioned before `docker run`,
torn down whenever the agent container is stopped/removed. Standalone and
unit-testable: docker plumbing is a thin injected callable, not a dependency
on the orchestrator module.

Engine specs (image, run args, readiness probe, env emission) live in the
pure registry ``roboco/models/sandbox.py``; this module only runs docker
against them. Adding an engine is one entry there — no branch edited here.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from roboco.models.sandbox import (
    SANDBOX_ENGINES,
    VALID_SANDBOX_SERVICES,
    SandboxConnection,
    SandboxEngine,
    SandboxInfo,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    # (args, timeout) -> (returncode, stdout, stderr)
    DockerRunner = Callable[[list[str], float], Awaitable[tuple[int, bytes, bytes]]]

# Docker subprocess deadlines. A single asyncio event loop is shared with
# dispatch, so every docker call here must be timeout-bounded, mirroring the
# orchestrator's own `_DOCKER_INSPECT_TIMEOUT_SECONDS` / `_DOCKER_EXEC_TIMEOUT_SECONDS`.
_DOCKER_RUN_TIMEOUT_SECONDS = 20.0
_DOCKER_EXEC_TIMEOUT_SECONDS = 10.0
_DOCKER_TEARDOWN_TIMEOUT_SECONDS = 15.0
_DOCKER_PS_TIMEOUT_SECONDS = 10.0
# `docker run` pulls inline when the image is absent, under the run deadline
# above; a NAS cold pull runs minutes, so the run is killed, the pull is
# cancelled, and every retry re-pulls from scratch — a persistent loop. Pulling
# explicitly with a generous deadline breaks it at the source.
_DOCKER_PULL_TIMEOUT_SECONDS = 300.0

# Readiness poll interval. Per-engine deadlines live on the engine
# (`SandboxEngine.ready_deadline`); only the poll cadence is shared here.
_READY_POLL_INTERVAL_SECONDS = 1.0

# Janitor grace: a sandbox is provisioned BEFORE its agent container exists,
# so a sweep racing a mid-flight spawn would see "owner not live" and reap
# the fresh sandbox. Owners provisioned within this window are skipped.
_JANITOR_GRACE_SECONDS = 180.0

SANDBOX_LABEL = "roboco.sandbox=1"
_OWNER_LABEL_KEY = "roboco.sandbox.owner"
_AGENT_CONTAINER_PREFIX = "roboco-agent-"


async def _default_docker_run(
    args: list[str], timeout: float
) -> tuple[int, bytes, bytes]:
    """Run one `docker <args>` invocation with a hard timeout; never hangs."""
    proc = await asyncio.create_subprocess_exec(
        "docker",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        raise
    return proc.returncode or 0, stdout, stderr


class SandboxProvisionError(RuntimeError):
    """Raised when a sandbox container fails to start or become ready."""


def _owner_label(agent_id: str) -> str:
    return f"{_AGENT_CONTAINER_PREFIX}{agent_id}"


@dataclass
class SandboxProvisioner:
    """Provisions/tears down throwaway engine sibling containers.

    ``network`` is caller-supplied (the orchestrator's `AGENT_NETWORK`
    constant) rather than hardcoded here, so a future network-isolation
    change (agents moved off `roboco_default`) only has to flip the value
    the caller passes in — sandboxes ride along automatically.
    """

    network: str
    runner: DockerRunner | None = None

    def __post_init__(self) -> None:
        # owner container name -> monotonic provision time; consulted by the
        # janitor's grace check. In-memory only: the provision/janitor race
        # exists solely within one process (the startup sweep runs before any
        # spawn), and staleness can only ever delay a reap, never force one.
        self._provisioned_at: dict[str, float] = {}

    def _run(self) -> DockerRunner:
        return self.runner or _default_docker_run

    async def _ensure_image(self, image: str) -> None:
        """Pull `image` if absent so `docker run` never blocks on a cold pull."""
        run = self._run()
        rc, _, _ = await run(["image", "inspect", image], _DOCKER_EXEC_TIMEOUT_SECONDS)
        if rc == 0:
            return
        rc, _, stderr = await run(["pull", image], _DOCKER_PULL_TIMEOUT_SECONDS)
        if rc != 0:
            raise SandboxProvisionError(
                f"sandbox image pull failed for {image}: "
                f"{stderr.decode(errors='replace')}"
            )

    async def provision(self, agent_id: str, services: list[str]) -> SandboxInfo:
        """Provision the requested services; on any failure, tear down + raise."""
        unknown = sorted(set(services) - VALID_SANDBOX_SERVICES)
        if unknown:
            raise SandboxProvisionError(
                f"unknown sandbox service(s) {unknown}; valid: "
                f"{sorted(VALID_SANDBOX_SERVICES)}"
            )
        # Pre-clear: a same-named sandbox left by a crash-missed teardown
        # would otherwise fail `docker run` on the name conflict and burn a
        # spawn attempt (and a respawn-tracker strike).
        await self.teardown(agent_id)
        self._provisioned_at[_owner_label(agent_id)] = time.monotonic()
        connections: dict[str, SandboxConnection] = {}
        try:
            for service in services:
                engine = SANDBOX_ENGINES[service]
                connections[service] = await self._provision_engine(agent_id, engine)
        except Exception:
            await self.teardown(agent_id)
            raise
        return SandboxInfo(services=connections)

    async def _provision_engine(
        self, agent_id: str, engine: SandboxEngine
    ) -> SandboxConnection:
        name = engine.container_name(agent_id)
        password = secrets.token_hex(16)
        run = self._run()
        await self._ensure_image(engine.image)
        args = [
            "run",
            "-d",
            "--name",
            name,
            "--network",
            self.network,
            "--label",
            SANDBOX_LABEL,
            "--label",
            f"{_OWNER_LABEL_KEY}={_owner_label(agent_id)}",
        ]
        for mount in engine.tmpfs:
            args += ["--tmpfs", mount]
        args += ["--memory", "512m", "--cpus", "1"]
        args += engine.run_env(password)
        args.append(engine.image)
        args += engine.run_command(password)
        rc, _, stderr = await run(args, _DOCKER_RUN_TIMEOUT_SECONDS)
        if rc != 0:
            raise SandboxProvisionError(
                f"{engine.name} sandbox run failed for {name}: "
                f"{stderr.decode(errors='replace')}"
            )
        ready = await self._wait_ready(
            name, engine.ready_probe(password), engine.ready_deadline
        )
        if not ready:
            raise SandboxProvisionError(
                f"{engine.name} sandbox {name} did not become ready in time"
            )
        return engine.connection(name, password)

    async def _wait_ready(
        self, container: str, probe_cmd: list[str], deadline_seconds: float
    ) -> bool:
        run = self._run()
        deadline = time.monotonic() + deadline_seconds
        while time.monotonic() < deadline:
            try:
                rc, _, _ = await run(
                    ["exec", container, *probe_cmd], _DOCKER_EXEC_TIMEOUT_SECONDS
                )
            except TimeoutError:
                rc = 1
            if rc == 0:
                return True
            await asyncio.sleep(_READY_POLL_INTERVAL_SECONDS)
        return False

    async def teardown(self, agent_id: str) -> None:
        """Idempotent: stop+kill+rm every engine's sandbox container. Never raises."""
        for engine in SANDBOX_ENGINES.values():
            await self._teardown_one(engine.container_name(agent_id))

    async def _teardown_one(self, name: str) -> None:
        run = self._run()
        try:
            rc, _, _ = await run(
                ["stop", "-t", "5", name], _DOCKER_TEARDOWN_TIMEOUT_SECONDS
            )
            if rc != 0:
                with contextlib.suppress(Exception):
                    await run(["kill", name], _DOCKER_EXEC_TIMEOUT_SECONDS)
        except TimeoutError:
            with contextlib.suppress(Exception):
                await run(["kill", name], _DOCKER_EXEC_TIMEOUT_SECONDS)
        except Exception:
            pass
        with contextlib.suppress(Exception):
            await run(["rm", "-f", name], _DOCKER_TEARDOWN_TIMEOUT_SECONDS)

    async def _list_labeled_sandboxes(self) -> list[tuple[str, str]]:
        """(sandbox name, owner) pairs from `docker ps`; [] on any failure."""
        try:
            rc, stdout, _ = await self._run()(
                [
                    "ps",
                    "-a",
                    "--filter",
                    f"label={SANDBOX_LABEL}",
                    "--format",
                    '{{.Names}}\t{{.Label "' + _OWNER_LABEL_KEY + '"}}',
                ],
                _DOCKER_PS_TIMEOUT_SECONDS,
            )
        except Exception:
            return []
        if rc != 0:
            return []
        return _parse_owner_lines(stdout.decode(errors="replace"))

    async def _list_live_agent_containers(self) -> set[str] | None:
        """Names of running agent containers; None on any failure (abort sweep)."""
        try:
            rc, out, _ = await self._run()(
                [
                    "ps",
                    "--filter",
                    f"name={_AGENT_CONTAINER_PREFIX}",
                    "--format",
                    "{{.Names}}",
                ],
                _DOCKER_PS_TIMEOUT_SECONDS,
            )
        except Exception:
            return None
        return set(out.decode(errors="replace").split()) if rc == 0 else set()

    def _prune_grace(self) -> None:
        """Drop provision timestamps past the janitor grace window."""
        now = time.monotonic()
        self._provisioned_at = {
            owner: at
            for owner, at in self._provisioned_at.items()
            if now - at < _JANITOR_GRACE_SECONDS
        }

    async def janitor_sweep(self) -> None:
        """Remove sandbox containers whose owning agent container is gone.

        Async, error-isolated: any docker-call failure aborts this sweep pass
        (never raises), retried on the next tick.
        """
        sandboxes = await self._list_labeled_sandboxes()
        if not sandboxes:
            return
        live = await self._list_live_agent_containers()
        if live is None:
            return
        self._prune_grace()
        for name, owner in sandboxes:
            if owner and owner not in live and owner not in self._provisioned_at:
                await self._teardown_one(name)


_PS_LINE_FIELDS = 2


def _parse_owner_lines(output: str) -> list[tuple[str, str]]:
    """Parse `docker ps --format "{{.Names}}\\t{{.Label ...}}"` output lines."""
    pairs: list[tuple[str, str]] = []
    for line in output.splitlines():
        parts = line.split("\t", 1)
        if len(parts) != _PS_LINE_FIELDS:
            continue
        name, owner = parts[0].strip(), parts[1].strip()
        if name:
            pairs.append((name, owner))
    return pairs
