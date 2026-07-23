"""Codex (OPENAI) and Gemini (GEMINI) are V1 delivery-roles-only — neither has
an interactive-session driver image (unlike GROK's dedicated
GROK_PROMPTER_IMAGE / GROK_SECRETARY_IMAGE). Routing either to the persistent
Intake/Secretary agent must refuse loudly instead of silently falling through
to the plain Claude SDK-driver image with a mismatched provider env.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from roboco.models.base import ModelProvider
from roboco.runtime.orchestrator import (
    INTAKE_AGENT_ID,
    SECRETARY_AGENT_ID,
    AgentOrchestrator,
    _reject_interactive_unsupported_provider,
)
from roboco.services import prompter_live


def _make_minimal_orchestrator() -> AgentOrchestrator:
    with patch.object(AgentOrchestrator, "__init__", return_value=None):
        orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._instances = {}
    orch._bg_tasks = set()
    orch._running = True
    orch._intake_spawn_lock = asyncio.Lock()
    orch._secretary_spawn_lock = asyncio.Lock()
    return orch


@pytest.fixture(autouse=True)
def _fresh_registry() -> Any:
    prev = prompter_live._RegistryHolder.instance
    prompter_live._RegistryHolder.instance = prompter_live.PrompterLiveRegistry()
    yield
    prompter_live._RegistryHolder.instance = prev


# ---------------------------------------------------------------------------
# Unit-level: the pure guard function itself.
# ---------------------------------------------------------------------------


class TestRejectInteractiveUnsupportedProvider:
    @pytest.mark.parametrize("provider", [ModelProvider.OPENAI, ModelProvider.GEMINI])
    def test_raises_for_delivery_only_providers(self, provider: ModelProvider) -> None:
        with pytest.raises(RuntimeError, match="delivery-roles-only"):
            _reject_interactive_unsupported_provider(INTAKE_AGENT_ID, provider)

    @pytest.mark.parametrize(
        "provider",
        [
            ModelProvider.ANTHROPIC,
            ModelProvider.GROK,
            ModelProvider.OLLAMA_CLOUD,
            ModelProvider.LOCAL,
        ],
    )
    def test_passes_for_interactive_capable_providers(
        self, provider: ModelProvider
    ) -> None:
        _reject_interactive_unsupported_provider(INTAKE_AGENT_ID, provider)  # no raise


# ---------------------------------------------------------------------------
# Intake spawn refusal — surfaces on the relay, container never launched.
# ---------------------------------------------------------------------------


class TestIntakeSpawnRefusesDeliveryOnlyProvider:
    @pytest.mark.parametrize("provider", [ModelProvider.OPENAI, ModelProvider.GEMINI])
    @pytest.mark.asyncio
    async def test_refuses_before_any_container_work(
        self, monkeypatch: pytest.MonkeyPatch, provider: ModelProvider
    ) -> None:
        orch = _make_minimal_orchestrator()

        async def _clone(*_a: Any, **_k: Any) -> tuple[str, list[str]]:
            return "/data/workspaces/roboco/board/intake-1", ["/cwd"]

        async def _route(_aid: str) -> Any:
            return SimpleNamespace(
                provider_type=provider,
                model_name="whatever",
                base_url=None,
                auth_token=None,
            )

        run_calls: list[list[str]] = []

        async def _run(cmd: list[str]) -> str:
            run_calls.append(cmd)
            return "containerid0123456789"

        monkeypatch.setattr(orch, "_clone_intake_scope", _clone)
        monkeypatch.setattr(orch, "_resolve_agent_route", _route)
        monkeypatch.setattr(
            orch, "_generate_composed_prompt", lambda *_a, **_k: Path("/tmp/p.md")
        )
        monkeypatch.setattr(orch, "_run_container_cmd", _run)

        registry = prompter_live.get_live_registry()
        pushed: list[tuple[str, dict[str, Any]]] = []
        closed: list[str] = []
        monkeypatch.setattr(registry, "push", lambda sid, ev: pushed.append((sid, ev)))
        monkeypatch.setattr(registry, "close", closed.append)
        registry.open("sess-refuse", INTAKE_AGENT_ID)

        await orch._spawn_intake_container_guarded(
            "sess-refuse", project_slug="roboco", product_id=None, initial_message=None
        )

        assert not run_calls  # no container was ever launched
        assert len(pushed) == 1
        assert pushed[0][1]["kind"] == "error"
        assert "delivery-roles-only" in pushed[0][1]["text"]
        assert closed == ["sess-refuse"]
        assert INTAKE_AGENT_ID not in orch._instances


# ---------------------------------------------------------------------------
# Secretary spawn refusal — same shape, same guard.
# ---------------------------------------------------------------------------


class TestSecretarySpawnRefusesDeliveryOnlyProvider:
    @pytest.mark.parametrize("provider", [ModelProvider.OPENAI, ModelProvider.GEMINI])
    @pytest.mark.asyncio
    async def test_refuses_before_any_container_work(
        self, monkeypatch: pytest.MonkeyPatch, provider: ModelProvider
    ) -> None:
        orch = _make_minimal_orchestrator()

        async def _route(_aid: str) -> Any:
            return SimpleNamespace(
                provider_type=provider,
                model_name="whatever",
                base_url=None,
                auth_token=None,
            )

        run_calls: list[list[str]] = []

        async def _run(cmd: list[str]) -> str:
            run_calls.append(cmd)
            return "containerid0123456789"

        monkeypatch.setattr(orch, "_resolve_agent_route", _route)
        monkeypatch.setattr(
            orch, "_generate_composed_prompt", lambda *_a, **_k: Path("/tmp/p.md")
        )
        monkeypatch.setattr(orch, "_run_container_cmd", _run)

        registry = prompter_live.get_live_registry()
        pushed: list[tuple[str, dict[str, Any]]] = []
        closed: list[str] = []
        monkeypatch.setattr(registry, "push", lambda sid, ev: pushed.append((sid, ev)))
        monkeypatch.setattr(registry, "close", closed.append)
        registry.open("sess-sec-refuse", SECRETARY_AGENT_ID)

        await orch._spawn_secretary_container_guarded(
            "sess-sec-refuse", initial_message=None
        )

        assert not run_calls  # no container was ever launched
        assert len(pushed) == 1
        assert pushed[0][1]["kind"] == "error"
        assert "delivery-roles-only" in pushed[0][1]["text"]
        assert closed == ["sess-sec-refuse"]
        assert SECRETARY_AGENT_ID not in orch._instances
