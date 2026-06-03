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
# _claimed_task_needs_agent (#19)
# ---------------------------------------------------------------------------

_STALE = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
_FRESH = datetime.now(UTC).isoformat()


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
        "updated_at": _FRESH,
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
