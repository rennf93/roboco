"""Provider registry — maps ``ModelProvider`` enum values to provider instances.

Usage::

    from roboco.llm.providers import ProviderRegistry
    from roboco.models.base import ModelProvider

    registry = ProviderRegistry()
    provider = registry.get(ModelProvider.ANTHROPIC)
    result = await provider.spawn(config, initial_prompt)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from roboco.models.base import ModelProvider

if TYPE_CHECKING:
    from roboco.llm.providers.base import AgentProvider


class ProviderNotRegisteredError(LookupError):
    """Raised when no provider is registered for a ``ModelProvider`` value."""

    def __init__(self, provider_type: ModelProvider) -> None:
        self.provider_type = provider_type
        super().__init__(
            f"No provider registered for {provider_type.value!r}. "
            f"Available: {[p.value for p in ModelProvider]}"
        )


class ProviderRegistry:
    """Maps ``ModelProvider`` enum values to ``AgentProvider`` instances.

    The registry is populated at startup (or in tests) and provides a single
    ``get()`` method that the orchestrator calls to resolve the right provider
    for a given agent route.

    The default configuration registers:
        - ``ANTHROPIC``    → ``ClaudeCodeProvider`` (Docker, legacy)
        - ``OLLAMA_CLOUD`` → ``ClaudeCodeProvider`` (Docker, same image)
        - ``LOCAL``        → ``OllamaLocalProvider`` (subprocess, no Docker)
        - ``OPENAI``       → not registered by default (reserved)
    """

    def __init__(self) -> None:
        self._providers: dict[ModelProvider, AgentProvider] = {}

    def register(self, provider_type: ModelProvider, provider: AgentProvider) -> None:
        """Register *provider* for *provider_type*.

        Replaces any existing registration for the same type.
        """
        self._providers[provider_type] = provider

    def get(self, provider_type: ModelProvider) -> AgentProvider:
        """Return the registered provider for *provider_type*.

        Raises:
            ProviderNotRegisteredError: No provider for this type.
        """
        provider = self._providers.get(provider_type)
        if provider is None:
            raise ProviderNotRegisteredError(provider_type)
        return provider

    def is_registered(self, provider_type: ModelProvider) -> bool:
        """Return True if a provider is registered for *provider_type*."""
        return provider_type in self._providers

    def registered_types(self) -> list[ModelProvider]:
        """Return the list of provider types that have been registered."""
        return list(self._providers.keys())

    def unregister(self, provider_type: ModelProvider) -> None:
        """Remove a provider registration."""
        self._providers.pop(provider_type, None)
