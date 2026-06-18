"""Provider registry — maps :class:`ModelProvider` values to provider instances.

Usage::

    registry = ProviderRegistry()
    registry.register(ModelProvider.GROK, GrokProvider(...))
    provider = registry.get(ModelProvider.GROK)
    result = await provider.spawn(config, initial_prompt)

The orchestrator builds the registry once at startup and calls ``get()`` for any
provider that has a dedicated backend. Providers that are *not* registered fall
back to the orchestrator's built-in Claude Code container spawn — so adding a new
backend is additive and never destabilises the existing Anthropic / Ollama /
self-hosted paths.
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
            f"Registered: {[p.value for p in ModelProvider]}"
        )


class ProviderRegistry:
    """Maps ``ModelProvider`` values to ``AgentProvider`` instances."""

    def __init__(self) -> None:
        self._providers: dict[ModelProvider, AgentProvider] = {}

    def register(self, provider_type: ModelProvider, provider: AgentProvider) -> None:
        """Register *provider* for *provider_type* (replaces any existing)."""
        self._providers[provider_type] = provider

    def get(self, provider_type: ModelProvider) -> AgentProvider:
        """Return the provider for *provider_type* or raise."""
        provider = self._providers.get(provider_type)
        if provider is None:
            raise ProviderNotRegisteredError(provider_type)
        return provider

    def get_or_none(self, provider_type: ModelProvider) -> AgentProvider | None:
        """Return the provider for *provider_type*, or ``None`` if unregistered.

        The orchestrator uses this to decide whether a dedicated backend
        exists; ``None`` means "use the built-in Claude Code spawn".
        """
        return self._providers.get(provider_type)

    def is_registered(self, provider_type: ModelProvider) -> bool:
        """Return True if a provider is registered for *provider_type*."""
        return provider_type in self._providers

    def registered_types(self) -> list[ModelProvider]:
        """Return all registered provider types."""
        return list(self._providers.keys())

    def unregister(self, provider_type: ModelProvider) -> None:
        """Remove a provider registration (no-op if absent)."""
        self._providers.pop(provider_type, None)
