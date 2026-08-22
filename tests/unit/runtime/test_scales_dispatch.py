"""Scales exploration dispatch — Product-Owner-solo, never the two-reviewer
board-review gate, never the dev/PM delivery dispatchers. Mirrors
test_pest_control_dispatch.py.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator
from roboco.services.task import SCALES_SOURCE


def _make_orch() -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    cast("Any", orch)._pm_respawn_tracker = {}
    cast("Any", orch)._schedule_respawn_persist = lambda *_a, **_k: None
    orch._instances = {}
    orch._board_dispatched = set()
    return orch


def _scales_task(
    *, orchestration_markers: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "status": "pending",
        "team": "board",
        "title": "Scales portfolio-rebalance cycle",
        "description": "Review the live backlog and propose a rebalance.",
        "assigned_to": "product-owner",
        "source": SCALES_SOURCE,
        "orchestration_markers": orchestration_markers,
    }


@pytest.mark.asyncio
async def test_scales_dispatch_spawns_only_product_owner() -> None:
    """A scales exploration task must spawn the Product Owner alone."""
    orch = _make_orch()
    task = _scales_task()
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_scales_exploration(task)

    spawn.assert_awaited_once()
    calls = list(spawn.await_args_list)
    assert calls[0].kwargs["agent_id"] == "product-owner"
    assert calls[0].kwargs["task_id"] == task["id"]


@pytest.mark.asyncio
async def test_scales_dispatch_retries_until_breaker() -> None:
    """A failed exploration must be retried, not abandoned.

    The explorer has a progression verb (``propose_*``), so a respawn CAN
    advance the task — unlike the two-reviewer review pass this guard was
    originally written for. Bounding belongs to
    ``_pm_respawn_should_gate`` (DB-persisted, reset by a status change),
    not to a never-expiring in-memory set.
    """
    orch = _make_orch()
    task = _scales_task()
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_scales_exploration(task)
        await orch._dispatch_scales_exploration(task)

    ticks = 2
    assert spawn.await_count == ticks, (
        "a second tick must re-attempt a failed exploration"
    )


@pytest.mark.asyncio
async def test_scales_dispatch_skips_once_authored() -> None:
    """Once ``propose_rebalance`` has stamped the rebalance_plan marker, the
    dispatcher must not spawn again."""
    orch = _make_orch()
    task = _scales_task(orchestration_markers={"rebalance_plan": {"items": []}})
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_scales_exploration(task)

    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_scales_dispatch_skips_active_po() -> None:
    orch = _make_orch()
    task = _scales_task()
    with (
        patch.object(orch, "_is_agent_active", return_value=True),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_scales_exploration(task)

    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_pm_work_routes_scales_away_from_board_handler() -> None:
    """A board_scales task must ride the dedicated dispatcher, never the
    two-reviewer ``_handle_board_assigned_task``."""
    task = _scales_task()
    stub = MagicMock()
    stub._fetch_tasks = AsyncMock(return_value=[task])
    stub._is_task_handled_this_tick = MagicMock(return_value=False)
    stub._is_paused = AsyncMock(return_value=False)
    stub._resolve_agent_slug = MagicMock(return_value="product-owner")
    stub._BOARD_AGENTS = frozenset({"product-owner", "head-marketing"})
    stub._dispatch_scales_exploration = AsyncMock()
    stub._handle_board_assigned_task = AsyncMock()
    stub._handle_pm_assigned_task = AsyncMock()
    stub._route_unassigned_pm_task = AsyncMock()

    client: Any = MagicMock()
    await AgentOrchestrator._dispatch_pm_work(cast("AgentOrchestrator", stub), client)

    stub._dispatch_scales_exploration.assert_awaited_once()
    stub._handle_board_assigned_task.assert_not_awaited()
    stub._handle_pm_assigned_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_scales_tasks_are_never_routed_by_dev_dispatch() -> None:
    tasks = [_scales_task()]
    stub = MagicMock()
    stub._fetch_tasks = AsyncMock(return_value=tasks)
    stub._is_task_handled_this_tick = MagicMock(return_value=False)
    stub._is_paused = AsyncMock(return_value=False)
    stub._dev_dispatch_one = AsyncMock()

    client: Any = MagicMock()
    await AgentOrchestrator._dispatch_dev_work(cast("AgentOrchestrator", stub), client)

    stub._dev_dispatch_one.assert_not_awaited()


def test_scales_prompt_names_solo_po_and_real_verbs() -> None:
    orch = _make_orch()
    prompt = orch._build_scales_prompt(_scales_task())
    assert "triage()" in prompt
    assert "propose_rebalance(" in prompt
    assert "i_am_idle()" in prompt
    assert "do not" in prompt.lower()


def test_scales_prompt_omits_optional_sections_when_empty() -> None:
    orch = _make_orch()
    prompt = orch._build_scales_prompt(_scales_task())
    assert "## Prior cycles" not in prompt
    assert "## Evidence gathered for you" not in prompt


def test_scales_prompt_renders_prior_cycles_and_evidence_when_given() -> None:
    orch = _make_orch()
    evidence = (
        "Stale backlog (BACKLOG/PENDING, >= 30d old...):\n"
        "- abc12345 'Old task' — P2, 45d in the backlog"
    )
    prompt = orch._build_scales_prompt(
        _scales_task(), "proposed 2, approved 1", evidence
    )
    assert "## Prior cycles" in prompt
    assert "proposed 2, approved 1" in prompt
    assert "## Evidence gathered for you" in prompt
    assert "abc12345 'Old task'" in prompt


@pytest.mark.asyncio
async def test_scales_dispatch_injects_prior_context_and_evidence() -> None:
    orch = _make_orch()
    task = _scales_task()
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(
            orch,
            "_board_program_prior_context",
            AsyncMock(return_value="proposed 2, approved 1"),
        ),
        patch.object(
            orch,
            "_scales_evidence_context",
            AsyncMock(return_value="- abc12345 'Old task' — P2, 45d in the backlog"),
        ),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_scales_exploration(task)

    prompt = spawn.await_args_list[0].kwargs["initial_prompt"]
    assert "proposed 2, approved 1" in prompt
    assert "abc12345 'Old task'" in prompt


@pytest.mark.asyncio
async def test_scales_evidence_context_survives_db_failure() -> None:
    """A DB hiccup gathering evidence degrades to '' — never raises."""
    orch = _make_orch()
    with patch(
        "roboco.services.scales_engine.get_scales_engine",
        side_effect=RuntimeError("db down"),
    ):
        result = await orch._scales_evidence_context()
    assert result == ""
