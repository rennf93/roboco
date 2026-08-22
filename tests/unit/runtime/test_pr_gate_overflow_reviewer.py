"""Shared cell-gate overflow reviewer cuts gate pile-up wait.

With one dedicated reviewer per cell (be/fe/ux-pr-reviewer), a same-cell
pile-up of awaiting_pr_review tasks serialized 12-14 min on a single
reviewer. cell-pr-reviewer-2 (board-team, image-identical) is the second
candidate in _select_agent_for_cell's pr_reviewer branch, so the dispatcher
falls back to it when the dedicated reviewer is active. It never serves
root->master or inbound external PRs (both hardcode pr-reviewer-1), preserving
the run-order starvation guard.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.foundation.identity import AGENTS, Role, Team
from roboco.runtime.orchestrator import AGENT_IMAGES, AgentOrchestrator
from roboco.seeds.initial_data import _AGENT_PRESENTATION

_TWO_SPAWNS = 2


def _orch() -> Any:
    orch: Any = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._tick_handled_tasks = set()
    orch._bg_tasks = set()
    orch._build_pr_gate_prompt = MagicMock(return_value="prompt")
    orch._task_git_context = MagicMock(return_value=None)
    return orch


# ---------------------------------------------------------------------------
# _select_agent_for_cell fallback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cell", "dedicated"),
    [
        ("backend", "be-pr-reviewer"),
        ("frontend", "fe-pr-reviewer"),
        ("ux_ui", "ux-pr-reviewer"),
    ],
)
def test_select_returns_dedicated_when_inactive(cell: str, dedicated: str) -> None:
    """Dedicated reviewer is returned when it is NOT active."""
    orch = _orch()
    orch._is_agent_active = MagicMock(return_value=False)
    assert orch._select_agent_for_cell(cell, "pr_reviewer") == dedicated


@pytest.mark.parametrize(
    ("cell", "dedicated"),
    [
        ("backend", "be-pr-reviewer"),
        ("frontend", "fe-pr-reviewer"),
        ("ux_ui", "ux-pr-reviewer"),
    ],
)
def test_select_falls_back_to_overflow_when_dedicated_active(
    cell: str, dedicated: str
) -> None:
    """When the dedicated reviewer is active, fall back to cell-pr-reviewer-2."""
    orch = _orch()

    def is_active(agent_id: str) -> bool:
        return agent_id == dedicated

    orch._is_agent_active = MagicMock(side_effect=is_active)
    result = orch._select_agent_for_cell(cell, "pr_reviewer")
    assert result == "cell-pr-reviewer-2"


# ---------------------------------------------------------------------------
# _dispatch_pr_gate_work pile-up no longer serializes
# ---------------------------------------------------------------------------


def _gate_task(task_id: str, team: str = "backend") -> dict[str, Any]:
    return {
        "id": task_id,
        "team": team,
        "branch_name": "feature/backend/GATE0001",
        "project_id": "22222222-2222-2222-2222-222222222222",
    }


@pytest.mark.asyncio
async def test_pile_up_spawns_dedicated_then_overflow() -> None:
    """Two backend gate tasks: first gets be-pr-reviewer, second gets
    cell-pr-reviewer-2 once be-pr-reviewer is active after the first spawn."""
    orch = _orch()
    tasks = [
        _gate_task("11111111-1111-1111-1111-111111111111"),
        _gate_task("22222222-2222-2222-2222-222222222222"),
    ]
    orch._fetch_tasks = AsyncMock(return_value=tasks)
    orch._pm_respawn_should_gate = AsyncMock(return_value=False)

    active: set[str] = set()
    orch._is_agent_active = MagicMock(side_effect=lambda aid: aid in active)

    spawn_calls: list[str] = []

    async def fake_spawn(**kwargs: Any) -> None:
        active.add(kwargs["agent_id"])
        spawn_calls.append(kwargs["agent_id"])

    orch.spawn_agent = AsyncMock(side_effect=fake_spawn)

    await orch._dispatch_pr_gate_work(MagicMock())

    assert spawn_calls == ["be-pr-reviewer", "cell-pr-reviewer-2"]


@pytest.mark.asyncio
async def test_pile_up_spawned_by_dispatch_pr_gate_work() -> None:
    """Verify spawn_agent is called with spawned_by=_dispatch_pr_gate_work."""
    orch = _orch()
    tasks = [
        _gate_task("11111111-1111-1111-1111-111111111111"),
        _gate_task("22222222-2222-2222-2222-222222222222"),
    ]
    orch._fetch_tasks = AsyncMock(return_value=tasks)
    orch._pm_respawn_should_gate = AsyncMock(return_value=False)

    active: set[str] = set()
    orch._is_agent_active = MagicMock(side_effect=lambda aid: aid in active)

    async def fake_spawn(**kwargs: Any) -> None:
        active.add(kwargs["agent_id"])

    orch.spawn_agent = AsyncMock(side_effect=fake_spawn)

    await orch._dispatch_pr_gate_work(MagicMock())

    assert orch.spawn_agent.await_count == _TWO_SPAWNS
    for call in orch.spawn_agent.await_args_list:
        assert call.kwargs["spawned_by"] == "_dispatch_pr_gate_work"


@pytest.mark.asyncio
async def test_no_routing_to_pr_reviewer_1() -> None:
    """When both be-pr-reviewer and cell-pr-reviewer-2 are active, a backend
    gate task must NOT be routed to pr-reviewer-1 (starvation guard)."""
    orch = _orch()
    tasks = [_gate_task("11111111-1111-1111-1111-111111111111")]
    orch._fetch_tasks = AsyncMock(return_value=tasks)
    orch._pm_respawn_should_gate = AsyncMock(return_value=False)

    active = {"be-pr-reviewer", "cell-pr-reviewer-2"}
    orch._is_agent_active = MagicMock(side_effect=lambda aid: aid in active)
    orch.spawn_agent = AsyncMock()

    await orch._dispatch_pr_gate_work(MagicMock())

    spawn_ids = [c.kwargs["agent_id"] for c in orch.spawn_agent.await_args_list]
    assert "pr-reviewer-1" not in spawn_ids
    assert orch.spawn_agent.await_count == 0


# ---------------------------------------------------------------------------
# Registry / image invariants
# ---------------------------------------------------------------------------


def test_cell_pr_reviewer_2_in_agents_registry() -> None:
    row = AGENTS["cell-pr-reviewer-2"]
    assert row.role == Role.PR_REVIEWER
    assert row.team == Team.BOARD


def test_cell_pr_reviewer_2_has_presentation_name() -> None:
    assert "cell-pr-reviewer-2" in _AGENT_PRESENTATION
    assert _AGENT_PRESENTATION["cell-pr-reviewer-2"]["name"]


def test_cell_pr_reviewer_2_image_is_pr_reviewer() -> None:
    assert AGENT_IMAGES["cell-pr-reviewer-2"] == "roboco-agent-pr-reviewer"
