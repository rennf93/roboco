"""The orchestrator routes only dedicated-backend providers through the registry.

GROK gets the GrokProvider; Anthropic / Ollama Cloud / self-hosted (and any
unknown value) return None so ``_spawn_container`` runs its built-in Claude Code
path unchanged. This keeps the GROK addition purely additive.
"""

from __future__ import annotations

from unittest.mock import patch

from roboco.llm.providers import GrokProvider
from roboco.runtime.orchestrator import AgentOrchestrator


def _make_orch() -> AgentOrchestrator:
    with patch.object(AgentOrchestrator, "__init__", return_value=None):
        orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._provider_registry = None
    return orch


def test_provider_for_grok_returns_grok_provider() -> None:
    assert isinstance(_make_orch()._provider_for("grok"), GrokProvider)


def test_provider_for_anthropic_returns_none() -> None:
    assert _make_orch()._provider_for("anthropic") is None


def test_provider_for_ollama_cloud_returns_none() -> None:
    assert _make_orch()._provider_for("ollama_cloud") is None


def test_provider_for_unknown_value_returns_none() -> None:
    assert _make_orch()._provider_for("bogus") is None


def test_provider_registry_built_once() -> None:
    orch = _make_orch()
    assert orch._ensure_provider_registry() is orch._ensure_provider_registry()
