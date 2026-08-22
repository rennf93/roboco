"""Periscope exploration dispatch — Head-of-Marketing-solo, never the
two-reviewer board-review gate, never the dev/PM delivery dispatchers.
Mirrors test_feature_spotlight_dispatch.py (both are complete-at-propose).
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator
from roboco.services.task import PERISCOPE_SOURCE


def _make_orch() -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    cast("Any", orch)._pm_respawn_tracker = {}
    cast("Any", orch)._schedule_respawn_persist = lambda *_a, **_k: None
    orch._instances = {}
    orch._board_dispatched = set()
    return orch


def _periscope_task(
    *, orchestration_markers: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "status": "pending",
        "team": "board",
        "title": "Periscope market-research cycle",
        "description": "Research the market and file ONE brief.",
        "assigned_to": "head-marketing",
        "source": PERISCOPE_SOURCE,
        "orchestration_markers": orchestration_markers,
        "project_slug": None,
    }


@pytest.mark.asyncio
async def test_periscope_dispatch_spawns_only_head_marketing() -> None:
    """A periscope exploration task must spawn the Head of Marketing alone —
    the Product Owner is not part of this cycle."""
    orch = _make_orch()
    task = _periscope_task()
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_periscope_exploration(task)

    spawn.assert_awaited_once()
    calls = list(spawn.await_args_list)
    assert calls[0].kwargs["agent_id"] == "head-marketing"
    assert calls[0].kwargs["task_id"] == task["id"]


@pytest.mark.asyncio
async def test_periscope_dispatch_retries_until_breaker() -> None:
    """A failed exploration must be retried, not abandoned.

    The explorer has a progression verb (``propose_*``), so a respawn CAN
    advance the task — unlike the two-reviewer review pass this guard was
    originally written for. Bounding belongs to
    ``_pm_respawn_should_gate`` (DB-persisted, reset by a status change),
    not to a never-expiring in-memory set.
    """
    orch = _make_orch()
    task = _periscope_task()
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_periscope_exploration(task)
        await orch._dispatch_periscope_exploration(task)

    ticks = 2
    assert spawn.await_count == ticks, (
        "a second tick must re-attempt a failed exploration"
    )


@pytest.mark.asyncio
async def test_periscope_dispatch_skips_active_hom() -> None:
    orch = _make_orch()
    task = _periscope_task()
    with (
        patch.object(orch, "_is_agent_active", return_value=True),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_periscope_exploration(task)

    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_pm_work_routes_periscope_source_away_from_board() -> None:
    """A board_periscope task must ride the dedicated periscope dispatcher,
    never the two-reviewer ``_handle_board_assigned_task``, nor the roadmap
    or feature-spotlight dispatchers, nor plain PM handling."""
    task = _periscope_task()
    stub = MagicMock()
    stub._fetch_tasks = AsyncMock(return_value=[task])
    stub._is_task_handled_this_tick = MagicMock(return_value=False)
    stub._is_paused = AsyncMock(return_value=False)
    stub._resolve_agent_slug = MagicMock(return_value="head-marketing")
    stub._BOARD_AGENTS = frozenset({"product-owner", "head-marketing"})
    stub._dispatch_roadmap_exploration = AsyncMock()
    stub._dispatch_feature_spotlight_exploration = AsyncMock()
    stub._dispatch_pest_control_exploration = AsyncMock()
    stub._dispatch_periscope_exploration = AsyncMock()
    stub._handle_board_assigned_task = AsyncMock()
    stub._handle_pm_assigned_task = AsyncMock()
    stub._route_unassigned_pm_task = AsyncMock()

    client: Any = MagicMock()
    await AgentOrchestrator._dispatch_pm_work(cast("AgentOrchestrator", stub), client)

    stub._dispatch_periscope_exploration.assert_awaited_once()
    stub._dispatch_roadmap_exploration.assert_not_awaited()
    stub._dispatch_feature_spotlight_exploration.assert_not_awaited()
    stub._dispatch_pest_control_exploration.assert_not_awaited()
    stub._handle_board_assigned_task.assert_not_awaited()
    stub._handle_pm_assigned_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_periscope_tasks_are_never_routed_by_dev_dispatch() -> None:
    tasks = [_periscope_task()]
    stub = MagicMock()
    stub._fetch_tasks = AsyncMock(return_value=tasks)
    stub._is_task_handled_this_tick = MagicMock(return_value=False)
    stub._dev_dispatch_one = AsyncMock()

    client: Any = MagicMock()
    await AgentOrchestrator._dispatch_dev_work(cast("AgentOrchestrator", stub), client)

    stub._dev_dispatch_one.assert_not_awaited()


def test_periscope_prompt_names_real_verbs() -> None:
    """The prompt must steer HoM to its real verbs (triage /
    propose_market_brief / i_am_idle)."""
    orch = _make_orch()
    prompt = orch._build_periscope_prompt(_periscope_task())
    assert "triage()" in prompt
    assert "propose_market_brief(" in prompt
    assert "i_am_idle()" in prompt


def test_periscope_prompt_names_uncited_finding_rejection() -> None:
    orch = _make_orch()
    prompt = orch._build_periscope_prompt(_periscope_task())
    assert "source_url" in prompt
    assert "rejects" in prompt.lower()


def test_periscope_prompt_omits_prior_cycles_section_when_empty() -> None:
    orch = _make_orch()
    prompt = orch._build_periscope_prompt(_periscope_task())
    assert "## Prior cycles" not in prompt


def test_periscope_prompt_renders_prior_cycles_when_given() -> None:
    orch = _make_orch()
    prompt = orch._build_periscope_prompt(_periscope_task(), "proposed 1, approved 0")
    assert "## Prior cycles" in prompt
    assert "proposed 1, approved 0" in prompt


@pytest.mark.asyncio
async def test_periscope_dispatch_injects_prior_context_into_prompt() -> None:
    """The dispatcher fetches LEARN context (best-effort) and threads it into
    the prompt builder — proving the wiring, not just the builder in
    isolation."""
    orch = _make_orch()
    task = _periscope_task()
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(
            orch,
            "_board_program_prior_context",
            AsyncMock(return_value="proposed 1, approved 1"),
        ),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_periscope_exploration(task)

    prompt = spawn.await_args_list[0].kwargs["initial_prompt"]
    assert "proposed 1, approved 1" in prompt


def test_roadmap_prompt_injects_latest_market_brief_when_given() -> None:
    """The spec's cross-role feed: Periscope's latest brief reaches the
    roadmap exploration prompt, clearly labeled as HoM market input."""
    orch = _make_orch()
    task = {"id": str(uuid4())}
    prompt = orch._build_roadmap_prompt(
        task, "", "Headline: A rival shipped agentic PR review\n- claim (source: url)"
    )
    assert "Head of Marketing" in prompt
    assert "market brief" in prompt.lower()
    assert "A rival shipped agentic PR review" in prompt


def test_roadmap_prompt_omits_market_brief_section_when_empty() -> None:
    orch = _make_orch()
    task = {"id": str(uuid4())}
    prompt = orch._build_roadmap_prompt(task)
    assert "Periscope" not in prompt


@pytest.mark.asyncio
async def test_roadmap_dispatch_injects_latest_market_brief_into_prompt() -> None:
    """The roadmap dispatcher fetches Periscope's latest brief (best-effort)
    and threads it into the prompt builder — proving the wiring end to end."""
    orch = _make_orch()
    task = {
        "id": str(uuid4()),
        "orchestration_markers": None,
        "assigned_to": "product-owner",
    }
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(orch, "_board_program_prior_context", AsyncMock(return_value="")),
        patch.object(
            orch,
            "_periscope_brief_context",
            AsyncMock(return_value="Headline: competitor signal"),
        ),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_roadmap_exploration(task)

    prompt = spawn.await_args_list[0].kwargs["initial_prompt"]
    assert "Headline: competitor signal" in prompt
