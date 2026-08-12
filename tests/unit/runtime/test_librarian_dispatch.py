"""Librarian exploration dispatch — Auditor-solo, never the two-reviewer
board-review gate (the Auditor is not in _BOARD_AGENTS), never the dev/PM
delivery dispatchers. Mirrors test_sentinel_dispatch.py (both are
complete-at-propose).
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator
from roboco.services.task import LIBRARIAN_SOURCE


def _make_orch() -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    cast("Any", orch)._pm_respawn_tracker = {}
    cast("Any", orch)._schedule_respawn_persist = lambda *_a, **_k: None
    orch._instances = {}
    orch._board_dispatched = set()
    return orch


def _librarian_task(
    *, orchestration_markers: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "status": "pending",
        "team": "board",
        "title": "Librarian playbook-mining cycle",
        "description": "Mine journals/learnings and draft 1-3 playbooks.",
        "assigned_to": "auditor",
        "source": LIBRARIAN_SOURCE,
        "orchestration_markers": orchestration_markers,
        "project_slug": None,
    }


@pytest.mark.asyncio
async def test_librarian_dispatch_spawns_only_auditor() -> None:
    """A librarian mining task must spawn the Auditor alone."""
    orch = _make_orch()
    task = _librarian_task()
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_librarian_exploration(task)

    spawn.assert_awaited_once()
    calls = list(spawn.await_args_list)
    assert calls[0].kwargs["agent_id"] == "auditor"
    assert calls[0].kwargs["task_id"] == task["id"]


@pytest.mark.asyncio
async def test_librarian_dispatch_retries_until_breaker() -> None:
    """A failed exploration must be retried, not abandoned.

    The explorer has a progression verb (``propose_*``), so a respawn CAN
    advance the task — unlike the two-reviewer review pass this guard was
    originally written for. Bounding belongs to
    ``_pm_respawn_should_gate`` (DB-persisted, reset by a status change),
    not to a never-expiring in-memory set.
    """
    orch = _make_orch()
    task = _librarian_task()
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_librarian_exploration(task)
        await orch._dispatch_librarian_exploration(task)

    ticks = 2
    assert spawn.await_count == ticks, (
        "a second tick must re-attempt a failed exploration"
    )


@pytest.mark.asyncio
async def test_librarian_dispatch_skips_active_auditor() -> None:
    orch = _make_orch()
    task = _librarian_task()
    with (
        patch.object(orch, "_is_agent_active", return_value=True),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_librarian_exploration(task)

    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_pm_work_routes_librarian_source_away_from_board() -> None:
    """A board_librarian task must ride the dedicated librarian dispatcher,
    never the two-reviewer ``_handle_board_assigned_task``, nor the roadmap/
    pest-control/periscope/sentinel dispatchers, nor plain PM handling."""
    task = _librarian_task()
    stub = MagicMock()
    stub._fetch_tasks = AsyncMock(return_value=[task])
    stub._is_task_handled_this_tick = MagicMock(return_value=False)
    stub._is_paused = AsyncMock(return_value=False)
    stub._resolve_agent_slug = MagicMock(return_value="auditor")
    stub._BOARD_AGENTS = frozenset({"product-owner", "head-marketing"})
    stub._dispatch_roadmap_exploration = AsyncMock()
    stub._dispatch_feature_spotlight_exploration = AsyncMock()
    stub._dispatch_pest_control_exploration = AsyncMock()
    stub._dispatch_periscope_exploration = AsyncMock()
    stub._dispatch_sentinel_exploration = AsyncMock()
    stub._dispatch_spackle_exploration = AsyncMock()
    stub._dispatch_scales_exploration = AsyncMock()
    stub._dispatch_librarian_exploration = AsyncMock()
    stub._handle_board_assigned_task = AsyncMock()
    stub._handle_pm_assigned_task = AsyncMock()
    stub._route_unassigned_pm_task = AsyncMock()

    client: Any = MagicMock()
    await AgentOrchestrator._dispatch_pm_work(cast("AgentOrchestrator", stub), client)

    stub._dispatch_librarian_exploration.assert_awaited_once()
    stub._dispatch_roadmap_exploration.assert_not_awaited()
    stub._dispatch_feature_spotlight_exploration.assert_not_awaited()
    stub._dispatch_pest_control_exploration.assert_not_awaited()
    stub._dispatch_periscope_exploration.assert_not_awaited()
    stub._dispatch_sentinel_exploration.assert_not_awaited()
    stub._dispatch_spackle_exploration.assert_not_awaited()
    stub._dispatch_scales_exploration.assert_not_awaited()
    stub._handle_board_assigned_task.assert_not_awaited()
    stub._handle_pm_assigned_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_librarian_tasks_are_never_routed_by_dev_dispatch() -> None:
    tasks = [_librarian_task()]
    stub = MagicMock()
    stub._fetch_tasks = AsyncMock(return_value=tasks)
    stub._is_task_handled_this_tick = MagicMock(return_value=False)
    stub._dev_dispatch_one = AsyncMock()

    client: Any = MagicMock()
    await AgentOrchestrator._dispatch_dev_work(cast("AgentOrchestrator", stub), client)

    stub._dev_dispatch_one.assert_not_awaited()


def test_librarian_prompt_names_real_verbs() -> None:
    """The prompt must steer the Auditor to its real verbs (triage /
    propose_playbook_drafts / i_am_idle), never draft_playbook."""
    orch = _make_orch()
    prompt = orch._build_librarian_prompt(_librarian_task())
    assert "triage()" in prompt
    assert "propose_playbook_drafts(" in prompt
    assert "i_am_idle()" in prompt


def test_librarian_prompt_omits_prior_cycles_section_when_empty() -> None:
    orch = _make_orch()
    prompt = orch._build_librarian_prompt(_librarian_task())
    assert "## Prior cycles" not in prompt


def test_librarian_prompt_renders_prior_cycles_when_given() -> None:
    orch = _make_orch()
    prompt = orch._build_librarian_prompt(_librarian_task(), "proposed 1, approved 0")
    assert "## Prior cycles" in prompt
    assert "proposed 1, approved 0" in prompt


def test_librarian_prompt_omits_mining_section_when_empty() -> None:
    orch = _make_orch()
    prompt = orch._build_librarian_prompt(_librarian_task())
    assert "## Mining context gathered for you" not in prompt


def test_librarian_prompt_renders_mining_context_when_given() -> None:
    orch = _make_orch()
    prompt = orch._build_librarian_prompt(
        _librarian_task(), "", "Recurring learning topics:\n- 'venv rot' recurred 3x"
    )
    assert "## Mining context gathered for you" in prompt
    assert "venv rot" in prompt


@pytest.mark.asyncio
async def test_librarian_dispatch_injects_prior_context_and_mining_into_prompt() -> (
    None
):
    """The dispatcher fetches LEARN context AND mining context (both
    best-effort) and threads them into the prompt builder — proving the
    wiring, not just the builder in isolation."""
    orch = _make_orch()
    task = _librarian_task()
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(
            orch,
            "_board_program_prior_context",
            AsyncMock(return_value="proposed 1, approved 1"),
        ),
        patch.object(
            orch,
            "_librarian_mining_context",
            AsyncMock(
                return_value="Recurring learning topics:\n- 'venv rot' recurred 2x"
            ),
        ),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_librarian_exploration(task)

    prompt = spawn.await_args_list[0].kwargs["initial_prompt"]
    assert "proposed 1, approved 1" in prompt
    assert "venv rot" in prompt
