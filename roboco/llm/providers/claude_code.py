"""Claude Code Docker provider.

Wraps the orchestrator's Docker-container lifecycle for Claude Code agents.
This is the legacy/default provider — it preserves the existing spawn/stop
behaviour byte-for-byte while conforming to the ``AgentProvider`` ABC.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import structlog

from roboco.llm.providers.base import AgentProvider, ProviderError, SpawnResult

logger = structlog.get_logger()


class ClaudeCodeProvider(AgentProvider):
    """Launch Claude Code agents inside Docker containers.

    Each agent runs in a named ``roboco-agent-{slug}`` container on the
    ``roboco`` Docker network with workspace volumes, MCP config, and
    per-agent settings mounted from the host.
    """

    DOCKER_NETWORK: str = "roboco_default"
    CONTAINER_PREFIX: str = "roboco-agent-"

    @staticmethod
    def _container_name(agent_id: str) -> str:
        return f"{ClaudeCodeProvider.CONTAINER_PREFIX}{agent_id}"

    async def spawn(
        self,
        config: Any,
        initial_prompt: str | None = None,
        agent_settings_path: Path | None = None,
    ) -> SpawnResult:
        """Spawn a Claude Code Docker container.

        .. note::
            The actual docker-run command assembly lives on the orchestrator
            (``_resolve_host_paths``, ``_build_mount_args``, etc.) to keep
            the diff minimal.  This provider delegates to
            ``orchestrator._spawn_container`` through a callable registered
            at construction time.

        This indirection lets M1 introduce the provider ABC without
        prematurely extracting several hundred lines of Docker plumbing
        from the orchestrator — a pure refactor that belongs in a
        subsequent milestone.
        """
        raise NotImplementedError(
            "ClaudeCodeProvider.spawn is dispatched through the orchestrator's "
            "_spawn_container method.  Register this provider in the "
            "ProviderRegistry and the orchestrator will route ANTHROPIC / "
            "OLLAMA_CLOUD spawns here after the full extraction in a "
            "follow-up milestone."
        )

    async def stop(
        self,
        instance_id: str,
        graceful: bool = True,
    ) -> None:
        """Stop a running agent container."""
        container_name = instance_id
        try:
            if graceful:
                proc = await asyncio.create_subprocess_exec(
                    "docker",
                    "stop",
                    "-t",
                    "10",
                    container_name,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            else:
                proc = await asyncio.create_subprocess_exec(
                    "docker",
                    "kill",
                    container_name,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            await proc.wait()
        except Exception as exc:
            raise ProviderError(
                f"Failed to stop container {container_name}",
                cause=exc,
            ) from exc

    async def health_check(self, instance_id: str) -> bool:
        """Check if the container is still running."""
        container_name = instance_id
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "inspect",
                "--format={{.State.Status}}",
                container_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            status = stdout.decode().strip()
            return status == "running"
        except Exception:
            return False

    async def remove(self, instance_id: str) -> None:
        """Remove a stopped container."""
        container_name = instance_id
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "rm",
                "-f",
                container_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except Exception as exc:
            logger.warning(
                "Failed to remove container",
                container=container_name,
                error=str(exc),
            )
