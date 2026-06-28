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
from roboco.runtime.orchestrator import (
    INTAKE_AGENT_ID,
    AgentOrchestrator,
    AgentState,
)


def _grok_instance(
    provider_type: str = "grok", agent_id: str = "be-dev-1"
) -> AgentInstance:
    cfg = type("C", (), {"provider_type": provider_type, "model": "grok-build"})()
    return AgentInstance(agent_id=agent_id, state=AgentState.ACTIVE, config=cfg)


def _orch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    cap: float,
    cost: float,
    provider_type: str = "grok",
    agent_id: str = "be-dev-1",
) -> tuple[AgentOrchestrator, AsyncMock]:
    """A bare orchestrator with the cost reader + container removal stubbed."""
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._grok_max_cost_usd = cap
    orch._instances = {agent_id: _grok_instance(provider_type, agent_id)}
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
async def test_cost_over_cap_finalizes_spawn_session_before_evict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Cost-cap-killed grok container must finalize its spawn session BEFORE the
    # instance is popped: _finalize_spawn_session reads _instances[agent_id] for
    # the model + usage_session_id; otherwise the burn stays invisible.
    orch, _remove_mock = _orch(monkeypatch, cap=5.0, cost=7.5)
    finalize = AsyncMock()
    monkeypatch.setattr(orch, "_finalize_spawn_session", finalize)

    await orch._enforce_grok_cost_budget()

    finalize.assert_awaited_once()
    # finalize ran with the agent still registered (so it could read the model +
    # usage_session_id), and the instance was evicted only after.
    assert finalize.await_args is not None
    assert finalize.await_args.args[0] == "be-dev-1"


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


@pytest.mark.asyncio
async def test_kill_failure_keeps_instance_for_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # If `docker rm` raises, the over-budget container must STAY registered so the
    # sweep retries next tick (the except `continue` resilience contract).
    orch, remove_mock = _orch(monkeypatch, cap=5.0, cost=7.5)
    remove_mock.side_effect = RuntimeError("docker rm failed")

    await orch._enforce_grok_cost_budget()

    remove_mock.assert_awaited_once()
    assert "be-dev-1" in orch._instances  # not evicted -> retried next tick


@pytest.mark.asyncio
async def test_interactive_kill_closes_the_relay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Killing an interactive (intake/secretary) container over budget must close
    # its panel relay with a reason so the chat ends cleanly, not a frozen SSE.
    orch, remove_mock = _orch(monkeypatch, cap=5.0, cost=9.0, agent_id=INTAKE_AGENT_ID)
    registry = type("R", (), {"calls": []})()
    registry.close_by_agent = lambda agent_id, error: registry.calls.append(
        (agent_id, error)
    )
    monkeypatch.setattr(
        "roboco.services.prompter_live.get_live_registry", lambda: registry
    )

    await orch._enforce_grok_cost_budget()

    remove_mock.assert_awaited_once_with(f"roboco-agent-{INTAKE_AGENT_ID}")
    assert INTAKE_AGENT_ID not in orch._instances
    assert len(registry.calls) == 1
    assert registry.calls[0][0] == INTAKE_AGENT_ID
    assert "cost" in registry.calls[0][1].lower()
