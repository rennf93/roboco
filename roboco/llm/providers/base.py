"""Abstract base class for agent lifecycle providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from roboco.models.runtime import OrchestratorAgentConfig as AgentConfig
from roboco.models.runtime import OrchestratorAgentState as AgentState


@dataclass(frozen=True)
class SpawnResult:
    """Outcome of a successful provider.spawn() call.

    Attributes:
        instance_id: Provider-specific handle (Docker container ID, PID, etc.).
        agent_state: The initial state after spawn (usually ACTIVE).
        extra: Provider-specific metadata (port, URL, pid file, etc.).
    """

    instance_id: str
    agent_state: str = AgentState.ACTIVE
    extra: dict[str, Any] = field(default_factory=dict)


class ProviderError(Exception):
    """Raised when a provider fails to spawn, stop, or check an agent.

    Attributes:
        message: Human-readable description.
        agent_id: The agent slug that failed.
        cause: Optional originating exception.
    """

    def __init__(
        self,
        message: str,
        agent_id: str | None = None,
        cause: Exception | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.cause = cause
        super().__init__(message)


class AgentProvider(ABC):
    """Abstract provider for LLM agent lifecycle.

    Each concrete subclass implements the mechanics of launching,
    monitoring, and tearing down a single agent instance for a
    specific backend (Docker/Claude-Code, subprocess/Ollama, etc.).
    """

    @abstractmethod
    async def spawn(
        self,
        config: AgentConfig,
        initial_prompt: str | None = None,
        agent_settings_path: Path | None = None,
    ) -> SpawnResult:
        """Launch an agent instance for *config*.

        Args:
            config: Resolved agent configuration including provider routing.
            initial_prompt: Optional pre-prompt text injected at session start.
            agent_settings_path: Optional per-agent Claude settings file.

        Returns:
            A ``SpawnResult`` with the provider-specific instance handle.

        Raises:
            ProviderError: The agent could not be started.
        """
        ...

    @abstractmethod
    async def stop(
        self,
        instance_id: str,
        graceful: bool = True,
    ) -> None:
        """Terminate a running agent instance.

        Args:
            instance_id: The handle returned by ``spawn()``.
            graceful: If True, allow a grace period before force-killing.

        Raises:
            ProviderError: The agent could not be stopped.
        """
        ...

    @abstractmethod
    async def health_check(self, instance_id: str) -> bool:
        """Return True if the agent instance is still running.

        Args:
            instance_id: The handle returned by ``spawn()``.
        """
        ...

    @abstractmethod
    async def remove(self, instance_id: str) -> None:
        """Clean up all resources for a stopped/failed instance.

        Called after ``stop()`` to release volumes, networks, log files,
        or other provider-scoped resources.
        """
        ...
