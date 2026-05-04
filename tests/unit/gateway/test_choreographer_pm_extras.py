"""Tests for the restored PM lifecycle verbs.

Covers: i_will_plan, delegate, submit_up, pm_give_me_work, and the
auto-pause behavior of i_am_idle for PMs.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from roboco.seeds.initial_data import AGENT_UUIDS
from roboco.services.gateway.choreographer import (
    Choreographer,
    ChoreographerDeps,
    DelegateInputs,
)


def _make_deps(**overrides: Any) -> ChoreographerDeps:
    base = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    base.update(overrides)
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
    return ChoreographerDeps(**base)


# ---------------------------------------------------------------------------
# i_will_plan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_will_plan_claims_starts_and_sets_plan() -> None:
    pm_id = uuid4()
    task_id = uuid4()
    pending = MagicMock(
        id=task_id,
        status="pending",
        plan=None,
        assigned_to=None,
        task_type="planning",
        parent_task_id=None,
        sequence=0,
    )
    claimed = MagicMock(
        id=task_id,
        status="claimed",
        plan=None,
        assigned_to=pm_id,
        task_type="planning",
    )
    started = MagicMock(
        id=task_id,
        status="in_progress",
        plan={"text": "x"},
        assigned_to=pm_id,
        task_type="planning",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = pending
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.claim.return_value = claimed
    task_svc.set_plan.return_value = claimed
    task_svc.start.return_value = started
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_plan(pm_id, task_id, plan="break the work into 3 subtasks")
    assert env.error is None
    assert env.status == "in_progress"
    task_svc.claim.assert_awaited_once_with(task_id, pm_id)
    task_svc.set_plan.assert_awaited_once()
    task_svc.start.assert_awaited_once_with(task_id, pm_id)


@pytest.mark.asyncio
async def test_i_will_plan_rejects_non_pm_role() -> None:
    pm_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = MagicMock(id=task_id, status="pending")
    task_svc.agent_for.return_value = MagicMock(role="developer", team="backend")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_plan(pm_id, task_id, plan="x")
    body = env.as_dict()
    assert body["error"] == "not_authorized"


@pytest.mark.asyncio
async def test_i_will_plan_rejects_non_pending_state() -> None:
    pm_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = MagicMock(id=task_id, status="in_progress")
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_plan(pm_id, task_id, plan="x")
    body = env.as_dict()
    assert body["error"] == "invalid_state"


@pytest.mark.asyncio
async def test_i_will_plan_calls_claim_when_pre_assigned_and_pending() -> None:
    """Regression: CEO pre-assigns root task to main-pm with status=pending.

    Old code skipped claim when task.assigned_to already matched the
    caller, leaving status=pending and start() silently returning None
    while the choreographer fabricated an OK envelope. Smoke 2026-05-03.
    """
    pm_id = uuid4()
    task_id = uuid4()
    pending_pre_assigned = MagicMock(
        id=task_id,
        status="pending",
        plan=None,
        assigned_to=pm_id,  # CEO pre-assigned to this PM
        task_type="planning",
        parent_task_id=None,
        sequence=0,
    )
    claimed = MagicMock(
        id=task_id,
        status="claimed",
        plan=None,
        assigned_to=pm_id,
        task_type="planning",
    )
    started = MagicMock(
        id=task_id,
        status="in_progress",
        plan={"text": "x"},
        assigned_to=pm_id,
        task_type="planning",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = pending_pre_assigned
    task_svc.agent_for.return_value = MagicMock(role="main_pm", team="main_pm")
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.claim.return_value = claimed
    task_svc.set_plan.return_value = claimed
    task_svc.start.return_value = started
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_plan(pm_id, task_id, plan="distribute to be-pm and fe-pm")

    assert env.error is None
    assert env.status == "in_progress"
    # The bug: claim was skipped when assigned_to == pm_agent_id.
    # The fix: claim is driven by status == pending, not assigned_to mismatch.
    task_svc.claim.assert_awaited_once_with(task_id, pm_id)
    task_svc.start.assert_awaited_once_with(task_id, pm_id)


@pytest.mark.asyncio
async def test_i_will_plan_surfaces_start_failure_instead_of_faking_ok() -> None:
    """Regression: when start() returns None, the verb must reject — not lie.

    Old code returned Envelope.ok with status='in_progress' even when
    start failed silently. Agents then called delegate against a still-
    pending task and got PARENT_NOT_CLAIMED in a loop. Smoke 2026-05-03.
    """
    pm_id = uuid4()
    task_id = uuid4()
    pending = MagicMock(
        id=task_id,
        status="pending",
        plan=None,
        assigned_to=None,
        task_type="planning",
        parent_task_id=None,
        sequence=0,
    )
    claimed = MagicMock(
        id=task_id,
        status="claimed",
        plan=None,
        assigned_to=pm_id,
        task_type="planning",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = pending
    task_svc.agent_for.return_value = MagicMock(role="main_pm", team="main_pm")
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.claim.return_value = claimed
    task_svc.set_plan.return_value = claimed
    task_svc.start.return_value = None  # the bug: start fails silently
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_plan(pm_id, task_id, plan="x")
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "start failed" in body["message"]


@pytest.mark.asyncio
async def test_i_will_plan_idempotent_when_already_in_progress_for_caller() -> None:
    """Regression: respawned PM re-calling i_will_plan on a task they
    already moved to in_progress must NOT be rejected. Returns OK with
    current state. Smoke 2026-05-04 captured the cycle the old reject
    caused: respawn → reject → reaper drops claim → respawn → loop.
    """
    pm_id = uuid4()
    task_id = uuid4()
    in_progress = MagicMock(
        id=task_id,
        status="in_progress",
        plan={"text": "x"},
        assigned_to=pm_id,
        task_type="planning",
        parent_task_id=None,
        sequence=0,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = in_progress
    task_svc.agent_for.return_value = MagicMock(role="main_pm", team="main_pm")
    task_svc.list_in_progress_for_agent.return_value = [in_progress]
    task_svc.list_paused_for_agent.return_value = []
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_plan(pm_id, task_id, plan="re-entry plan")

    assert env.error is None
    assert env.status == "in_progress"
    # Heartbeat refreshed so the reaper sees activity.
    task_svc.heartbeat.assert_awaited()
    # Did NOT re-call claim or start — already past those.
    task_svc.claim.assert_not_awaited()
    task_svc.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_i_will_plan_idempotent_when_already_claimed_for_caller() -> None:
    """Same regression but task is in claimed (post-claim, pre-start)."""
    pm_id = uuid4()
    task_id = uuid4()
    claimed = MagicMock(
        id=task_id,
        status="claimed",
        plan=None,
        assigned_to=pm_id,
        task_type="planning",
        parent_task_id=None,
        sequence=0,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = claimed
    task_svc.agent_for.return_value = MagicMock(role="main_pm", team="main_pm")
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_plan(pm_id, task_id, plan="re-entry plan")

    assert env.error is None
    assert env.status == "claimed"
    task_svc.heartbeat.assert_awaited()


@pytest.mark.asyncio
async def test_i_will_plan_still_rejects_in_progress_for_other_agent() -> None:
    """Idempotency only applies to the caller. Different PM still rejected."""
    pm_id = uuid4()
    other_pm_id = uuid4()
    task_id = uuid4()
    in_progress = MagicMock(
        id=task_id,
        status="in_progress",
        plan={"text": "x"},
        assigned_to=other_pm_id,  # different PM owns it
        task_type="planning",
        parent_task_id=None,
        sequence=0,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = in_progress
    task_svc.agent_for.return_value = MagicMock(role="main_pm", team="main_pm")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_plan(pm_id, task_id, plan="x")
    body = env.as_dict()

    assert body["error"] == "invalid_state"


@pytest.mark.asyncio
async def test_i_will_plan_returns_tracing_gap_without_plan() -> None:
    pm_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = MagicMock(
        id=task_id, status="pending", plan=None, assigned_to=None
    )
    task_svc.agent_for.return_value = MagicMock(role="main_pm", team="main_pm")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_plan(pm_id, task_id, plan="")
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "plan" in body["missing"]


@pytest.mark.asyncio
async def test_i_will_plan_task_not_found() -> None:
    pm_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_plan(pm_id, task_id, plan="x")
    assert env.as_dict()["error"] == "not_found"


# ---------------------------------------------------------------------------
# delegate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegate_main_pm_to_cell_pm_creates_subtask() -> None:
    main_pm_id = uuid4()
    parent_id = uuid4()
    project_id = uuid4()
    parent = MagicMock(
        id=parent_id,
        project_id=project_id,
        status="in_progress",
        assigned_to=main_pm_id,
    )
    new_task = MagicMock(id=uuid4())
    task_svc = AsyncMock()
    task_svc.get.return_value = parent
    task_svc.agent_for.return_value = MagicMock(role="main_pm", team="main_pm")
    task_svc.get_subtasks.return_value = []
    task_svc.create_subtask.return_value = new_task
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.delegate(
        main_pm_id,
        parent_id,
        DelegateInputs(
            title="Backend planning",
            description="Plan backend work for feature X",
            assigned_to="be-pm",
            team="backend",
        ),
    )
    assert env.error is None
    assert env.status == "created"
    task_svc.create_subtask.assert_awaited_once()
    req = task_svc.create_subtask.call_args.args[0]
    assert req.parent_task_id == parent_id
    assert req.assigned_to == UUID(AGENT_UUIDS["be-pm"])


@pytest.mark.asyncio
async def test_delegate_cell_pm_to_team_dev_creates_subtask() -> None:
    cell_pm_id = uuid4()
    parent_id = uuid4()
    project_id = uuid4()
    parent = MagicMock(
        id=parent_id,
        project_id=project_id,
        status="in_progress",
        assigned_to=cell_pm_id,
    )
    new_task = MagicMock(id=uuid4())
    task_svc = AsyncMock()
    task_svc.get.return_value = parent
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.get_subtasks.return_value = []
    task_svc.create_subtask.return_value = new_task
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.delegate(
        cell_pm_id,
        parent_id,
        DelegateInputs(
            title="Implement endpoint",
            description="Add /v1/foo endpoint with tests",
            assigned_to="be-dev-1",
            team="backend",
        ),
    )
    assert env.error is None
    assert env.status == "created"


@pytest.mark.asyncio
async def test_delegate_main_pm_to_dev_is_rejected() -> None:
    main_pm_id = uuid4()
    parent_id = uuid4()
    parent = MagicMock(id=parent_id, project_id=uuid4())
    task_svc = AsyncMock()
    task_svc.get.return_value = parent
    task_svc.agent_for.return_value = MagicMock(role="main_pm", team="main_pm")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.delegate(
        main_pm_id,
        parent_id,
        DelegateInputs(
            title="x", description="y", assigned_to="be-dev-1", team="backend"
        ),
    )
    body = env.as_dict()
    assert body["error"] == "not_authorized"
    assert "be-pm" in body["remediate"]


@pytest.mark.asyncio
async def test_delegate_cell_pm_to_other_pm_rejected() -> None:
    cell_pm_id = uuid4()
    parent_id = uuid4()
    parent = MagicMock(id=parent_id, project_id=uuid4())
    task_svc = AsyncMock()
    task_svc.get.return_value = parent
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.delegate(
        cell_pm_id,
        parent_id,
        DelegateInputs(title="x", description="y", assigned_to="be-pm", team="backend"),
    )
    body = env.as_dict()
    assert body["error"] == "not_authorized"


@pytest.mark.asyncio
async def test_delegate_unknown_assignee_returns_invalid_state() -> None:
    pm_id = uuid4()
    parent_id = uuid4()
    parent = MagicMock(id=parent_id, project_id=uuid4())
    task_svc = AsyncMock()
    task_svc.get.return_value = parent
    task_svc.agent_for.return_value = MagicMock(role="main_pm", team="main_pm")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.delegate(
        pm_id,
        parent_id,
        DelegateInputs(
            title="x", description="y", assigned_to="nope-pm", team="backend"
        ),
    )
    body = env.as_dict()
    assert body["error"] == "not_authorized"


@pytest.mark.asyncio
async def test_delegate_invalid_team_enum_rejected() -> None:
    pm_id = uuid4()
    parent_id = uuid4()
    parent = MagicMock(id=parent_id, project_id=uuid4())
    task_svc = AsyncMock()
    task_svc.get.return_value = parent
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.delegate(
        pm_id,
        parent_id,
        DelegateInputs(
            title="x", description="y", assigned_to="be-dev-1", team="not-a-team"
        ),
    )
    assert env.as_dict()["error"] == "invalid_state"


# ---------------------------------------------------------------------------
# submit_up
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_up_opens_pr_and_reassigns_to_main_pm() -> None:
    pm_id = uuid4()
    task_id = uuid4()
    main_pm_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="in_progress",
        assigned_to=pm_id,
        branch_name="feature/backend/abc123",
        team="backend",
    )
    after = MagicMock(
        id=task_id,
        status="awaiting_pm_review",
        assigned_to=pm_id,
        branch_name="feature/backend/abc123",
        team="backend",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.all_subtasks_terminal.return_value = True
    task_svc.submit_pm_review.return_value = after
    task_svc.main_pm_agent.return_value = MagicMock(id=main_pm_id)
    git_svc = AsyncMock()
    git_svc.create_pr.return_value = {"pr_number": 12, "pr_url": "x"}
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    deps = _make_deps(task=task_svc, git=git_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.submit_up(
        pm_id, task_id, notes="cell completed all subtasks; ready for main pm"
    )
    assert env.error is None
    assert env.status == "awaiting_pm_review"
    git_svc.create_pr.assert_awaited_once()
    task_svc.reassign.assert_awaited_once()


@pytest.mark.asyncio
async def test_submit_up_blocks_when_subtasks_not_terminal() -> None:
    pm_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="in_progress",
        assigned_to=pm_id,
        branch_name="feature/backend/abc123",
        team="backend",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.all_subtasks_terminal.return_value = False
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.submit_up(pm_id, task_id, notes="ready for main pm please review")
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "subtasks" in str(body["missing"]).lower()


@pytest.mark.asyncio
async def test_submit_up_rejects_main_pm_role() -> None:
    main_pm_id = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="in_progress", assigned_to=main_pm_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="main_pm", team="main_pm")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.submit_up(main_pm_id, task_id, notes="enough words to pass min len")
    body = env.as_dict()
    assert body["error"] == "not_authorized"


@pytest.mark.asyncio
async def test_submit_up_blocks_without_journal_decision() -> None:
    pm_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="in_progress",
        assigned_to=pm_id,
        branch_name="feature/backend/abc",
        team="backend",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = False
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.submit_up(pm_id, task_id, notes="enough words to pass min len")
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "journal:decision" in body["missing"]


@pytest.mark.asyncio
async def test_submit_up_short_notes_rejected() -> None:
    pm_id = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="in_progress", assigned_to=pm_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.submit_up(pm_id, task_id, notes="short")
    body = env.as_dict()
    assert body["error"] == "tracing_gap"


# ---------------------------------------------------------------------------
# pm_give_me_work
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pm_give_me_work_returns_first_assigned() -> None:
    pm_id = uuid4()
    t = MagicMock(id=uuid4(), status="pending", title="x", team="backend")
    task_svc = AsyncMock()
    task_svc.list_assigned_for_agent.return_value = [t]
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.pm_give_me_work(pm_id)
    assert env.error is None
    assert env.task_id == str(t.id)
    assert "i_will_plan" in env.next


@pytest.mark.asyncio
async def test_pm_give_me_work_returns_idle_when_empty() -> None:
    pm_id = uuid4()
    task_svc = AsyncMock()
    task_svc.list_assigned_for_agent.return_value = []
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.pm_give_me_work(pm_id)
    assert env.status == "idle"


@pytest.mark.asyncio
async def test_pm_give_me_work_paused_hint_mentions_subtasks() -> None:
    pm_id = uuid4()
    t = MagicMock(id=uuid4(), status="paused", title="x", team="backend")
    task_svc = AsyncMock()
    task_svc.list_assigned_for_agent.return_value = [t]
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.pm_give_me_work(pm_id)
    assert "subtasks" in env.next or "complete" in env.next


# ---------------------------------------------------------------------------
# i_am_idle auto-pause
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_am_idle_auto_pauses_in_progress_tasks() -> None:
    agent_id = uuid4()
    t = MagicMock(id=uuid4(), status="in_progress")
    task_svc = AsyncMock()
    task_svc.list_in_progress_for_agent.return_value = [t]
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_am_idle(agent_id)
    assert env.status == "idle"
    task_svc.pause_for_agent.assert_awaited_once_with(agent_id, t.id)
    task_svc.mark_agent_idle.assert_awaited_once()


@pytest.mark.asyncio
async def test_i_am_idle_no_in_progress_skips_pause() -> None:
    agent_id = uuid4()
    task_svc = AsyncMock()
    task_svc.list_in_progress_for_agent.return_value = []
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_am_idle(agent_id)
    assert env.status == "idle"
    task_svc.pause_for_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_i_am_idle_with_unread_skips_pause_and_idle() -> None:
    agent_id = uuid4()
    task_svc = AsyncMock()
    deps = _make_deps(task=task_svc)
    # Override the default empty list AFTER _make_deps has zeroed it out.
    deps.evidence_repo.list_unread_a2a.return_value = ["something"]
    c = Choreographer(deps)

    env = await c.i_am_idle(agent_id)
    assert env.status == "idle_with_unread"
    task_svc.list_in_progress_for_agent.assert_not_awaited()
    task_svc.mark_agent_idle.assert_not_awaited()
