"""War Room campaign-planning dispatch — Head-of-Marketing-solo,
EVENT-triggered, never the two-reviewer board-review gate, never the dev/PM
delivery dispatchers. Mirrors test_coroner_dispatch.py: complete-at-propose
(no "already authored" marker pre-check — propose_campaign completes the
task atomically), and no extra async context-gathering helper (the release
brief, or {} for a blank on-demand cycle, is already ON the task's own
``war_room_brief`` marker at origination time — no separate DB read needed).
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator
from roboco.services.task import WAR_ROOM_SOURCE


def _make_orch() -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    cast("Any", orch)._pm_respawn_tracker = {}
    cast("Any", orch)._schedule_respawn_persist = lambda *_a, **_k: None
    orch._instances = {}
    orch._board_dispatched = set()
    return orch


def _war_room_task(
    *, orchestration_markers: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "status": "pending",
        "team": "board",
        "title": "War Room campaign planning",
        "description": "Design one campaign and propose it.",
        "assigned_to": "head-marketing",
        "source": WAR_ROOM_SOURCE,
        "orchestration_markers": orchestration_markers
        if orchestration_markers is not None
        else {
            "war_room_brief": {
                "version": "0.30.0",
                "highlights": ["MegaTask v2", "Findings ledger"],
            }
        },
    }


@pytest.mark.asyncio
async def test_war_room_dispatch_spawns_only_hom() -> None:
    orch = _make_orch()
    task = _war_room_task()
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_war_room_exploration(task)

    spawn.assert_awaited_once()
    calls = list(spawn.await_args_list)
    assert calls[0].kwargs["agent_id"] == "head-marketing"
    assert calls[0].kwargs["task_id"] == task["id"]


@pytest.mark.asyncio
async def test_war_room_dispatch_retries_until_breaker() -> None:
    """A failed exploration must be retried, not abandoned.

    The explorer has a progression verb (``propose_*``), so a respawn CAN
    advance the task — unlike the two-reviewer review pass this guard was
    originally written for. Bounding belongs to
    ``_pm_respawn_should_gate`` (DB-persisted, reset by a status change),
    not to a never-expiring in-memory set.
    """
    orch = _make_orch()
    task = _war_room_task()
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_war_room_exploration(task)
        await orch._dispatch_war_room_exploration(task)

    ticks = 2
    assert spawn.await_count == ticks, (
        "a second tick must re-attempt a failed exploration"
    )


@pytest.mark.asyncio
async def test_war_room_dispatch_skips_active_hom() -> None:
    orch = _make_orch()
    task = _war_room_task()
    with (
        patch.object(orch, "_is_agent_active", return_value=True),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_war_room_exploration(task)

    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_pm_work_routes_war_room_away_from_board_handler() -> None:
    """A board_war_room task must ride the dedicated dispatcher, never the
    two-reviewer ``_handle_board_assigned_task`` or the generic PM-assigned
    handler."""
    task = _war_room_task()
    stub = MagicMock()
    stub._fetch_tasks = AsyncMock(return_value=[task])
    stub._is_task_handled_this_tick = MagicMock(return_value=False)
    stub._is_paused = AsyncMock(return_value=False)
    stub._resolve_agent_slug = MagicMock(return_value="head-marketing")
    stub._BOARD_AGENTS = frozenset({"product-owner", "head-marketing"})
    stub._dispatch_war_room_exploration = AsyncMock()
    stub._handle_board_assigned_task = AsyncMock()
    stub._handle_pm_assigned_task = AsyncMock()
    stub._route_unassigned_pm_task = AsyncMock()

    client: Any = MagicMock()
    await AgentOrchestrator._dispatch_pm_work(cast("AgentOrchestrator", stub), client)

    stub._dispatch_war_room_exploration.assert_awaited_once()
    stub._handle_board_assigned_task.assert_not_awaited()
    stub._handle_pm_assigned_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_war_room_tasks_are_never_routed_by_dev_dispatch() -> None:
    tasks = [_war_room_task()]
    stub = MagicMock()
    stub._fetch_tasks = AsyncMock(return_value=tasks)
    stub._is_task_handled_this_tick = MagicMock(return_value=False)
    stub._is_paused = AsyncMock(return_value=False)
    stub._dev_dispatch_one = AsyncMock()

    client: Any = MagicMock()
    await AgentOrchestrator._dispatch_dev_work(cast("AgentOrchestrator", stub), client)

    stub._dev_dispatch_one.assert_not_awaited()


def test_war_room_prompt_names_solo_hom_and_real_verbs() -> None:
    orch = _make_orch()
    task = _war_room_task()
    prompt = orch._build_war_room_prompt(task)
    assert "triage()" in prompt
    assert "propose_campaign(" in prompt
    assert "i_am_idle()" in prompt
    assert "v0.30.0" in prompt
    assert "MegaTask v2" in prompt


def test_war_room_prompt_renders_blank_brief_message_when_absent() -> None:
    orch = _make_orch()
    task = _war_room_task(orchestration_markers={"war_room_brief": {}})
    prompt = orch._build_war_room_prompt(task)
    assert "No release triggered this cycle" in prompt


def test_war_room_prompt_renders_blank_brief_message_with_no_marker() -> None:
    """A malformed/absent marker degrades to the same blank-brief branch,
    never a KeyError."""
    orch = _make_orch()
    task = _war_room_task(orchestration_markers={})
    prompt = orch._build_war_room_prompt(task)
    assert "No release triggered this cycle" in prompt
