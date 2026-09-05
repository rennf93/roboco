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
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from roboco.foundation.identity import AGENTS, Role, Team
from roboco.runtime.orchestrator import AGENT_IMAGES, AgentOrchestrator
from roboco.seeds.initial_data import _AGENT_PRESENTATION, AGENT_UUIDS

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
# _sync_gate_reviewer_assignment: assigned_to follows the spawned reviewer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_gate_reviewer_assignment_skips_when_unassigned() -> None:
    """A task with no assigned_to yet (the common, already-correct case
    once submit_up/submit_root assign the primary reviewer at gate entry)
    costs no DB round trip."""
    orch = _orch()
    with patch("roboco.services.task.TaskService") as task_service_cls:
        await orch._sync_gate_reviewer_assignment(
            _gate_task("11111111-1111-1111-1111-111111111111"), "be-pr-reviewer"
        )
    task_service_cls.assert_not_called()


@pytest.mark.asyncio
async def test_sync_gate_reviewer_assignment_skips_when_already_matches() -> None:
    """assigned_to/claimed_by already name the reviewer being spawned (the
    real post-gate-entry state ``_notify_pr_reviewer`` leaves) -> no DB
    call."""
    orch = _orch()
    task = _gate_task("11111111-1111-1111-1111-111111111111")
    task["assigned_to"] = AGENT_UUIDS["be-pr-reviewer"]
    task["claimed_by"] = AGENT_UUIDS["be-pr-reviewer"]
    with patch("roboco.services.task.TaskService") as task_service_cls:
        await orch._sync_gate_reviewer_assignment(task, "be-pr-reviewer")
    task_service_cls.assert_not_called()


@pytest.mark.asyncio
async def test_sync_gate_reviewer_assignment_reassigns_on_overflow_mismatch() -> None:
    """assigned_to/claimed_by name the busy dedicated reviewer (the
    gate-entry state) but the overflow reviewer is the one actually being
    spawned -> reassign to the overflow reviewer so assigned_to names the
    real spawned agent."""
    orch = _orch()
    task = _gate_task("11111111-1111-1111-1111-111111111111")
    task["assigned_to"] = AGENT_UUIDS["be-pr-reviewer"]
    task["claimed_by"] = AGENT_UUIDS["be-pr-reviewer"]

    svc = AsyncMock()
    db = MagicMock()
    db_ctx = MagicMock(
        __aenter__=AsyncMock(return_value=db), __aexit__=AsyncMock(return_value=False)
    )
    with (
        patch("roboco.db.base.get_db_context", return_value=db_ctx),
        patch("roboco.services.task.TaskService", return_value=svc) as task_service_cls,
    ):
        await orch._sync_gate_reviewer_assignment(task, "cell-pr-reviewer-2")

    task_service_cls.assert_called_once_with(db)
    svc.reassign.assert_awaited_once_with(
        UUID(task["id"]), UUID(AGENT_UUIDS["cell-pr-reviewer-2"])
    )


@pytest.mark.asyncio
async def test_dispatch_primary_idle_spawns_primary_no_reassign() -> None:
    """(a) Real post-gate-entry state: assigned_to/claimed_by both already
    name the primary reviewer (set by ``_notify_pr_reviewer``),
    active_claimant_id still None (no real claim taken yet). Primary idle
    -> primary spawned, and since the entry assignment already matches,
    ``_sync_gate_reviewer_assignment`` costs no DB round trip."""
    orch = _orch()
    task = _gate_task("11111111-1111-1111-1111-111111111111")
    task["assigned_to"] = AGENT_UUIDS["be-pr-reviewer"]
    task["claimed_by"] = AGENT_UUIDS["be-pr-reviewer"]
    task["active_claimant_id"] = None
    orch._fetch_tasks = AsyncMock(return_value=[task])
    orch._pm_respawn_should_gate = AsyncMock(return_value=False)
    orch._is_agent_active = MagicMock(return_value=False)
    orch.spawn_agent = AsyncMock()

    with patch("roboco.services.task.TaskService") as task_service_cls:
        await orch._dispatch_pr_gate_work(MagicMock())

    task_service_cls.assert_not_called()
    assert orch.spawn_agent.await_args.kwargs["agent_id"] == "be-pr-reviewer"


@pytest.mark.asyncio
async def test_dispatch_pr_gate_work_reassigns_on_overflow_spawn() -> None:
    """(b) Real post-gate-entry state, primary busy -> overflow spawned and
    the task is synced to the overflow reviewer that actually gets
    spawned."""
    orch = _orch()
    task = _gate_task("11111111-1111-1111-1111-111111111111")
    task["assigned_to"] = AGENT_UUIDS["be-pr-reviewer"]
    task["claimed_by"] = AGENT_UUIDS["be-pr-reviewer"]
    orch._fetch_tasks = AsyncMock(return_value=[task])
    orch._pm_respawn_should_gate = AsyncMock(return_value=False)
    orch._is_agent_active = MagicMock(side_effect=lambda aid: aid == "be-pr-reviewer")
    orch.spawn_agent = AsyncMock()
    orch._sync_gate_reviewer_assignment = AsyncMock()

    await orch._dispatch_pr_gate_work(MagicMock())

    orch._sync_gate_reviewer_assignment.assert_awaited_once_with(
        task, "cell-pr-reviewer-2"
    )
    assert orch.spawn_agent.await_args.kwargs["agent_id"] == "cell-pr-reviewer-2"


