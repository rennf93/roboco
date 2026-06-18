"""Abstract base class for agent lifecycle providers.

An ``AgentProvider`` encapsulates *how* an agent is spawned, stopped,
health-checked, and removed for one LLM backend. The orchestrator stays
provider-agnostic: it resolves a provider from the :class:`ProviderRegistry`
by the agent's :class:`~roboco.models.base.ModelProvider` and calls these
methods without knowing whether the agent runs as a Claude Code container, an
OpenAI-protocol subprocess, or a remote host.

This is the seam new backends plug into. ``GROK`` (xAI / grok-build-0.1) is the
first OpenAI-protocol provider; see :mod:`roboco.llm.providers.grok`.
"""

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
        instance_id: Provider-specific handle (container id, PID, ...).
        agent_state: Initial state after a successful spawn.
        extra: Provider-specific metadata (container name, model, url, ...).
    """

    instance_id: str
    agent_state: str = "active"
    extra: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class InteractiveSpawnSpec:
    """Inputs to spawn a long-lived **interactive** agent (intake / secretary).

    Interactive roles run a held-open chat session the human types into, rather
    than a one-shot task. This bundles everything a provider's
    :meth:`AgentProvider.spawn_interactive` needs so it never imports
    orchestrator internals: the resolved :class:`AgentConfig` (carries the
    provider routing creds, model, mcp config, agent id), the per-session id the
    relay/SSE keys on, the role-specific interactive image, and — for the
    secretary, whose directive tools authenticate to the API — the HMAC token.
    """

    config: AgentConfig
    image: str
    session_id: str
    role: str  # "prompter" | "secretary"
    agent_token: str | None = None
    agent_settings_path: Path | None = None


class ProviderError(Exception):
    """Raised when an agent-lifecycle operation fails inside a provider.

    Attributes:
        message: Human-readable description.
        agent_id: The agent being operated on, if known.
        cause: The original exception, if any.
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
    """Abstract base for an agent-lifecycle backend.

    Every concrete provider implements the full one-shot lifecycle so the
    orchestrator can drive any backend through one interface. Interactive
    (held-open chat) spawns are opt-in: a provider that serves the human-facing
    intake/secretary roles sets ``supports_interactive = True`` and overrides
    :meth:`spawn_interactive`. One-shot-only providers inherit the default,
    which declines — so adding the interactive surface breaks no existing
    provider.
    """

    #: Whether this backend can spawn long-lived interactive (chat) agents.
    supports_interactive: bool = False

    @abstractmethod
    async def spawn(
        self,
        config: AgentConfig,
        initial_prompt: str | None = None,
        agent_settings_path: Path | None = None,
    ) -> SpawnResult:
        """Spawn an agent instance and return a handle to it."""
        ...

    async def spawn_interactive(self, spec: InteractiveSpawnSpec) -> SpawnResult:
        """Spawn a long-lived interactive agent (intake / secretary).

        Non-abstract: the default declines so one-shot-only providers need no
        change. Providers that set ``supports_interactive = True`` override this.
        """
        raise ProviderError(
            f"{type(self).__name__} does not support interactive spawns",
            agent_id=spec.config.agent_id,
        )

    @abstractmethod
    async def stop(self, instance_id: str, graceful: bool = True) -> None:
        """Stop a running instance (graceful shutdown unless ``graceful=False``)."""
        ...

    @abstractmethod
    async def health_check(self, instance_id: str) -> bool:
        """Return True if the instance is still alive."""
        ...

    @abstractmethod
    async def remove(self, instance_id: str) -> None:
        """Remove the instance, releasing all resources."""
        ...
