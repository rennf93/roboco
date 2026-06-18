"""GROK cost budget kill-switch: kill a live container over the cost ceiling.

opencode exposes no usage hook to a plugin, so the budget kill-switch lives in
the orchestrator: it reads each live GROK container's cumulative opencode cost
and kills + evicts it past ROBOCO_GROK_MAX_COST_USD (also catching runaway-loop
token burn). The cost computation itself is covered in opencode_usage tests; here
cost_for_session is stubbed so the kill DECISION is exercised deterministically.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from roboco.models.runtime import AgentInstance
from roboco.runtime.orchestrator import AgentOrchestrator, AgentState

_COST_FN = "roboco.llm.providers.opencode_usage.cost_for_session"


def _grok_instance(provider_type: str = "grok") -> AgentInstance:
    cfg = type("C", (), {"provider_type": provider_type, "model": "grok-build-0.1"})()
    return AgentInstance(agent_id="be-dev-1", state=AgentState.ACTIVE, config=cfg)


@pytest.mark.asyncio
async def test_cost_over_cap_kills_and_evicts(monkeypatch: pytest.MonkeyPatch) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._grok_max_cost_usd = 5.0
    orch._instances = {"be-dev-1": _grok_instance()}
    remove_mock = AsyncMock()
    monkeypatch.setattr(orch, "_remove_container", remove_mock)
    monkeypatch.setattr(_COST_FN, lambda *_a, **_k: (None, 7.5))

    await orch._enforce_grok_cost_budget()

    remove_mock.assert_awaited_once_with("roboco-agent-be-dev-1")
    assert "be-dev-1" not in orch._instances


@pytest.mark.asyncio
async def test_cost_under_cap_spares(monkeypatch: pytest.MonkeyPatch) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._grok_max_cost_usd = 5.0
    orch._instances = {"be-dev-1": _grok_instance()}
    remove_mock = AsyncMock()
    monkeypatch.setattr(orch, "_remove_container", remove_mock)
    monkeypatch.setattr(_COST_FN, lambda *_a, **_k: (None, 1.0))

    await orch._enforce_grok_cost_budget()

    remove_mock.assert_not_awaited()
    assert "be-dev-1" in orch._instances


@pytest.mark.asyncio
async def test_cap_zero_disables_the_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._grok_max_cost_usd = 0.0
    orch._instances = {"be-dev-1": _grok_instance()}
    remove_mock = AsyncMock()
    monkeypatch.setattr(orch, "_remove_container", remove_mock)
    monkeypatch.setattr(_COST_FN, lambda *_a, **_k: (None, 999.0))

    await orch._enforce_grok_cost_budget()

    remove_mock.assert_not_awaited()
    assert "be-dev-1" in orch._instances


@pytest.mark.asyncio
async def test_non_grok_container_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._grok_max_cost_usd = 5.0
    orch._instances = {"be-dev-1": _grok_instance(provider_type="anthropic")}
    remove_mock = AsyncMock()
    monkeypatch.setattr(orch, "_remove_container", remove_mock)
    monkeypatch.setattr(_COST_FN, lambda *_a, **_k: (None, 999.0))

    await orch._enforce_grok_cost_budget()

    remove_mock.assert_not_awaited()
    assert "be-dev-1" in orch._instances