# ---------------------------------------------------------------------------
# _gate_task_reviewer / double-dispatch onto an already-claimed task
# ---------------------------------------------------------------------------


def test_gate_task_reviewer_targets_the_existing_claimant() -> None:
    """A task with a REAL claim (``active_claimant_id`` set by
    ``claim_gate_review``) always resolves back to that claimant, never to
    the team-based selection (which could pick a different/overflow
    reviewer)."""
    orch = _orch()
    task = _gate_task("11111111-1111-1111-1111-111111111111", team="frontend")
    task["active_claimant_id"] = AGENT_UUIDS["fe-pr-reviewer"]
    assert orch._gate_task_reviewer(task) == "fe-pr-reviewer"


def test_gate_task_reviewer_ignores_assigned_to_without_a_real_claim() -> None:
    """assigned_to/claimed_by naming the primary reviewer is the gate-entry
    state ``_notify_pr_reviewer`` leaves before any claim - it must NOT pin
    the reviewer the way a real ``active_claimant_id`` claim does. Falls
    through to team selection, which lands on the same primary reviewer
    while it is idle."""
    orch = _orch()
    orch._is_agent_active = MagicMock(return_value=False)
    task = _gate_task("11111111-1111-1111-1111-111111111111", team="frontend")
    task["assigned_to"] = AGENT_UUIDS["fe-pr-reviewer"]
    task["claimed_by"] = AGENT_UUIDS["fe-pr-reviewer"]
    assert orch._gate_task_reviewer(task) == "fe-pr-reviewer"


def test_gate_task_reviewer_falls_back_to_team_selection_when_unclaimed() -> None:
    orch = _orch()
    orch._is_agent_active = MagicMock(return_value=False)
    task = _gate_task("11111111-1111-1111-1111-111111111111", team="frontend")
    assert orch._gate_task_reviewer(task) == "fe-pr-reviewer"


def test_gate_task_reviewer_falls_back_when_claimant_slug_unresolvable() -> None:
    """An unrecognized active_claimant_id (not in the seeded AGENT_UUIDS
    map) falls back to team-based selection rather than dispatching a
    bogus id."""
    orch = _orch()
    orch._is_agent_active = MagicMock(return_value=False)
    task = _gate_task("11111111-1111-1111-1111-111111111111", team="frontend")
    task["active_claimant_id"] = "99999999-9999-9999-9999-999999999999"
    assert orch._gate_task_reviewer(task) == "fe-pr-reviewer"


@pytest.mark.asyncio
async def test_no_second_spawn_onto_an_already_claimed_task() -> None:
    """(c) Regression: the primary reviewer holds a REAL claim
    (active_claimant_id) from an earlier tick and is still active, so a
    later tick must not spawn ANY reviewer (not the overflow, not a
    duplicate of the claimant) onto the same task."""
    orch = _orch()
    task = _gate_task("11111111-1111-1111-1111-111111111111", team="frontend")
    task["assigned_to"] = AGENT_UUIDS["fe-pr-reviewer"]
    task["claimed_by"] = AGENT_UUIDS["fe-pr-reviewer"]
    task["active_claimant_id"] = AGENT_UUIDS["fe-pr-reviewer"]
    orch._fetch_tasks = AsyncMock(return_value=[task])
    orch._pm_respawn_should_gate = AsyncMock(return_value=False)
    orch._is_agent_active = MagicMock(side_effect=lambda aid: aid == "fe-pr-reviewer")
    orch.spawn_agent = AsyncMock()

    await orch._dispatch_pr_gate_work(MagicMock())

    orch.spawn_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_respawns_same_claimant_not_overflow_when_container_died() -> (
    None
):
    """(d) Regression (live 2026-09, task 8ea28f7a / PR #990): fe-pr-reviewer
    held a REAL claim (active_claimant_id) on the gate task, its container
    exited between ticks, and the dispatcher kept spawning cell-pr-reviewer-2
    onto the SAME task every tick, each claim_gate_review rejected outright,
    burning spawns until the respawn breaker paged the CEO over a task that
    had already advanced. The claimed reviewer's own container dying must
    respawn THAT SAME reviewer, never the overflow."""
    orch = _orch()
    task = _gate_task("11111111-1111-1111-1111-111111111111", team="frontend")
    task["assigned_to"] = AGENT_UUIDS["fe-pr-reviewer"]
    task["claimed_by"] = AGENT_UUIDS["fe-pr-reviewer"]
    task["active_claimant_id"] = AGENT_UUIDS["fe-pr-reviewer"]
    orch._fetch_tasks = AsyncMock(return_value=[task])
    orch._pm_respawn_should_gate = AsyncMock(return_value=False)
    orch._is_agent_active = MagicMock(return_value=False)
    orch.spawn_agent = AsyncMock()
    orch._sync_gate_reviewer_assignment = AsyncMock()

    await orch._dispatch_pr_gate_work(MagicMock())

    assert orch.spawn_agent.await_args.kwargs["agent_id"] == "fe-pr-reviewer"
    spawn_ids = [c.kwargs["agent_id"] for c in orch.spawn_agent.await_args_list]
    assert "cell-pr-reviewer-2" not in spawn_ids


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
