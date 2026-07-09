"""Feature-spotlight exploration dispatch — Head-of-Marketing-solo, never the
two-reviewer board-review gate, never the dev/PM delivery dispatchers.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator
from roboco.services.task import X_FEATURE_EXPLORATION_SOURCE


def _make_orch() -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    cast("Any", orch)._pm_respawn_tracker = {}
    cast("Any", orch)._schedule_respawn_persist = lambda *_a, **_k: None
    orch._instances = {}
    orch._board_dispatched = set()
    return orch


def _feature_task(
    *, orchestration_markers: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "status": "pending",
        "team": "board",
        "title": "X feature-spotlight exploration",
        "description": "Investigate shipped capabilities and propose a spotlight.",
        "assigned_to": "head-marketing",
        "source": X_FEATURE_EXPLORATION_SOURCE,
        "orchestration_markers": orchestration_markers,
    }


@pytest.mark.asyncio
async def test_feature_spotlight_dispatch_spawns_only_head_marketing() -> None:
    """A feature-spotlight exploration task must spawn the Head of Marketing
    alone — the Product Owner is not part of this cycle."""
    orch = _make_orch()
    task = _feature_task()
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_feature_spotlight_exploration(task)

    spawn.assert_awaited_once()
    calls = list(spawn.await_args_list)
    assert calls[0].kwargs["agent_id"] == "head-marketing"
    assert calls[0].kwargs["task_id"] == task["id"]


@pytest.mark.asyncio
async def test_feature_spotlight_dispatch_is_one_shot() -> None:
    """Re-ticking a still-pending exploration must NOT respawn — board roles
    have no progression verb, so a respawn would just loop."""
    orch = _make_orch()
    task = _feature_task()
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_feature_spotlight_exploration(task)
        await orch._dispatch_feature_spotlight_exploration(task)

    spawn.assert_awaited_once()


@pytest.mark.asyncio
async def test_feature_spotlight_dispatch_skips_active_hom() -> None:
    orch = _make_orch()
    task = _feature_task()
    with (
        patch.object(orch, "_is_agent_active", return_value=True),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_feature_spotlight_exploration(task)

    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_pm_work_routes_feature_source_away_from_board() -> None:
    """An x_feature_exploration task must ride the dedicated feature-spotlight
    dispatcher, never the two-reviewer ``_handle_board_assigned_task`` (which
    would also spawn the Product Owner and fire the Approve & Start handoff —
    both wrong here), nor the roadmap dispatcher, nor plain PM handling."""
    task = _feature_task()
    stub = MagicMock()
    stub._fetch_tasks = AsyncMock(return_value=[task])
    stub._is_task_handled_this_tick = MagicMock(return_value=False)
    stub._resolve_agent_slug = MagicMock(return_value="head-marketing")
    stub._BOARD_AGENTS = frozenset({"product-owner", "head-marketing"})
    stub._dispatch_roadmap_exploration = AsyncMock()
    stub._dispatch_feature_spotlight_exploration = AsyncMock()
    stub._handle_board_assigned_task = AsyncMock()
    stub._handle_pm_assigned_task = AsyncMock()
    stub._route_unassigned_pm_task = AsyncMock()

    client: Any = MagicMock()
    await AgentOrchestrator._dispatch_pm_work(cast("AgentOrchestrator", stub), client)

    stub._dispatch_feature_spotlight_exploration.assert_awaited_once()
    stub._dispatch_roadmap_exploration.assert_not_awaited()
    stub._handle_board_assigned_task.assert_not_awaited()
    stub._handle_pm_assigned_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_feature_spotlight_tasks_are_never_routed_by_dev_dispatch() -> None:
    tasks = [_feature_task()]
    stub = MagicMock()
    stub._fetch_tasks = AsyncMock(return_value=tasks)
    stub._is_task_handled_this_tick = MagicMock(return_value=False)
    stub._dev_dispatch_one = AsyncMock()

    client: Any = MagicMock()
    await AgentOrchestrator._dispatch_dev_work(cast("AgentOrchestrator", stub), client)

    stub._dev_dispatch_one.assert_not_awaited()


def test_feature_spotlight_prompt_names_real_verbs_and_seen_features() -> None:
    """The prompt must steer HoM to its real verbs (triage /
    propose_feature_spotlight / i_am_idle) and render the seen-features marker,
    with a friendly fallback when the list is empty."""
    orch = _make_orch()
    prompt = orch._build_feature_spotlight_prompt(
        _feature_task(orchestration_markers={"x_seen_features": ["org-memory"]})
    )
    assert "triage()" in prompt
    assert "propose_feature_spotlight(" in prompt
    assert "i_am_idle()" in prompt
    assert "org-memory" in prompt

    empty_prompt = orch._build_feature_spotlight_prompt(_feature_task())
    assert "none yet" in empty_prompt.lower()


def test_feature_spotlight_prompt_names_the_skip_exit() -> None:
    """The HoM must know the skip=True exit exists and that it beats a weak
    forced spotlight — otherwise the smart-cadence work is pointless."""
    orch = _make_orch()
    prompt = orch._build_feature_spotlight_prompt(_feature_task())
    assert "skip=True" in prompt
    assert "skip_reason=" in prompt


def test_feature_spotlight_prompt_renders_enriched_brief_sections() -> None:
    """The x_spotlight_brief marker's seen-dates, shipped-since, and rejected
    sections must reach the spawn prompt HoM actually sees."""
    orch = _make_orch()
    markers_dict = {
        "x_seen_features": ["org-memory"],
        "x_spotlight_brief": {
            "seen": [{"slug": "org-memory", "seen_at": "2026-07-01T00:00:00+00:00"}],
            "shipped_since": [
                {
                    "version": "0.21.0",
                    "date": "2026-07-09",
                    "titles": ["Added", "Fixed"],
                }
            ],
            "rejected": [
                {"slug": "old-one", "title": "Old Feature", "reason": "too niche"}
            ],
        },
    }
    prompt = orch._build_feature_spotlight_prompt(
        _feature_task(orchestration_markers=markers_dict)
    )
    assert "org-memory (seen 2026-07-01)" in prompt
    assert "0.21.0" in prompt
    assert "Added, Fixed" in prompt
    assert "too niche" in prompt


def test_feature_spotlight_prompt_brief_fallbacks_when_marker_missing() -> None:
    """No x_spotlight_brief marker (a pre-brief exploration) must never break
    prompt rendering — every section falls back to a friendly placeholder."""
    orch = _make_orch()
    prompt = orch._build_feature_spotlight_prompt(_feature_task())
    assert "nothing new since the last cycle" in prompt
    assert "(none)" in prompt
