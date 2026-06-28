"""Parked-provider spawn must short-circuit BEFORE the expensive prepare.

``spawn_agent`` runs the parked-provider check (``_provider_spawn_parked``) to
avoid re-spawning a container into a rate-limited / overloaded provider every
dispatcher tick. The check only needs ``provider_type``, which is cheaply
resolvable via ``_resolve_agent_route``. Running the full
``_prepare_agent_spawn`` first — which writes the blueprint / settings /
briefing / MCP-config files, ensures the agent image, and registers a STARTING
``AgentInstance`` in ``_instances`` — only to bail at the parked check wastes
all that file I/O every tick the provider stays parked, and leaves a STARTING
instance registered then downgraded to OFFLINE.

The fix: resolve the route + run the parked check BEFORE ``_prepare_agent_spawn``,
bailing with a minimal unregistered OFFLINE instance. The existing-running
check stays first (inside the lock) so a running agent is never bailed.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from roboco.models.runtime import AgentInstance
from roboco.runtime.orchestrator import AgentOrchestrator, AgentState


def _make_orchestrator() -> AgentOrchestrator:
    # __new__ + skip __init__: avoid all constructor I/O.
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._instances = {}
    orch._lock = asyncio.Lock()
    orch._tick_handled_tasks = set()
    orch._bg_tasks = set()
    orch._running = True
    return orch


def _wire(monitor: dict[str, Any]) -> Any:
    """Build the mock wiring closure capturing call counts in ``monitor``."""

    async def _readiness_gate(_aid: str, _tid: str | None) -> None:
        return None

    async def _git_context(_gc: Any, _tid: str | None) -> None:
        return None

    async def _route(_aid: str) -> Any:
        monitor["route_calls"] += 1
        return SimpleNamespace(
            provider_type=SimpleNamespace(value="anthropic"),
            model_name="opus",
            base_url=None,
            auth_token=None,
        )

    async def _prepare(*_a: Any, **_k: Any) -> Any:
        monitor["prepare_calls"] += 1
        # Mirrors the real prepare's registration side-effect so the RED test
        # observes the STARTING instance the current code leaks.
        cfg = SimpleNamespace(provider_type="anthropic", model="opus")
        inst = AgentInstance(agent_id="be-dev-1", state=AgentState.STARTING, config=cfg)
        return cfg, inst, None

    return _readiness_gate, _git_context, _route, _prepare


@pytest.mark.asyncio
async def test_parked_spawn_skips_prepare_and_does_not_register(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the provider is parked, ``_prepare_agent_spawn`` (all the file
    writes + image ensure + STARTING registration) must NOT run, and no
    instance may be left registered in ``_instances``."""
    orch = _make_orchestrator()
    monitor = {"route_calls": 0, "prepare_calls": 0}
    _rg, _gc, _route, _prepare = _wire(monitor)

    monkeypatch.setattr(orch, "_readiness_gate", _rg)
    monkeypatch.setattr(orch, "_resolve_spawn_git_context", _gc)
    monkeypatch.setattr(orch, "_resolve_agent_route", _route)
    monkeypatch.setattr(orch, "_prepare_agent_spawn", _prepare)
    monkeypatch.setattr(orch, "_provider_spawn_parked", AsyncMock(return_value=True))

    result = await orch.spawn_agent(agent_id="be-dev-1", task_id="task-9")

    # The parked check ran (route resolved for the cheap provider_type lookup).
    assert monitor["route_calls"] >= 1
    # The expensive prepare was NOT called — the whole point of the fix.
    assert monitor["prepare_calls"] == 0
    # Bailed with an OFFLINE instance, no container launched.
    assert isinstance(result, AgentInstance)
    assert result.state is AgentState.OFFLINE
    # No STARTING/OFFLINE instance left lingering in _instances.
    assert "be-dev-1" not in orch._instances
    # Task marked handled so later dispatchers in this tick skip it.
    assert "task-9" in orch._tick_handled_tasks


@pytest.mark.asyncio
async def test_not_parked_spawn_still_runs_prepare_and_launches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity: when the provider is NOT parked, the normal prepare + launch
    path runs unchanged — the shortcut only short-circuits the parked case."""
    orch = _make_orchestrator()
    monitor = {"route_calls": 0, "prepare_calls": 0}
    _rg, _gc, _route, _prepare = _wire(monitor)

    launched: list[bool] = []

    async def _launch(*_a: Any, **_k: Any) -> AgentInstance:
        launched.append(True)
        return AgentInstance(
            agent_id="be-dev-1",
            state=AgentState.ACTIVE,
            config=SimpleNamespace(provider_type="anthropic", model="opus"),
        )

    monkeypatch.setattr(orch, "_readiness_gate", _rg)
    monkeypatch.setattr(orch, "_resolve_spawn_git_context", _gc)
    monkeypatch.setattr(orch, "_resolve_agent_route", _route)
    monkeypatch.setattr(orch, "_prepare_agent_spawn", _prepare)
    monkeypatch.setattr(orch, "_provider_spawn_parked", AsyncMock(return_value=False))
    monkeypatch.setattr(orch, "_launch_spawn", _launch)

    await orch.spawn_agent(agent_id="be-dev-1", task_id="task-9")

    assert monitor["prepare_calls"] == 1
    assert launched == [True]


@pytest.mark.asyncio
async def test_running_agent_not_bailed_by_parked_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A running (ACTIVE) agent whose provider gets parked mid-flight must be
    returned as-is — the parked shortcut must NOT replace a live instance with
    a fresh OFFLINE one. The existing-running check stays first."""
    orch = _make_orchestrator()
    monitor = {"route_calls": 0, "prepare_calls": 0}
    _rg, _gc, _route, _prepare = _wire(monitor)

    existing = AgentInstance(
        agent_id="be-dev-1",
        state=AgentState.ACTIVE,
        config=SimpleNamespace(provider_type="anthropic", model="opus"),
    )
    orch._instances["be-dev-1"] = existing

    monkeypatch.setattr(orch, "_readiness_gate", _rg)
    monkeypatch.setattr(orch, "_resolve_spawn_git_context", _gc)
    monkeypatch.setattr(orch, "_resolve_agent_route", _route)
    monkeypatch.setattr(orch, "_prepare_agent_spawn", _prepare)
    monkeypatch.setattr(orch, "_provider_spawn_parked", AsyncMock(return_value=True))

    result = await orch.spawn_agent(agent_id="be-dev-1", task_id="task-9")

    # The running instance is returned untouched — parked check never reached.
    assert result is existing
    assert monitor["prepare_calls"] == 0
