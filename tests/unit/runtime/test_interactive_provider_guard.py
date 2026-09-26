"""Interactive intake/secretary honor WHATEVER provider the operator routed.

2026-09-17, operator directive: the selected provider powers ALL agents, not
just delivery roles. The V1 delivery-only refusal is retired - every provider
now has an interactive path:

  * anthropic          - the Claude SDK driver images (unchanged)
  * grok               - the grok CLI session images (unchanged)
  * hummin/codex/gemini/kimi/openrouter/nebius - the provider-generic live
    driver (roboco.agent_sdk.live_main) on roboco-agent-<cli>-live images,
    per-turn headless CLI runs with tools bridged via rendered mcpServers
    (or the pi extension for hummin)

The resolver's exemption list and the orchestrator's spawn guard are RETIRED
to empty (parity-pinned below) so no GLOBAL/ROLE row ever bounces a chat
back to Anthropic; the guard stays wired as the backstop for future
delivery-only providers.
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
    _INTERACTIVE_UNSUPPORTED_PROVIDERS,
    INTAKE_AGENT_ID,
    SECRETARY_AGENT_ID,
    AgentOrchestrator,
    _reject_interactive_unsupported_provider,
)
from roboco.services import prompter_live
from roboco.services.llm import (
    INTERACTIVE_AGENT_SLUGS,
    INTERACTIVE_UNSUPPORTED_PROVIDERS,
)


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
# The retired lists + the pure guard function.
# ---------------------------------------------------------------------------


class TestRetiredInteractiveExemptions:
    def test_guard_set_matches_the_resolver_exemption_set(self) -> None:
        """The orchestrator's literal must track the resolver's canonical
        tuple (kept separate to avoid a runtime import cycle)."""
        assert tuple(_INTERACTIVE_UNSUPPORTED_PROVIDERS) == tuple(
            INTERACTIVE_UNSUPPORTED_PROVIDERS
        )

    def test_exemption_lists_are_empty(self) -> None:
        """No provider is delivery-only anymore: a fleet-wide mode switch
        must route the chats onto the selected provider, never exempt them
        back to Anthropic."""
        assert INTERACTIVE_UNSUPPORTED_PROVIDERS == ()
        assert _INTERACTIVE_UNSUPPORTED_PROVIDERS == ()

    def test_resolver_slugs_match_the_orchestrator_agent_ids(self) -> None:
        assert set(INTERACTIVE_AGENT_SLUGS) == {INTAKE_AGENT_ID, SECRETARY_AGENT_ID}

    @pytest.mark.parametrize(
        "provider",
        [
            ModelProvider.ANTHROPIC,
            ModelProvider.GROK,
            ModelProvider.OLLAMA_CLOUD,
            ModelProvider.LOCAL,
            ModelProvider.OPENAI,
            ModelProvider.GEMINI,
            ModelProvider.KIMI,
            ModelProvider.OPENROUTER,
            ModelProvider.NEBIUS,
            ModelProvider.HUMMIN,
        ],
    )
    def test_guard_passes_for_every_current_provider(
        self, provider: ModelProvider
    ) -> None:
        """With the list retired the guard passes for every provider; it
        stays wired for future delivery-only additions."""
        _reject_interactive_unsupported_provider(INTAKE_AGENT_ID, provider)  # no raise


# ---------------------------------------------------------------------------
# Live-chat spawns go through on the formerly-refused providers.
# ---------------------------------------------------------------------------


_ROUTE_STATE: dict[str, ModelProvider] = {"provider": ModelProvider.HUMMIN}


async def _route(_aid: str) -> Any:
    """Route stub: resolves to whatever provider the test staged."""
    return SimpleNamespace(
        provider_type=_ROUTE_STATE["provider"],
        model_name="whatever",
        base_url=None,
        auth_token=None,
    )


def _mock_live_spawn(
    orch: AgentOrchestrator, monkeypatch: pytest.MonkeyPatch
) -> list[list[str]]:
    run_calls: list[list[str]] = []

    async def _clone(*_a: Any, **_k: Any) -> tuple[str, list[str]]:
        return "/data/workspaces/roboco/board/intake-1", ["/cwd"]

    async def _run(cmd: list[str]) -> str:
        run_calls.append(cmd)
        return "containerid0123456789"

    async def _remove(*_a: Any, **_k: Any) -> None:
        return None

    async def _labels(*_a: Any, **_k: Any) -> list[str]:
        return []

    async def _ensure_live(_image: str) -> None:
        return None

    async def _noop_uuid(*_a: Any, **_k: Any) -> str:
        # _scoped_project_uuid hits the DB for the live draft bridge; keep the
        # spawn-path mocks hermetic.
        return ""

    monkeypatch.setattr(orch, "_clone_intake_scope", _clone)
    monkeypatch.setattr(orch, "_scoped_project_uuid", _noop_uuid)
    monkeypatch.setattr(orch, "_run_container_cmd", _run)
    monkeypatch.setattr(orch, "_remove_container", _remove)
    monkeypatch.setattr(
        orch,
        "_generate_composed_prompt",
        lambda *_a, **_k: Path("/tmp/p.md"),
    )
    monkeypatch.setattr(
        "roboco.runtime.engines.interactive_sessions.compose_label_args", _labels
    )
    monkeypatch.setattr(orch, "_ensure_live_interactive_image", _ensure_live)
    return run_calls


class TestLiveChatsSpawnOnEveryProvider:
    @pytest.mark.parametrize(
        "provider,image,env_name",
        [
            (
                ModelProvider.HUMMIN,
                "roboco-agent-hummin-live",
                "ROBOCO_LIVE_PROVIDER=hummin",
            ),
            (
                ModelProvider.OPENAI,
                "roboco-agent-codex-live",
                "ROBOCO_LIVE_PROVIDER=openai",
            ),
            (
                ModelProvider.GEMINI,
                "roboco-agent-gemini-live",
                "ROBOCO_LIVE_PROVIDER=gemini",
            ),
            (ModelProvider.KIMI, "roboco-agent-kimi-live", "ROBOCO_LIVE_PROVIDER=kimi"),
            (
                ModelProvider.OPENROUTER,
                "roboco-agent-openrouter-live",
                "ROBOCO_LIVE_PROVIDER=openrouter",
            ),
            (
                ModelProvider.NEBIUS,
                "roboco-agent-nebius-live",
                "ROBOCO_LIVE_PROVIDER=nebius",
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_intake_spawns_the_live_image(
        self,
        monkeypatch: pytest.MonkeyPatch,
        provider: ModelProvider,
        image: str,
        env_name: str,
    ) -> None:
        _ROUTE_STATE["provider"] = provider
        orch = _make_minimal_orchestrator()
        run_calls = _mock_live_spawn(orch, monkeypatch)
        monkeypatch.setattr(orch, "_resolve_agent_route", _route)

        registry = prompter_live.get_live_registry()
        registry.open("sess-live-intake", INTAKE_AGENT_ID)
        await orch._spawn_intake_container_guarded(
            "sess-live-intake",
            project_slug="roboco",
            product_id=None,
            initial_message=None,
        )

        assert len(run_calls) == 1
        cmd = run_calls[0]
        assert cmd[-1] == image
        assert env_name in cmd

    @pytest.mark.parametrize(
        "provider,image",
        [
            (ModelProvider.HUMMIN, "roboco-agent-hummin-live"),
            (ModelProvider.OPENAI, "roboco-agent-codex-live"),
            (ModelProvider.OPENROUTER, "roboco-agent-openrouter-live"),
        ],
    )
    @pytest.mark.asyncio
    async def test_secretary_spawns_the_live_image(
        self,
        monkeypatch: pytest.MonkeyPatch,
        provider: ModelProvider,
        image: str,
    ) -> None:
        _ROUTE_STATE["provider"] = provider
        orch = _make_minimal_orchestrator()
        run_calls = _mock_live_spawn(orch, monkeypatch)

        async def _no_prompt(*_a: Any, **_k: Any) -> str:
            return "prompt"

        monkeypatch.setattr(orch, "_resolve_agent_route", _route)
        monkeypatch.setattr(orch, "_generate_composed_prompt", _no_prompt)

        registry = prompter_live.get_live_registry()
        registry.open("sess-live-sec", SECRETARY_AGENT_ID)
        await orch._spawn_secretary_container_guarded(
            "sess-live-sec", initial_message=None
        )

        assert len(run_calls) == 1
        cmd = run_calls[0]
        assert cmd[-1] == image
        assert "ROBOCO_LIVE_PROVIDER=" + provider.value in cmd
        assert SECRETARY_AGENT_ID in orch._instances
