"""LLM Agent Providers.

Each provider implements the ``AgentProvider`` ABC to handle agent lifecycle
(spawn, stop, health_check) for a specific LLM backend.  The
``ProviderRegistry`` maps ``ModelProvider`` enum values to their concrete
providers at startup.

Currently implemented:
- ``ClaudeCodeProvider`` — Docker container with Claude Code (legacy path)
- ``OllamaLocalProvider`` — local subprocess agent via Ollama (no Docker)
"""

from roboco.llm.providers.base import AgentProvider, ProviderError, SpawnResult
from roboco.llm.providers.claude_code import ClaudeCodeProvider
from roboco.llm.providers.ollama_local import OllamaLocalProvider
from roboco.llm.providers.opencode import OpenCodeProvider
from roboco.llm.providers.registry import ProviderRegistry

__all__ = [
    "AgentProvider",
    "ClaudeCodeProvider",
    "OllamaLocalProvider",
    "OpenCodeProvider",
    "ProviderError",
    "ProviderRegistry",
    "SpawnResult",
]
