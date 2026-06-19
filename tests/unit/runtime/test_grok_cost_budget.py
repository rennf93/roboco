"""GROK cost budget kill-switch: kill a live container over the cost ceiling.

The grok CLI exposes no live usage hook, so the budget kill-switch lives in the
orchestrator: it reads each live GROK container's captured cost (from its
usage.json, via ``_grok_cost_usd``) and kills + evicts it past
ROBOCO_GROK_MAX_COST_USD (also catching runaway-loop token burn). The usage.json
read is covered in the grok usage tests; here ``_grok_cost_usd`` is stubbed so the
kill DECISION is exercised deterministically.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from roboco.models.runtime import AgentInstance
from roboco.runtime.orchestrator import AgentOrchestrator, AgentState


def _grok_instance(provider_type: str = "grok") -> AgentInstance:
    cfg = type("C", (), {"provider_type": provider_type, "model": "grok-build"})()
    return AgentInstance(agent_id="be-dev-1", state=AgentState.ACTIVE, config=cfg)


def _orch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    cap: float,
    cost: float,
    provider_type: str = "grok",
) -> tuple[AgentOrchestrator, AsyncMock]:
    """A bare orchestrator with the cost reader + container removal stubbed."""
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._grok_max_cost_usd = cap
    orch._instances = {"be-dev-1": _grok_instance(provider_type)}
    monkeypatch.setattr(orch, "_grok_cost_usd", lambda _agent_id: cost)
    remove_mock = AsyncMock()
    monkeypatch.setattr(orch, "_remove_container", remove_mock)
    return orch, remove_mock


@pytest.mark.asyncio
async def test_cost_over_cap_kills_and_evicts(monkeypatch: pytest.MonkeyPatch) -> None:
    orch, remove_mock = _orch(monkeypatch, cap=5.0, cost=7.5)

    await orch._enforce_grok_cost_budget()

    remove_mock.assert_awaited_once_with("roboco-agent-be-dev-1")
    assert "be-dev-1" not in orch._instances


@pytest.mark.asyncio
async def test_cost_under_cap_spares(monkeypatch: pytest.MonkeyPatch) -> None:
    orch, remove_mock = _orch(monkeypatch, cap=5.0, cost=1.0)

    await orch._enforce_grok_cost_budget()

    remove_mock.assert_not_awaited()
    assert "be-dev-1" in orch._instances


@pytest.mark.asyncio
async def test_cap_zero_disables_the_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    orch, remove_mock = _orch(monkeypatch, cap=0.0, cost=999.0)

    await orch._enforce_grok_cost_budget()

    remove_mock.assert_not_awaited()
    assert "be-dev-1" in orch._instances


@pytest.mark.asyncio
async def test_non_grok_container_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    orch, remove_mock = _orch(
        monkeypatch, cap=5.0, cost=999.0, provider_type="anthropic"
    )

    await orch._enforce_grok_cost_budget()

    remove_mock.assert_not_awaited()
    assert "be-dev-1" in orch._instances
