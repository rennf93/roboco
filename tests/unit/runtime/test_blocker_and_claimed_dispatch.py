"""Dispatch routing for blocked tasks (#17) and agentless claims (#19).

#17: a blocked task reassigned to Main PM must dispatch THAT assignee to
unblock it, not the ex-assignee cell PM — the pre-unblock note is assignee-only
and the ex-assignee got not_authorized, livelocking the respawn.

#19: a task left claimed/in_progress with an assignee but no running container
is invisibly stuck (only PENDING tasks get fresh dispatch). The orchestrator
must (re)spawn the assignee after a short grace window, or release the claim to
pending when the assignee is unknown.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.models.runtime import AgentInstance
from roboco.runtime.orchestrator import AgentOrchestrator, AgentState
from roboco.seeds.initial_data import AGENT_UUIDS


def _orch() -> AgentOrchestrator:
    orch = object.__new__(AgentOrchestrator)
    orch._instances = {}
    return orch


def _active_instance(agent_id: str) -> AgentInstance:
    return AgentInstance(agent_id=agent_id, state=AgentState.ACTIVE)


# ---------------------------------------------------------------------------
# _blocker_resolver_slug (#17)
# ---------------------------------------------------------------------------


def test_blocked_task_assigned_to_main_pm_dispatches_main_pm() -> None:
    orch = _orch()
    task: dict[str, Any] = {
        "id": "t1",
        "team": "backend",
        "assigned_to": AGENT_UUIDS["main-pm"],
    }
    # The current assignee (Main PM) holds unblock authority — dispatch THEM,
    # not the ex-assignee cell PM (be-pm), which would loop on not_authorized.
    assert orch._blocker_resolver_slug(task) == "main-pm"


def test_blocked_task_assigned_to_board_dispatches_board() -> None:
    orch = _orch()
    task: dict[str, Any] = {
        "id": "t1",
        "team": "backend",
        "assigned_to": AGENT_UUIDS["product-owner"],
    }
    assert orch._blocker_resolver_slug(task) == "product-owner"


def test_blocked_task_held_by_dev_falls_back_to_cell_pm() -> None:
    orch = _orch()
    # A dev raised i_am_blocked and still holds the task → cell PM resolves.
    task: dict[str, Any] = {
        "id": "t1",
        "team": "backend",
        "assigned_to": AGENT_UUIDS["be-dev-1"],
    }
    assert orch._blocker_resolver_slug(task) == "be-pm"


def test_blocked_task_unassigned_falls_back_to_cell_pm() -> None:
    orch = _orch()
    task: dict[str, Any] = {"id": "t1", "team": "frontend", "assigned_to": None}
    assert orch._blocker_resolver_slug(task) == "fe-pm"


def test_blocked_task_non_cell_team_unassigned_is_unroutable() -> None:
    orch = _orch()
    task: dict[str, Any] = {"id": "t1", "team": "board", "assigned_to": None}
    assert orch._blocker_resolver_slug(task) is None


# ---------------------------------------------------------------------------
# _claimed_task_needs_agent — claimed-but-no-agent detection
# ---------------------------------------------------------------------------

_STALE = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()


def test_claimed_task_with_no_agent_past_grace_returns_assignee() -> None:
    orch = _orch()
    task: dict[str, Any] = {
        "id": "t1",
        "status": "claimed",
        "assigned_to": AGENT_UUIDS["be-dev-1"],
        "updated_at": _STALE,
    }
    assert orch._claimed_task_needs_agent(task) == "be-dev-1"


def test_claimed_task_with_active_agent_is_healthy() -> None:
    orch = _orch()
    orch._instances["be-dev-1"] = _active_instance("be-dev-1")
    task: dict[str, Any] = {
        "id": "t1",
        "status": "claimed",
        "assigned_to": AGENT_UUIDS["be-dev-1"],
        "updated_at": _STALE,
    }
    assert orch._claimed_task_needs_agent(task) is None


def test_claimed_task_within_grace_window_is_skipped() -> None:
    orch = _orch()
    task: dict[str, Any] = {
        "id": "t1",
        "status": "claimed",
        "assigned_to": AGENT_UUIDS["be-dev-1"],
        # Compute "fresh" at test time, not module load: the grace check uses
        # wall-clock now(), so a module-level constant ages out of the window
        # during a long full-suite run and flakes this assertion.
        "updated_at": datetime.now(UTC).isoformat(),
    }
    # Fresh claim — spawn may still be in flight; do not churn.
    assert orch._claimed_task_needs_agent(task) is None


def test_claimed_task_without_assignee_is_skipped() -> None:
    orch = _orch()
    task: dict[str, Any] = {
        "id": "t1",
        "status": "claimed",
        "assigned_to": None,
        "claimed_by": None,
        "updated_at": _STALE,
    }
    assert orch._claimed_task_needs_agent(task) is None


def test_hitl_blocked_claimed_task_is_skipped() -> None:
    orch = _orch()
    task: dict[str, Any] = {
        "id": "t1",
        "status": "blocked",
        "blocker_resolver_type": "human",
        "assigned_to": AGENT_UUIDS["be-dev-1"],
        "updated_at": _STALE,
    }
    assert orch._claimed_task_needs_agent(task) is None


def test_in_progress_task_with_no_agent_returns_assignee() -> None:
    orch = _orch()
    task: dict[str, Any] = {
        "id": "t1",
        "status": "in_progress",
        "assigned_to": AGENT_UUIDS["fe-dev-2"],
        "updated_at": _STALE,
    }
    assert orch._claimed_task_needs_agent(task) == "fe-dev-2"


# ---------------------------------------------------------------------------
# _get_prompt_for_agent — role-appropriate respawn prompt (#19)
# ---------------------------------------------------------------------------
#
# A respawn must hand each role the prompt it can act on. The bug: the PM/board
# branch fell through to the developer prompt, telling a PM/board agent to write
# code and call verbs it does not own.


def _task(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "t1",
        "title": "T",
        "status": "in_progress",
        "team": "backend",
    }
    base.update(over)
    return base


@pytest.mark.parametrize(
    ("agent_slug", "marker"),
    [
        ("be-dev-1", "development task"),
        ("be-qa", "ready for QA review"),
        ("be-doc", "ready for documentation"),
        ("be-pm", "PM for backend team"),
        ("main-pm", "MAIN PM at RoboCo"),
        ("product-owner", "You are on the Board"),
        ("auditor", "AUDIT"),
    ],
)
def test_get_prompt_for_agent_routes_by_role(agent_slug: str, marker: str) -> None:
    orch = _orch()
    prompt = orch._get_prompt_for_agent(agent_slug, _task())
    assert marker in prompt


def test_get_prompt_for_pm_is_not_the_dev_prompt() -> None:
    # Regression for #19: a respawned PM must NOT receive the developer prompt.
    orch = _orch()
    pm_prompt = orch._get_prompt_for_agent("be-pm", _task())
    assert "development task" not in pm_prompt
    assert "You do NOT code" in pm_prompt


def test_get_prompt_for_board_is_not_the_dev_prompt() -> None:
    orch = _orch()
    board_prompt = orch._get_prompt_for_agent("product-owner", _task())
    assert "development task" not in board_prompt
    assert "do NOT build, code" in board_prompt


def test_head_marketing_prompt_is_marketing_on_marketing_team() -> None:
    orch = _orch()
    prompt = orch._get_prompt_for_agent("head-marketing", _task(team="marketing"))
    assert "marketing task" in prompt


def test_head_marketing_prompt_is_board_off_marketing_team() -> None:
    orch = _orch()
    prompt = orch._get_prompt_for_agent("head-marketing", _task(team="backend"))
    assert "You are on the Board" in prompt


# ---------------------------------------------------------------------------
# _dispatch_claimed_without_agent — one-spawn-per-tick throttle (#19)
# ---------------------------------------------------------------------------
#
# `monkeypatch.setattr` is used to stub instance methods because direct
# attribute assignment (`orch.spawn_agent = ...`) trips mypy's method-assign
# check; the fixture is the type-safe, suppression-free way to do it.


def _stub_git_context(orch: AgentOrchestrator, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orch, "_task_git_context", lambda _task: None)


@pytest.mark.asyncio
async def test_dispatch_claimed_without_agent_spawns_at_most_one_per_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = _orch()
    orch._tick_handled_tasks = set()
    stale_tasks = [
        {"id": f"t{i}", "status": "claimed", "assigned_to": AGENT_UUIDS["be-dev-1"]}
        for i in range(3)
    ]
    monkeypatch.setattr(orch, "_fetch_tasks", AsyncMock(return_value=stale_tasks))
    monkeypatch.setattr(orch, "_claimed_task_needs_agent", lambda _task: "be-dev-1")
    _stub_git_context(orch, monkeypatch)
    spawn = AsyncMock()
    monkeypatch.setattr(orch, "spawn_agent", spawn)

    await orch._dispatch_claimed_without_agent(client=MagicMock())

    # Three agentless claims, but only ONE container spawned this tick.
    spawn.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_claimed_without_agent_releases_unknown_without_spending_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The release-to-pending path spawns nothing and must NOT consume the
    # per-tick spawn budget — it keeps draining stale unknown claims, then
    # spawns the first task with a known assignee.
    orch = _orch()
    orch._tick_handled_tasks = set()
    tasks = [
        {"id": "u1", "status": "claimed", "assigned_to": "ghost-uuid"},
        {"id": "u2", "status": "claimed", "assigned_to": "ghost-uuid"},
        {"id": "k1", "status": "claimed", "assigned_to": AGENT_UUIDS["be-dev-1"]},
    ]
    monkeypatch.setattr(orch, "_fetch_tasks", AsyncMock(return_value=tasks))

    def _needs(task: dict[str, Any]) -> str:
        return orch._resolve_agent_slug(str(task["assigned_to"]))

    monkeypatch.setattr(orch, "_claimed_task_needs_agent", _needs)
    _stub_git_context(orch, monkeypatch)
    release = AsyncMock()
    monkeypatch.setattr(orch, "_release_claim_to_pending", release)
    spawn = AsyncMock()
    monkeypatch.setattr(orch, "spawn_agent", spawn)

    await orch._dispatch_claimed_without_agent(client=MagicMock())

    expected_releases = 2  # both ghost claims released
    assert release.await_count == expected_releases
    spawn.assert_awaited_once()  # then one known assignee respawned


@pytest.mark.asyncio
async def test_handle_dev_existing_owner_skips_blocked() -> None:
    """A blocked task's owner is not respawned — it has no legal move from
    blocked, so respawning it only churns; it waits for unblock or release."""
    orch = _orch()
    orch._respawn_dev_if_inactive = AsyncMock()
    orch._is_agent_active = MagicMock(return_value=False)
    await orch._handle_dev_existing_owner({"id": "t1"}, "blocked", "be-dev-1")
    orch._respawn_dev_if_inactive.assert_not_called()


@pytest.mark.asyncio
async def test_handle_dev_existing_owner_respawns_in_progress() -> None:
    """An in_progress task whose owner is inactive is still respawned."""
    orch = _orch()
    orch._respawn_dev_if_inactive = AsyncMock()
    orch._is_agent_active = MagicMock(return_value=False)
    await orch._handle_dev_existing_owner({"id": "t1"}, "in_progress", "be-dev-1")
    orch._respawn_dev_if_inactive.assert_awaited_once()
