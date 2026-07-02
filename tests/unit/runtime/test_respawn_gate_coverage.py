"""The respawn circuit breaker guards EVERY task-keyed spawn path.

Live break (2026-07-02, b8fe0494): fe-doc respawned 26 times in ~100 min
(~$7.20) on an awaiting_documentation task with no valid verb — because
``_pm_respawn_should_gate`` (progress-aware strikes, DB-durable, one-shot CEO
notification) was consulted by only 3 of the ~10 task-keyed dispatch paths.
The doc/QA/PR-gate/dev paths spawned unguarded at fixed cadence.

These tests pin the gate consultation on the previously-unguarded helpers:
gate says skip → no spawn; gate says go → spawn proceeds.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator


def _orch(gate_result: bool) -> AgentOrchestrator:
    """Orchestrator via __new__ with the gate + spawn stubbed."""
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    o = cast("Any", orch)
    o._pm_respawn_should_gate = AsyncMock(return_value=gate_result)
    o.spawn_agent = AsyncMock()
    o._resolve_agent_slug = lambda x: x
    o._is_agent_active = lambda _slug: False
    o._task_git_context = lambda _t: None
    o._build_doc_prompt = lambda _t: "doc prompt"
    o._build_qa_prompt = lambda _t: "qa prompt"
    o._build_pr_gate_prompt = lambda _t: "gate prompt"
    o._select_agent_for_cell = lambda _team, _role: "fe-pr-reviewer"
    o._is_task_handled_this_tick = lambda _tid: False
    return orch


def _task(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": str(uuid4()),
        "status": "awaiting_documentation",
        "team": "frontend",
        "assigned_to": "fe-doc",
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_doc_respawn_consults_gate_and_skips_when_tripped() -> None:
    # The exact fe-doc loop path: assigned documenter, inactive, respawned
    # every tick. With the gate tripped, the spawn must be skipped.
    orch = _orch(gate_result=True)
    handled = await orch._respawn_doc_if_assigned(_task())
    assert handled is True  # task stays handled (no auto-assign fallthrough)
    cast("Any", orch).spawn_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_doc_respawn_spawns_when_gate_clear() -> None:
    orch = _orch(gate_result=False)
    handled = await orch._respawn_doc_if_assigned(_task())
    assert handled is True
    cast("Any", orch).spawn_agent.assert_awaited_once()


@pytest.mark.asyncio
async def test_assigned_qa_consults_gate_and_skips_when_tripped() -> None:
    orch = _orch(gate_result=True)
    handled = await orch._spawn_assigned_qa(
        _task(status="awaiting_qa"), assigned_to="fe-qa"
    )
    assert handled is True
    cast("Any", orch).spawn_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_assigned_qa_spawns_when_gate_clear() -> None:
    orch = _orch(gate_result=False)
    handled = await orch._spawn_assigned_qa(
        _task(status="awaiting_qa"), assigned_to="fe-qa"
    )
    assert handled is True
    cast("Any", orch).spawn_agent.assert_awaited_once()


@pytest.mark.asyncio
async def test_pr_gate_dispatch_consults_gate_and_skips_when_tripped() -> None:
    orch = _orch(gate_result=True)
    o = cast("Any", orch)
    o._fetch_tasks = AsyncMock(
        return_value=[_task(status="awaiting_pr_review", team="frontend")]
    )
    await orch._dispatch_pr_gate_work(MagicMock())
    o.spawn_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_pr_gate_dispatch_spawns_when_gate_clear() -> None:
    orch = _orch(gate_result=False)
    o = cast("Any", orch)
    o._fetch_tasks = AsyncMock(
        return_value=[_task(status="awaiting_pr_review", team="frontend")]
    )
    await orch._dispatch_pr_gate_work(MagicMock())
    o.spawn_agent.assert_awaited_once()
