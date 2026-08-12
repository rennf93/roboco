"""Board roadmap exploration dispatch — Product-Owner-solo (v1), never the
two-reviewer board-review gate, never the dev/PM delivery dispatchers.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator
from roboco.services.task import ROADMAP_SOURCE


def _make_orch() -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    cast("Any", orch)._pm_respawn_tracker = {}
    cast("Any", orch)._schedule_respawn_persist = lambda *_a, **_k: None
    orch._instances = {}
    orch._board_dispatched = set()
    return orch


def _roadmap_task(
    *, orchestration_markers: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "status": "pending",
        "team": "board",
        "title": "Roadmap exploration cycle",
        "description": "Explore and propose a themed roadmap cycle.",
        "assigned_to": "product-owner",
        "source": ROADMAP_SOURCE,
        "orchestration_markers": orchestration_markers,
    }


@pytest.mark.asyncio
async def test_roadmap_dispatch_spawns_only_product_owner() -> None:
    """A roadmap exploration task must spawn the Product Owner alone — Head of
    Marketing is out of scope for v1 (non-goal: HoM co-authoring)."""
    orch = _make_orch()
    task = _roadmap_task()
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_roadmap_exploration(task)

    spawn.assert_awaited_once()
    calls = list(spawn.await_args_list)
    assert calls[0].kwargs["agent_id"] == "product-owner"
    assert calls[0].kwargs["task_id"] == task["id"]


@pytest.mark.asyncio
async def test_roadmap_dispatch_retries_until_breaker() -> None:
    """A failed exploration must be retried, not abandoned.

    The explorer has a progression verb (``propose_*``), so a respawn CAN
    advance the task — unlike the two-reviewer review pass this guard was
    originally written for. Bounding belongs to
    ``_pm_respawn_should_gate`` (DB-persisted, reset by a status change),
    not to a never-expiring in-memory set.
    """
    orch = _make_orch()
    task = _roadmap_task()
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_roadmap_exploration(task)
        await orch._dispatch_roadmap_exploration(task)

    ticks = 2
    assert spawn.await_count == ticks, (
        "a second tick must re-attempt a failed exploration"
    )


@pytest.mark.asyncio
async def test_roadmap_dispatch_skips_once_authored() -> None:
    """Once ``propose_roadmap`` has stamped the roadmap_cycle marker, the
    dispatcher must not spawn again — the CEO roadmap queue owns the rest."""
    orch = _make_orch()
    task = _roadmap_task(
        orchestration_markers={"roadmap_cycle": {"goal": "x", "items": []}}
    )
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_roadmap_exploration(task)

    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_roadmap_dispatch_skips_active_po() -> None:
    orch = _make_orch()
    task = _roadmap_task()
    with (
        patch.object(orch, "_is_agent_active", return_value=True),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_roadmap_exploration(task)

    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_pm_work_routes_roadmap_source_away_from_board_handler() -> None:
    """A board_roadmap task must ride the dedicated roadmap dispatcher, never
    the two-reviewer ``_handle_board_assigned_task`` (which would also spawn
    Head of Marketing and fire the Approve & Start handoff — both wrong here)."""
    task = _roadmap_task()
    stub = MagicMock()
    stub._fetch_tasks = AsyncMock(return_value=[task])
    stub._is_task_handled_this_tick = MagicMock(return_value=False)
    stub._is_paused = AsyncMock(return_value=False)
    stub._resolve_agent_slug = MagicMock(return_value="product-owner")
    stub._BOARD_AGENTS = frozenset({"product-owner", "head-marketing"})
    stub._dispatch_roadmap_exploration = AsyncMock()
    stub._handle_board_assigned_task = AsyncMock()
    stub._handle_pm_assigned_task = AsyncMock()
    stub._route_unassigned_pm_task = AsyncMock()

    client: Any = MagicMock()
    await AgentOrchestrator._dispatch_pm_work(cast("AgentOrchestrator", stub), client)

    stub._dispatch_roadmap_exploration.assert_awaited_once()
    stub._handle_board_assigned_task.assert_not_awaited()
    stub._handle_pm_assigned_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_roadmap_tasks_are_never_routed_by_dev_dispatch() -> None:
    tasks = [_roadmap_task()]
    stub = MagicMock()
    stub._fetch_tasks = AsyncMock(return_value=tasks)
    stub._is_task_handled_this_tick = MagicMock(return_value=False)
    stub._dev_dispatch_one = AsyncMock()

    client: Any = MagicMock()
    await AgentOrchestrator._dispatch_dev_work(cast("AgentOrchestrator", stub), client)

    stub._dev_dispatch_one.assert_not_awaited()


def test_roadmap_prompt_names_solo_po_and_real_verbs() -> None:
    """The prompt must steer the PO to its real verbs (triage / propose_roadmap
    / i_am_idle), make the solo-authorship explicit, and away from
    claim/plan/delegate it does not have."""
    orch = _make_orch()
    prompt = orch._build_roadmap_prompt(_roadmap_task())
    assert "triage()" in prompt
    assert "propose_roadmap(" in prompt
    assert "i_am_idle()" in prompt
    assert "Head of Marketing is not" in prompt
    assert "involved in this cycle" in prompt
    assert "do not" in prompt.lower()


def test_roadmap_prompt_omits_prior_cycles_section_when_empty() -> None:
    orch = _make_orch()
    prompt = orch._build_roadmap_prompt(_roadmap_task())
    assert "## Prior cycles" not in prompt


def test_roadmap_prompt_renders_prior_cycles_when_given() -> None:
    orch = _make_orch()
    prompt = orch._build_roadmap_prompt(
        _roadmap_task(), "proposed 5, approved 3; rejected: item-2 — too risky"
    )
    assert "## Prior cycles" in prompt
    assert "proposed 5, approved 3; rejected: item-2 — too risky" in prompt


@pytest.mark.asyncio
async def test_roadmap_dispatch_injects_prior_context_into_prompt() -> None:
    """The dispatcher fetches LEARN context (best-effort) and threads it into
    the prompt builder — proving the wiring, not just the builder in
    isolation."""
    orch = _make_orch()
    task = _roadmap_task()
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(
            orch,
            "_board_program_prior_context",
            AsyncMock(return_value="proposed 2, approved 1"),
        ),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_roadmap_exploration(task)

    prompt = spawn.await_args_list[0].kwargs["initial_prompt"]
    assert "proposed 2, approved 1" in prompt


@pytest.mark.asyncio
async def test_board_program_prior_context_survives_db_failure() -> None:
    """A DB hiccup fetching prior context degrades to '' — never raises, so
    the caller (the dispatcher) never needs its own safety net around it."""
    orch = _make_orch()
    with patch(
        "roboco.services.board_programs.get_board_program_engine",
        side_effect=RuntimeError("db down"),
    ):
        result = await orch._board_program_prior_context("roadmap")
    assert result == ""
