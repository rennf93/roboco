"""Abstract base class for agent lifecycle providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from roboco.models.runtime import OrchestratorAgentConfig as AgentConfig


@dataclass(frozen=True)
class SpawnResult:
    """Result of a successful agent spawn.

    Attributes:
        instance_id: Unique instance identifier (container ID, PID, etc.).
        agent_state: Initial state after successful spawn.
        extra: Provider-specific metadata (container name, PID, URL, etc.).
    """

    instance_id: str
    agent_state: str = "active"
    extra: dict[str, object] = field(default_factory=dict)


class ProviderError(Exception):
    """Raised by a provider when an agent lifecycle operation fails.

    Attributes:
        message: Human-readable error description.
        agent_id: The agent that was being operated on.
        cause: The original exception that caused this error.
    """

    def __init__(
        self,
        message: str,
        agent_id: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.agent_id = agent_id
        self.cause = cause


class AgentProvider(ABC):
    """Abstract base for agent lifecycle providers.

    Each provider implements how an agent is spawned, stopped, health-checked,
    and removed.  The orchestrator is provider-agnostic — it calls these methods
    without knowing whether the agent runs in Docker, a local process, or a
    remote host.
    """

    @abstractmethod
    async def spawn(
        self,
        config: AgentConfig,
        initial_prompt: str | None = None,
        agent_settings_path: Path | None = None,
    ) -> SpawnResult:
        """Spawn an agent instance and return a handle to it."""
        ...

    @abstractmethod
    async def stop(self, instance_id: str, graceful: bool = True) -> None:
        """Stop a running agent instance.

        Args:
            instance_id: Provider-specific instance identifier.
            graceful: If True, allow graceful shutdown; otherwise force-kill.
        """
        ...

    @abstractmethod
    async def health_check(self, instance_id: str) -> bool:
        """Check if the agent instance is still alive."""
        ...

    @abstractmethod
    async def remove(self, instance_id: str) -> None:
        """Remove the agent instance, releasing all resources."""
        ...
