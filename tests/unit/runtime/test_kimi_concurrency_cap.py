"""Kimi per-provider spawn concurrency cap.

Every Kimi container shares ONE OAuth refresh-token chain (a host-mounted,
read-write ``~/.kimi-code`` — see ``roboco.llm.providers.kimi``'s module
docstring). Moonshot rotates refresh tokens with a short reuse-grace: two
containers refreshing near-simultaneously fork the chain, and a later
redemption of a stale ancestor triggers family revocation — fleet-wide Kimi
auth dies and the CEO must re-login (a live incident, 2026-07-29). With one
Kimi consumer at a time refreshes stay strictly sequential.

``settings.kimi_max_concurrent`` (default 1) caps live Kimi containers,
enforced in ``AgentOrchestrator.spawn_agent`` at the exact same chokepoints as
the provider-parked check (see ``test_parked_spawn_shortcut.py`` for the
pre-prepare / post-prepare shape this mirrors). No other provider is capped.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from roboco.config import settings
from roboco.models.runtime import AgentInstance
from roboco.runtime import orchestrator as orch_module
from roboco.runtime.orchestrator import AgentConfig, AgentOrchestrator, AgentState


def _make_orchestrator() -> AgentOrchestrator:
    # __new__ + skip __init__: avoid all constructor I/O (mirrors
    # test_parked_spawn_shortcut.py's _make_orchestrator).
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._instances = {}
    orch._lock = asyncio.Lock()
    orch._tick_handled_tasks = set()
    orch._bg_tasks = set()
    orch._running = True
    return orch


def _live_instance(
    agent_id: str, provider_type: str, state: AgentState
) -> AgentInstance:
    cfg = AgentConfig(
        agent_id=agent_id, blueprint_path=Path(), provider_type=provider_type
    )
    return AgentInstance(agent_id=agent_id, state=state, config=cfg)


def _wire(monitor: dict[str, Any], provider_value: str) -> Any:
    """Build the mock wiring closure capturing call counts in ``monitor``."""

    async def _readiness_gate(_aid: str, _tid: str | None) -> None:
        return None

    async def _git_context(_gc: Any, _tid: str | None) -> None:
        return None

    async def _route(_aid: str, _tid: str | None = None) -> Any:
        monitor["route_calls"] += 1
        return SimpleNamespace(
            provider_type=SimpleNamespace(value=provider_value),
            model_name="kimi-code/k3" if provider_value == "kimi" else "opus",
            base_url=None,
            auth_token=None,
        )

    async def _prepare(agent_id: str, *_a: Any, **_k: Any) -> Any:
        # Registers the STARTING instance under the requested agent_id like the
        # real _prepare_agent_spawn does — the post-prepare gate re-check runs
        # against _instances WITH this entry present (the self-count regression
        # only reproduces with it registered).
        monitor["prepare_calls"] += 1
        cfg = SimpleNamespace(provider_type=provider_value, model="model")
        inst = AgentInstance(
            agent_id=agent_id,
            state=AgentState.STARTING,
            config=AgentConfig(
                agent_id=agent_id, blueprint_path=Path(), provider_type=provider_value
            ),
        )
        monitor["orch"]._instances[agent_id] = inst
        return cfg, inst, None

    return _readiness_gate, _git_context, _route, _prepare


def _wire_orch(
    orch: AgentOrchestrator,
    monkeypatch: pytest.MonkeyPatch,
    monitor: dict[str, Any],
    provider_value: str,
) -> None:
    monitor["orch"] = orch
    _rg, _gc, _route, _prepare = _wire(monitor, provider_value)
    monkeypatch.setattr(orch, "_readiness_gate", _rg)
    monkeypatch.setattr(orch, "_resolve_spawn_git_context", _gc)
    monkeypatch.setattr(orch, "_resolve_agent_route", _route)
    monkeypatch.setattr(orch, "_prepare_agent_spawn", _prepare)
    monkeypatch.setattr(orch, "_provider_spawn_parked", AsyncMock(return_value=False))


def _capture_info_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []

    def _info(event: str, **kwargs: Any) -> None:
        calls.append((event, kwargs))

    monkeypatch.setattr(orch_module.logger, "info", _info)
    return calls


# ---------------------------------------------------------------------------
# Helper-level unit tests
# ---------------------------------------------------------------------------


def test_provider_concurrency_cap_kimi_only() -> None:
    assert (
        AgentOrchestrator._provider_concurrency_cap("kimi")
        == settings.kimi_max_concurrent
    )
    assert AgentOrchestrator._provider_concurrency_cap("anthropic") is None
    assert AgentOrchestrator._provider_concurrency_cap("openai") is None
    assert AgentOrchestrator._provider_concurrency_cap(None) is None


def test_live_provider_instance_count_and_capacity() -> None:
    orch = _make_orchestrator()
    assert orch._live_provider_instance_count("kimi") == 0
    assert orch._provider_spawn_at_capacity("kimi") is False

    orch._instances["be-dev-1"] = _live_instance("be-dev-1", "kimi", AgentState.ACTIVE)
    assert orch._live_provider_instance_count("kimi") == 1
    assert orch._provider_spawn_at_capacity("kimi") is True  # default cap 1

    # OFFLINE/WAITING_LONG never occupy a slot (mirrors _existing_running_instance).
    orch._instances["be-dev-1"].state = AgentState.OFFLINE
    assert orch._live_provider_instance_count("kimi") == 0
    assert orch._provider_spawn_at_capacity("kimi") is False

    # An uncapped provider is never at capacity, however many live instances.
    orch._instances["fe-dev-1"] = _live_instance(
        "fe-dev-1", "anthropic", AgentState.ACTIVE
    )
    assert orch._provider_spawn_at_capacity("anthropic") is False


# ---------------------------------------------------------------------------
# spawn_agent integration: exercises the exact chokepoint every kimi spawn
# path crosses (mirrors test_parked_spawn_shortcut.py).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sole_kimi_spawn_passes_its_own_post_prepare_cap_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 2026-07-29 fleet wedge: with cap=1 and NO other kimi instance, the
    post-prepare gate re-check counted the spawn's own just-registered
    STARTING instance (1 >= 1) and cancelled every kimi spawn forever. The
    agent being spawned must be excluded from its own capacity check."""
    orch = _make_orchestrator()
    monitor = {"route_calls": 0, "prepare_calls": 0}
    _wire_orch(orch, monkeypatch, monitor, "kimi")

    launched: list[bool] = []

    async def _launch(*_a: Any, **_k: Any) -> AgentInstance:
        launched.append(True)
        return AgentInstance(agent_id="main-pm", state=AgentState.ACTIVE, config=None)

    monkeypatch.setattr(orch, "_launch_spawn", _launch)

    await orch.spawn_agent(agent_id="main-pm", task_id="task-1")

    assert monitor["prepare_calls"] == 1
    assert launched == [True]


