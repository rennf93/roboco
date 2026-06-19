"""LLM agent providers.

Each provider implements the :class:`AgentProvider` lifecycle for one backend.
The :class:`ProviderRegistry` maps :class:`~roboco.models.base.ModelProvider`
values to provider instances; the orchestrator looks a provider up at spawn time
and falls back to its built-in Claude Code spawn when none is registered.

Backends:
- :class:`ClaudeCodeProvider` — Anthropic-protocol Claude Code container (default;
  also serves Ollama Cloud / self-hosted via ``ANTHROPIC_BASE_URL`` injection).
- :class:`GrokCliProvider` — xAI Grok Build via the official ``grok`` CLI on the
  SuperGrok subscription (mounted ``~/.grok`` auth, parity with the Claude path).
"""

from roboco.llm.providers.base import AgentProvider, ProviderError, SpawnResult
from roboco.llm.providers.claude_code import ClaudeCodeProvider
from roboco.llm.providers.grok import GrokCliProvider
from roboco.llm.providers.registry import ProviderNotRegisteredError, ProviderRegistry

__all__ = [
    "AgentProvider",
    "ClaudeCodeProvider",
    "GrokCliProvider",
    "ProviderError",
    "ProviderNotRegisteredError",
    "ProviderRegistry",
    "SpawnResult",
]
