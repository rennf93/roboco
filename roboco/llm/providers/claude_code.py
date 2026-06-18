"""Claude Code provider — the default Anthropic-protocol Docker backend.

This is a thin adapter over the orchestrator's existing Docker spawn
(``_spawn_container`` / ``_remove_container``). It exists so the registry has a
first-class provider for the Claude Code runtime and so new backends
(:mod:`roboco.llm.providers.grok`) have a reference to mirror.

It deliberately *delegates* to the orchestrator rather than copying ~hundreds of
lines of mount/auth/CLI assembly. Moving that body into this class is the job of
the separate orchestrator-decomposition refactor; keeping it delegated here
means this seam adds the abstraction without destabilising the live spawn path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from roboco.llm.providers._docker import container_running, stop_container
from roboco.llm.providers.base import AgentProvider, ProviderError, SpawnResult

if TYPE_CHECKING:
    from pathlib import Path

    from roboco.models.runtime import OrchestratorAgentConfig as AgentConfig


class _ClaudeCodeHost(Protocol):
    """The slice of the orchestrator that ``ClaudeCodeProvider`` delegates to.

    Typing against a Protocol (rather than importing ``AgentOrchestrator``)
    keeps this module import-cycle-free and trivially mockable in tests.
    """

    async def _spawn_container(
        self,
        config: AgentConfig,
        initial_prompt: str | None = ...,
        agent_settings_path: Path | None = ...,
    ) -> str: ...

    async def _remove_container(self, container_name: str) -> None: ...


def _container_name(agent_id: str) -> str:
    """The container name the orchestrator uses for an agent."""
    return f"roboco-agent-{agent_id}"


class ClaudeCodeProvider(AgentProvider):
    """Spawn agents as Claude Code Docker containers (Anthropic protocol).

    Non-Anthropic models that *speak the Anthropic Messages API* (Ollama Cloud,
    self-hosted) also run through this provider — they are routed purely by
    ``ANTHROPIC_BASE_URL`` / ``ANTHROPIC_AUTH_TOKEN`` injection at spawn, which
    the orchestrator already handles. Backends that speak a *different* wire
    protocol (e.g. OpenAI-compatible xAI) need their own provider.
    """

    def __init__(self, host: _ClaudeCodeHost) -> None:
        self._host = host

    async def spawn(
        self,
        config: AgentConfig,
        initial_prompt: str | None = None,
        agent_settings_path: Path | None = None,
    ) -> SpawnResult:
        try:
            container_id = await self._host._spawn_container(
                config, initial_prompt, agent_settings_path
            )
        except Exception as exc:
            # Re-wrap any spawn failure as a typed ProviderError for callers.
            raise ProviderError(
                f"Claude Code spawn failed: {exc}",
                agent_id=config.agent_id,
                cause=exc,
            ) from exc
        return SpawnResult(
            instance_id=_container_name(config.agent_id),
            extra={"container_id": container_id, "model": config.model},
        )

    async def stop(self, instance_id: str, graceful: bool = True) -> None:
        await stop_container(instance_id, graceful)

    async def health_check(self, instance_id: str) -> bool:
        return await container_running(instance_id)

    async def remove(self, instance_id: str) -> None:
        # Delegate so the orchestrator's log-dump-before-remove behaviour is kept.
        await self._host._remove_container(instance_id)
