"""The gate dispatcher must not spawn a reviewer while CI is still running.

Live incident (2026-08): ``pr_pass`` correctly refuses while the assembled
PR's head-commit CI is pending, but nothing told the dispatcher — it kept
respawning the reviewer roughly every tick to re-ask, burning spawns until
the respawn breaker tripped and paged the CEO (two tasks, 7 rejected
``pr_pass`` calls each in ~12 minutes). ``_gate_task_ci_pending`` reuses
``GitService.get_pr_ci_status`` — the exact signal ``pr_pass``'s own guard
checks — and is cached per (project_slug, pr_number) so a busy dispatch
cadence can't turn into a GitHub call per tick per task.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator


def _orch() -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    o = cast("Any", orch)
    o.spawn_agent = AsyncMock()
    o._resolve_agent_slug = lambda x: x
    o._is_agent_active = lambda _slug: False
    o._task_git_context = lambda _t: None
    o._build_pr_gate_prompt = lambda _t: "gate prompt"
    o._select_agent_for_cell = lambda _team, _role: "fe-pr-reviewer"
    o._is_task_handled_this_tick = lambda _tid: False
    o._pm_respawn_should_gate = AsyncMock(return_value=False)
    o._gate_ci_status_cache = {}
    return orch


def _task(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": str(uuid4()),
        "status": "awaiting_pr_review",
        "team": "main_pm",
        "assigned_to": "main-pm",
        "project_slug": "roboco",
        "pr_number": 42,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# _gate_task_ci_pending: the tri-state classification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ci_pending_state_blocks() -> None:
    orch = _orch()
    o = cast("Any", orch)
    o._fetch_gate_ci_state = AsyncMock(return_value="pending")

    assert await orch._gate_task_ci_pending(_task()) is True


@pytest.mark.asyncio
async def test_ci_pending_not_scheduled_blocks() -> None:
    orch = _orch()
    o = cast("Any", orch)
    o._fetch_gate_ci_state = AsyncMock(return_value="pending_not_scheduled")

    assert await orch._gate_task_ci_pending(_task()) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["success", "failure", "no_ci_configured", "error"])
async def test_ci_non_pending_states_do_not_block(state: str) -> None:
    orch = _orch()
    o = cast("Any", orch)
    o._fetch_gate_ci_state = AsyncMock(return_value=state)

    assert await orch._gate_task_ci_pending(_task()) is False


@pytest.mark.asyncio
async def test_ci_lookup_failure_fails_open() -> None:
    """A None state (lookup error, no signal) must never strand a task —
    only an explicit pending/pending_not_scheduled state blocks."""
    orch = _orch()
    o = cast("Any", orch)
    o._fetch_gate_ci_state = AsyncMock(return_value=None)

    assert await orch._gate_task_ci_pending(_task()) is False


@pytest.mark.asyncio
async def test_ci_missing_pr_number_fails_open_without_a_lookup() -> None:
    orch = _orch()
    o = cast("Any", orch)
    o._fetch_gate_ci_state = AsyncMock(return_value="pending")

    assert await orch._gate_task_ci_pending(_task(pr_number=None)) is False
    o._fetch_gate_ci_state.assert_not_awaited()


# ---------------------------------------------------------------------------
# Caching: bounded lookups per (slug, pr_number)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ci_status_cache_bounds_repeated_lookups(monkeypatch: Any) -> None:
    orch = _orch()
    o = cast("Any", orch)
    o._fetch_gate_ci_state = AsyncMock(return_value="pending")
    clock = [1000.0]
    monkeypatch.setattr("roboco.runtime.orchestrator.time.monotonic", lambda: clock[0])

    task = _task()
    assert await orch._gate_task_ci_pending(task) is True
    clock[0] += 5.0  # well inside the TTL window
    assert await orch._gate_task_ci_pending(task) is True

    o._fetch_gate_ci_state.assert_awaited_once()


@pytest.mark.asyncio
async def test_ci_status_cache_expires_after_ttl(monkeypatch: Any) -> None:
    orch = _orch()
    o = cast("Any", orch)
    o._fetch_gate_ci_state = AsyncMock(return_value="pending")
    clock = [1000.0]
    monkeypatch.setattr("roboco.runtime.orchestrator.time.monotonic", lambda: clock[0])

    task = _task()
    await orch._gate_task_ci_pending(task)
    clock[0] += AgentOrchestrator._GATE_CI_STATUS_CACHE_TTL_SECONDS + 1.0
    await orch._gate_task_ci_pending(task)

    expected_lookups_after_ttl_expiry = 2
    assert o._fetch_gate_ci_state.await_count == expected_lookups_after_ttl_expiry


# ---------------------------------------------------------------------------
# _dispatch_pr_gate_work: pending CI must skip the spawn entirely
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_skips_spawn_when_ci_pending() -> None:
    orch = _orch()
    o = cast("Any", orch)
    o._fetch_tasks = AsyncMock(return_value=[_task()])
    o._gate_task_ci_pending = AsyncMock(return_value=True)

    await orch._dispatch_pr_gate_work(MagicMock())

    o.spawn_agent.assert_not_awaited()
    o._pm_respawn_should_gate.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_spawns_when_ci_not_pending() -> None:
    orch = _orch()
    o = cast("Any", orch)
    o._fetch_tasks = AsyncMock(return_value=[_task()])
    o._gate_task_ci_pending = AsyncMock(return_value=False)

    await orch._dispatch_pr_gate_work(MagicMock())

    o.spawn_agent.assert_awaited_once()
