"""Barfly exploration dispatch — Head-of-Marketing-solo, never the
two-reviewer board-review gate, never the dev/PM delivery dispatchers.
Mirrors test_periscope_dispatch.py (both are complete-at-propose)."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator
from roboco.services.task import BARFLY_SOURCE


def _make_orch() -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    cast("Any", orch)._pm_respawn_tracker = {}
    cast("Any", orch)._schedule_respawn_persist = lambda *_a, **_k: None
    orch._instances = {}
    orch._board_dispatched = set()
    return orch


def _barfly_task(
    *, orchestration_markers: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "status": "pending",
        "team": "board",
        "title": "Barfly conversation-reply cycle",
        "description": "Review the screened candidates and draft replies.",
        "assigned_to": "head-marketing",
        "source": BARFLY_SOURCE,
        "orchestration_markers": orchestration_markers,
        "project_slug": None,
    }


@pytest.mark.asyncio
async def test_barfly_dispatch_spawns_only_head_marketing() -> None:
    """A barfly exploration task must spawn the Head of Marketing alone —
    the Product Owner is not part of this cycle."""
    orch = _make_orch()
    task = _barfly_task()
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_barfly_exploration(task)

    spawn.assert_awaited_once()
    calls = list(spawn.await_args_list)
    assert calls[0].kwargs["agent_id"] == "head-marketing"
    assert calls[0].kwargs["task_id"] == task["id"]


@pytest.mark.asyncio
async def test_barfly_dispatch_retries_until_breaker() -> None:
    """A failed exploration must be retried, not abandoned.

    The explorer has a progression verb (``propose_*``), so a respawn CAN
    advance the task — unlike the two-reviewer review pass this guard was
    originally written for. Bounding belongs to
    ``_pm_respawn_should_gate`` (DB-persisted, reset by a status change),
    not to a never-expiring in-memory set.
    """
    orch = _make_orch()
    task = _barfly_task()
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_barfly_exploration(task)
        await orch._dispatch_barfly_exploration(task)

    ticks = 2
    assert spawn.await_count == ticks, (
        "a second tick must re-attempt a failed exploration"
    )


@pytest.mark.asyncio
async def test_barfly_dispatch_skips_active_hom() -> None:
    orch = _make_orch()
    task = _barfly_task()
    with (
        patch.object(orch, "_is_agent_active", return_value=True),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_barfly_exploration(task)

    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_pm_work_routes_barfly_source_away_from_board() -> None:
    """A board_barfly task must ride the dedicated barfly dispatcher, never
    the two-reviewer ``_handle_board_assigned_task``, nor the periscope or
    feature-spotlight dispatchers, nor plain PM handling."""
    task = _barfly_task()
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
    stub._dispatch_barfly_exploration = AsyncMock()
    stub._handle_board_assigned_task = AsyncMock()
    stub._handle_pm_assigned_task = AsyncMock()
    stub._route_unassigned_pm_task = AsyncMock()

    client: Any = MagicMock()
    await AgentOrchestrator._dispatch_pm_work(cast("AgentOrchestrator", stub), client)

    stub._dispatch_barfly_exploration.assert_awaited_once()
    stub._dispatch_roadmap_exploration.assert_not_awaited()
    stub._dispatch_feature_spotlight_exploration.assert_not_awaited()
    stub._dispatch_periscope_exploration.assert_not_awaited()
    stub._handle_board_assigned_task.assert_not_awaited()
    stub._handle_pm_assigned_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_barfly_tasks_are_never_routed_by_dev_dispatch() -> None:
    tasks = [_barfly_task()]
    stub = MagicMock()
    stub._fetch_tasks = AsyncMock(return_value=tasks)
    stub._is_task_handled_this_tick = MagicMock(return_value=False)
    stub._dev_dispatch_one = AsyncMock()

    client: Any = MagicMock()
    await AgentOrchestrator._dispatch_dev_work(cast("AgentOrchestrator", stub), client)

    stub._dev_dispatch_one.assert_not_awaited()


def test_barfly_prompt_names_real_verbs() -> None:
    """The prompt must steer HoM to its real verbs (triage /
    propose_conversation_replies / i_am_idle)."""
    orch = _make_orch()
    prompt = orch._build_barfly_prompt(_barfly_task())
    assert "triage()" in prompt
    assert "propose_conversation_replies(" in prompt
    assert "i_am_idle()" in prompt


def test_barfly_prompt_names_never_invent_a_tweet() -> None:
    orch = _make_orch()
    prompt = orch._build_barfly_prompt(_barfly_task())
    assert "never invent a tweet" in prompt.lower()


def test_barfly_prompt_names_no_links_in_reply_body() -> None:
    """The platform appends the conversation's own URL server-side
    (materialize_barfly_reply); the prompt must tell the drafting agent not
    to add a second one of its own."""
    orch = _make_orch()
    prompt = orch._build_barfly_prompt(_barfly_task())
    assert "appends the conversation's own URL" in prompt
    assert "reply_body itself must contain NO links" in prompt


def test_barfly_prompt_renders_candidates() -> None:
    orch = _make_orch()
    task = _barfly_task(
        orchestration_markers={
            "barfly_candidates": [
                {
                    "id": "111",
                    "author_handle": "someone",
                    "text": "we should build a multi-agent org",
                    "engagement_note": "3 combined likes/replies/retweets",
                }
            ]
        }
    )
    prompt = orch._build_barfly_prompt(task)
    assert "id=111" in prompt
    assert "we should build a multi-agent org" in prompt


def test_barfly_prompt_renders_none_when_no_candidates() -> None:
    orch = _make_orch()
    prompt = orch._build_barfly_prompt(_barfly_task())
    assert "(none)" in prompt


def test_barfly_prompt_omits_prior_cycles_section_when_empty() -> None:
    orch = _make_orch()
    prompt = orch._build_barfly_prompt(_barfly_task())
    assert "## Prior cycles" not in prompt


def test_barfly_prompt_renders_prior_cycles_when_given() -> None:
    orch = _make_orch()
    prompt = orch._build_barfly_prompt(_barfly_task(), "proposed 2, approved 1")
    assert "## Prior cycles" in prompt
    assert "proposed 2, approved 1" in prompt


@pytest.mark.asyncio
async def test_barfly_dispatch_injects_prior_context_into_prompt() -> None:
    """The dispatcher fetches LEARN context (best-effort) and threads it into
    the prompt builder — proving the wiring, not just the builder in
    isolation."""
    orch = _make_orch()
    task = _barfly_task()
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
        await orch._dispatch_barfly_exploration(task)

    prompt = spawn.await_args_list[0].kwargs["initial_prompt"]
    assert "proposed 1, approved 1" in prompt
