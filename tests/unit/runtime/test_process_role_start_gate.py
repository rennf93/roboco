"""ROBOCO_ROLE process split: the single gate in ``AgentOrchestrator.start()``.

Invariant: role='api' constructs the orchestrator (routes still use it for
on-demand spawns/live chats) but must not run the once-per-fleet startup
reconciliation or schedule a single background loop, those belong to the
'dispatcher' role so they run exactly once across the split fleet, not once
per process. 'dispatcher' and 'all' fall through to today's unchanged start()
body. See roboco/config.py's ``role`` field and roboco/bootstrap.py's
ROBOCO_ROLE=indexer branch (covered separately in tests/unit/test_bootstrap.py).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.config import settings
from roboco.runtime.orchestrator import AgentOrchestrator

# (task attr, coroutine-method name) for every loop start() may schedule.
# Mirrors the tuple stop() cancels, sourced from the real production
# initializer so a newly added engine loop can never drift this fixture
# stale. The method is stubbed to a no-op so this stays a unit test: the real
# bodies do live DB/docker/redis I/O with nothing behind them here.
_LOOPS = (
    ("_health_task", "_health_loop"),
    ("_dispatcher_task", "_dispatcher_loop"),
    ("_sweeper_task", "_sweeper_loop"),
    ("_rate_limit_probe_task", "_rate_limit_probe_loop"),
    ("_strategy_engine_task", "_strategy_engine_loop"),
    ("_external_pr_poll_task", "_external_pr_poll_loop"),
    ("_self_heal_task", "_self_heal_loop"),
    ("_ci_watch_task", "_ci_watch_loop"),
    ("_dep_update_task", "_dep_update_loop"),
    ("_env_sync_task", "_env_sync_loop"),
    ("_release_manager_task", "_release_manager_loop"),
    ("_x_mentions_task", "_x_mentions_poll_loop"),
    ("_board_program_task", "_board_program_loop"),
    ("_video_render_task", "_video_render_loop"),
    ("_vault_intake_task", "_vault_intake_loop"),
    ("_vault_janitor_task", "_vault_janitor_loop"),
    ("_vault_kb_task", "_vault_kb_loop"),
    ("_telegram_poll_task", "_telegram_poll_loop"),
)

# Every reconciliation step start() awaits before launching the loops, plus
# the heartbeat call, all best-effort DB/docker/redis I/O in production,
# stubbed here so this stays a unit test.
_RECONCILE_METHODS = (
    "_mark_running_and_beat",
    "_ensure_agent_image",
    "restore_waiting_records",
    "restore_respawn_tracker",
    "_reconcile_orphan_claims_on_startup",
    "_heal_stale_agent_tokens",
    "_readopt_running_agents",
    "_reconcile_orphan_spawn_sessions",
    "_sandbox_janitor_sweep",
)


def _make_orchestrator() -> AgentOrchestrator:
    """AgentOrchestrator with constructor I/O skipped; start() deps stubbed."""
    with patch.object(AgentOrchestrator, "__init__", return_value=None):
        orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._running = False
    orch.dispatcher_interval = 30
    orch._health_task = None
    orch._dispatcher_task = None
    orch._sweeper_task = None
    orch._init_engine_loop_task_slots()
    orch._last_dispatch_heartbeat = None
    for name in _RECONCILE_METHODS:
        setattr(orch, name, AsyncMock())
    # Any-typed view: a plain assignment here is a bound-method override
    # mypy's method-assign check rejects outright.
    orch_any: Any = orch
    orch_any._ensure_vault_assets_on_startup = MagicMock()
    # Every loop body replaced with a no-op coroutine: start() only needs to
    # prove it SCHEDULES the task; running the real bodies (DB/docker polling)
    # has nothing behind it in a unit test and would just hang.
    for _task_attr, loop_method in _LOOPS:
        setattr(orch, loop_method, AsyncMock(return_value=None))
    return orch


@pytest.mark.asyncio
async def test_api_role_skips_reconciliation_and_every_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """role='api': the orchestrator is attached (self._running flips true) but
    not one reconciliation step runs and not one loop is scheduled."""
    monkeypatch.setattr(settings, "role", "api")
    orch = _make_orchestrator()

    await orch.start()

    assert orch._running is True
    for name in _RECONCILE_METHODS:
        getattr(orch, name).assert_not_awaited()
    for task_attr, _loop_method in _LOOPS:
        assert getattr(orch, task_attr) is None, (
            f"{task_attr} must stay unscheduled in api role"
        )


@pytest.mark.asyncio
async def test_dispatcher_role_runs_reconciliation_and_every_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """role='dispatcher': every reconciliation step runs and every loop is
    scheduled, this is the ONE process now responsible for the fleet."""
    monkeypatch.setattr(settings, "role", "dispatcher")
    with patch(
        "roboco.services.release_proposal.sweep_orphan_release_locks",
        AsyncMock(),
    ):
        orch = _make_orchestrator()
        await orch.start()

    assert orch._running is True
    for name in _RECONCILE_METHODS:
        getattr(orch, name).assert_awaited_once()
    tasks = [getattr(orch, task_attr) for task_attr, _ in _LOOPS]
    for task_attr, task in zip((t for t, _ in _LOOPS), tasks, strict=True):
        assert task is not None, f"{task_attr} must be scheduled"
    await asyncio.gather(*tasks)
    for _task_attr, loop_method in _LOOPS:
        getattr(orch, loop_method).assert_awaited_once()


@pytest.mark.asyncio
async def test_all_role_matches_dispatcher_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """role='all' (the default) is byte-for-byte today's single-process
    behavior: same reconciliation + loop launch as 'dispatcher'."""
    monkeypatch.setattr(settings, "role", "all")
    with patch(
        "roboco.services.release_proposal.sweep_orphan_release_locks",
        AsyncMock(),
    ):
        orch = _make_orchestrator()
        await orch.start()

    assert orch._running is True
    for name in _RECONCILE_METHODS:
        getattr(orch, name).assert_awaited_once()
    tasks = [getattr(orch, task_attr) for task_attr, _ in _LOOPS]
    for task_attr, task in zip((t for t, _ in _LOOPS), tasks, strict=True):
        assert task is not None, f"{task_attr} must be scheduled"
    await asyncio.gather(*tasks)
