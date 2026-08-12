"""Mirror exploration dispatch — Head-of-Marketing-solo, never the
two-reviewer board-review gate, never the dev/PM delivery dispatchers.
Mirrors test_spackle_dispatch.py.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator
from roboco.services.task import MIRROR_SOURCE


def _make_orch() -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    cast("Any", orch)._pm_respawn_tracker = {}
    cast("Any", orch)._schedule_respawn_persist = lambda *_a, **_k: None
    orch._instances = {}
    orch._board_dispatched = set()
    return orch


def _mirror_task(
    *, orchestration_markers: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "status": "pending",
        "team": "board",
        "title": "Mirror exploration cycle",
        "description": "Audit messaging surfaces and propose a messaging-fixes audit.",
        "assigned_to": "head-marketing",
        "source": MIRROR_SOURCE,
        "orchestration_markers": orchestration_markers,
    }


@pytest.mark.asyncio
async def test_mirror_dispatch_spawns_only_head_of_marketing() -> None:
    """A mirror exploration task must spawn the Head of Marketing alone."""
    orch = _make_orch()
    task = _mirror_task()
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_mirror_exploration(task)

    spawn.assert_awaited_once()
    calls = list(spawn.await_args_list)
    assert calls[0].kwargs["agent_id"] == "head-marketing"
    assert calls[0].kwargs["task_id"] == task["id"]


@pytest.mark.asyncio
async def test_mirror_dispatch_retries_until_breaker() -> None:
    """A failed exploration must be retried, not abandoned.

    The explorer has a progression verb (``propose_*``), so a respawn CAN
    advance the task — unlike the two-reviewer review pass this guard was
    originally written for. Bounding belongs to
    ``_pm_respawn_should_gate`` (DB-persisted, reset by a status change),
    not to a never-expiring in-memory set.
    """
    orch = _make_orch()
    task = _mirror_task()
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_mirror_exploration(task)
        await orch._dispatch_mirror_exploration(task)

    ticks = 2
    assert spawn.await_count == ticks, (
        "a second tick must re-attempt a failed exploration"
    )


@pytest.mark.asyncio
async def test_mirror_dispatch_skips_once_authored() -> None:
    """Once ``propose_messaging_fixes`` has stamped the messaging_fixes
    marker, the dispatcher must not spawn again."""
    orch = _make_orch()
    task = _mirror_task(orchestration_markers={"messaging_fixes": {"items": []}})
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_mirror_exploration(task)

    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_mirror_dispatch_skips_active_hom() -> None:
    orch = _make_orch()
    task = _mirror_task()
    with (
        patch.object(orch, "_is_agent_active", return_value=True),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_mirror_exploration(task)

    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_pm_work_routes_mirror_away_from_board_handler() -> None:
    """A board_mirror task must ride the dedicated dispatcher, never the
    two-reviewer ``_handle_board_assigned_task``."""
    task = _mirror_task()
    stub = MagicMock()
    stub._fetch_tasks = AsyncMock(return_value=[task])
    stub._is_task_handled_this_tick = MagicMock(return_value=False)
    stub._is_paused = AsyncMock(return_value=False)
    stub._resolve_agent_slug = MagicMock(return_value="head-marketing")
    stub._BOARD_AGENTS = frozenset({"product-owner", "head-marketing"})
    stub._dispatch_mirror_exploration = AsyncMock()
    stub._handle_board_assigned_task = AsyncMock()
    stub._handle_pm_assigned_task = AsyncMock()
    stub._route_unassigned_pm_task = AsyncMock()

    client: Any = MagicMock()
    await AgentOrchestrator._dispatch_pm_work(cast("AgentOrchestrator", stub), client)

    stub._dispatch_mirror_exploration.assert_awaited_once()
    stub._handle_board_assigned_task.assert_not_awaited()
    stub._handle_pm_assigned_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_mirror_tasks_are_never_routed_by_dev_dispatch() -> None:
    tasks = [_mirror_task()]
    stub = MagicMock()
    stub._fetch_tasks = AsyncMock(return_value=tasks)
    stub._is_task_handled_this_tick = MagicMock(return_value=False)
    stub._is_paused = AsyncMock(return_value=False)
    stub._dev_dispatch_one = AsyncMock()

    client: Any = MagicMock()
    await AgentOrchestrator._dispatch_dev_work(cast("AgentOrchestrator", stub), client)

    stub._dev_dispatch_one.assert_not_awaited()


def test_mirror_prompt_names_solo_hom_and_real_verbs() -> None:
    orch = _make_orch()
    prompt = orch._build_mirror_prompt(_mirror_task())
    assert "triage()" in prompt
    assert "propose_messaging_fixes(" in prompt
    assert "i_am_idle()" in prompt
    assert "do not" in prompt.lower()


def test_mirror_prompt_omits_optional_sections_when_empty() -> None:
    orch = _make_orch()
    prompt = orch._build_mirror_prompt(_mirror_task())
    assert "## Prior cycles" not in prompt


def test_mirror_prompt_renders_prior_cycles_when_given() -> None:
    orch = _make_orch()
    prompt = orch._build_mirror_prompt(
        _mirror_task(),
        "proposed 2, approved 1",
    )
    assert "## Prior cycles" in prompt
    assert "proposed 2, approved 1" in prompt


@pytest.mark.asyncio
async def test_mirror_dispatch_injects_prior_context() -> None:
    orch = _make_orch()
    task = _mirror_task()
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
        await orch._dispatch_mirror_exploration(task)

    prompt = spawn.await_args_list[0].kwargs["initial_prompt"]
    assert "proposed 2, approved 1" in prompt