@pytest.mark.asyncio
async def test_second_concurrent_kimi_spawn_skipped_while_first_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = _make_orchestrator()
    orch._instances["be-dev-1"] = _live_instance("be-dev-1", "kimi", AgentState.ACTIVE)
    monitor = {"route_calls": 0, "prepare_calls": 0}
    _wire_orch(orch, monkeypatch, monitor, "kimi")
    info_calls = _capture_info_logs(monkeypatch)

    result = await orch.spawn_agent(agent_id="fe-dev-1", task_id="task-9")

    assert monitor["prepare_calls"] == 0
    assert isinstance(result, AgentInstance)
    assert result.state is AgentState.OFFLINE
    assert "fe-dev-1" not in orch._instances
    assert "task-9" in orch._tick_handled_tasks
    assert any(
        event == "Spawn skipped: kimi concurrency cap reached"
        and kwargs.get("agent_id") == "fe-dev-1"
        and kwargs.get("task_id") == "task-9"
        and kwargs.get("provider") == "kimi"
        for event, kwargs in info_calls
    ), info_calls


@pytest.mark.asyncio
async def test_non_kimi_spawn_unaffected_by_kimi_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A kimi agent already at cap must never block an unrelated provider."""
    orch = _make_orchestrator()
    orch._instances["be-dev-1"] = _live_instance("be-dev-1", "kimi", AgentState.ACTIVE)
    monitor = {"route_calls": 0, "prepare_calls": 0}
    _wire_orch(orch, monkeypatch, monitor, "anthropic")

    launched: list[bool] = []

    async def _launch(*_a: Any, **_k: Any) -> AgentInstance:
        launched.append(True)
        return AgentInstance(agent_id="fe-dev-1", state=AgentState.ACTIVE, config=None)

    monkeypatch.setattr(orch, "_launch_spawn", _launch)

    await orch.spawn_agent(agent_id="fe-dev-1", task_id="task-9")

    assert monitor["prepare_calls"] == 1
    assert launched == [True]


@pytest.mark.asyncio
async def test_cap_honors_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """ROBOCO_KIMI_MAX_CONCURRENT populates settings.kimi_max_concurrent at
    construction; monkeypatching the singleton attribute is this suite's
    established stand-in for an env override (see test_agent_image_registry.py)."""
    monkeypatch.setattr(orch_module.settings, "kimi_max_concurrent", 2)
    orch = _make_orchestrator()
    orch._instances["be-dev-1"] = _live_instance("be-dev-1", "kimi", AgentState.ACTIVE)
    orch._instances["be-dev-2"] = _live_instance("be-dev-2", "kimi", AgentState.ACTIVE)
    monitor = {"route_calls": 0, "prepare_calls": 0}
    _wire_orch(orch, monkeypatch, monitor, "kimi")

    launched: list[bool] = []

    async def _launch(*_a: Any, **_k: Any) -> AgentInstance:
        launched.append(True)
        return AgentInstance(agent_id="fe-dev-1", state=AgentState.ACTIVE, config=None)

    monkeypatch.setattr(orch, "_launch_spawn", _launch)

    # Two live kimi agents, cap raised to 2: a third spawn is still skipped.
    result = await orch.spawn_agent(agent_id="fe-dev-1", task_id="task-9")
    assert monitor["prepare_calls"] == 0
    assert result.state is AgentState.OFFLINE
    assert launched == []

    # Drop to one live kimi agent: now under the raised cap, the spawn proceeds.
    orch._instances["be-dev-2"].state = AgentState.OFFLINE
    orch._tick_handled_tasks.clear()
    await orch.spawn_agent(agent_id="fe-dev-1", task_id="task-10")
    assert monitor["prepare_calls"] == 1
    assert launched == [True]


@pytest.mark.asyncio
async def test_spawn_proceeds_once_prior_kimi_instance_is_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = _make_orchestrator()
    orch._instances["be-dev-1"] = _live_instance("be-dev-1", "kimi", AgentState.ACTIVE)
    monitor = {"route_calls": 0, "prepare_calls": 0}
    _wire_orch(orch, monkeypatch, monitor, "kimi")

    launched: list[bool] = []

    async def _launch(*_a: Any, **_k: Any) -> AgentInstance:
        launched.append(True)
        return AgentInstance(agent_id="fe-dev-1", state=AgentState.ACTIVE, config=None)

    monkeypatch.setattr(orch, "_launch_spawn", _launch)

    # At cap: skipped.
    result = await orch.spawn_agent(agent_id="fe-dev-1", task_id="task-9")
    assert result.state is AgentState.OFFLINE
    assert monitor["prepare_calls"] == 0

    # The first kimi instance goes OFFLINE (finished/parked/reaped) — the
    # slot is free, the next spawn proceeds normally.
    orch._instances["be-dev-1"].state = AgentState.OFFLINE
    orch._tick_handled_tasks.clear()
    await orch.spawn_agent(agent_id="fe-dev-1", task_id="task-10")
    assert monitor["prepare_calls"] == 1
    assert launched == [True]
