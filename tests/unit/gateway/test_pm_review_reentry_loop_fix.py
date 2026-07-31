"""PM re-entry on an awaiting_pm_review task must steer, never re-claim.

Live incident: an awaiting_pm_review task (already past the in-path PR gate)
kept getting re-offered to its owning PM by give_me_work. The respawned PM
called i_will_plan, and CLAIM_RULES used to grant CELL_PM/MAIN_PM a claim from
AWAITING_PM_REVIEW — so the composed (claim, set_plan, start) sequence legally
reset the task to in_progress and re-ran submit_up -> pr_pass ->
awaiting_pm_review forever (one production task looped 11 cycles across 37
spawns in 4h). ``_handle_pm_reentry`` now recognizes this status for the
owning PM and returns a steering-only OK envelope (complete / request_changes)
with no claim and no state change; CLAIM_RULES no longer permits the claim at
all, so a non-owner (or any other caller) falls through to a normal spec
rejection.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps

# ---------------------------------------------------------------------------
# Shared fixture helpers — same pattern as test_i_will_plan_sub_tasks_gate.py
# ---------------------------------------------------------------------------


def _make_deps(**overrides: Any) -> ChoreographerDeps:
    base: dict[str, Any] = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    base.update(overrides)
    task = base["task"]
    task.session = MagicMock()
    task.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    repo = base["evidence_repo"]
    for method in (
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
        "journal_highlights_for_task",
    ):
        getattr(repo, method).return_value = []
    _ldef = base["journal"].latest_decision_at.return_value
    if type(_ldef).__name__ in ("MagicMock", "AsyncMock"):
        base["journal"].latest_decision_at.return_value = datetime.now(UTC)
    return ChoreographerDeps(**base)


def _review_task_svc(task_id: object, pm_id: object, *, role: str) -> AsyncMock:
    """TaskService mock for a PM re-entering its own awaiting_pm_review task."""
    task_svc = AsyncMock()
    task_svc.get.return_value = MagicMock(
        id=task_id,
        status="awaiting_pm_review",
        plan={"text": "already planned"},
        assigned_to=pm_id,
        task_type="planning",
        parent_task_id=None,
        sequence=0,
        team="backend",
        commits=["abc123"],
        pr_number=42,
        branch_name="feature/backend/abc",
        quick_context=None,
    )
    task_svc.agent_for.return_value = MagicMock(
        id=pm_id, role=role, team="backend", slug=None
    )
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.get_subtasks.return_value = []
    task_svc.session = MagicMock()
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    return task_svc


async def _assert_steers_without_reclaiming(role: str) -> None:
    pm_id = uuid4()
    task_id = uuid4()
    task_svc = _review_task_svc(task_id, pm_id, role=role)
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_plan(pm_id, task_id, plan="resume")
    body = env.as_dict()

    assert body.get("error") is None, body
    assert body["status"] == "awaiting_pm_review", body
    assert "complete" in body["next"], body

    task_svc.claim.assert_not_awaited()
    task_svc.set_plan.assert_not_awaited()
    task_svc.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_cell_pm_reentry_awaiting_pm_review_steers_to_complete() -> None:
    await _assert_steers_without_reclaiming("cell_pm")


@pytest.mark.asyncio
async def test_main_pm_reentry_awaiting_pm_review_steers_to_complete() -> None:
    await _assert_steers_without_reclaiming("main_pm")


@pytest.mark.asyncio
async def test_non_owner_awaiting_pm_review_is_rejected_not_reclaimed() -> None:
    """A PM that does NOT own the review task gets the normal spec rejection —
    CLAIM_RULES no longer grants a claim from awaiting_pm_review to anyone, so
    this falls straight through to invalid_state instead of resetting the task.
    """
    pm_id = uuid4()
    other_pm_id = uuid4()
    task_id = uuid4()
    task_svc = _review_task_svc(task_id, other_pm_id, role="cell_pm")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_plan(pm_id, task_id, plan="resume")
    body = env.as_dict()

    assert body.get("error") == "invalid_state", body
    task_svc.claim.assert_not_awaited()
    task_svc.set_plan.assert_not_awaited()
    task_svc.start.assert_not_awaited()
