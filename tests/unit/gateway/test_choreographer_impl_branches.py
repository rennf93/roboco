"""Targeted coverage for branches in roboco.services.gateway.choreographer._impl.

Each test pins one rejection-envelope branch so the larger Choreographer
verb continues to surface remediation hints rather than crash on edge
states (claim failures, start failures, missing parents, etc.).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import structlog
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps
from roboco.services.gateway.choreographer._impl import DelegateInputs
from roboco.services.gateway.envelope import Envelope

# #172: a developer fresh claim must carry a substantive step checklist.
# Inert on re-entry/error/non-dev paths (the gate is skipped or the call
# short-circuits before it), so it is safe to pass everywhere.
_STEPS = [
    {
        "title": "Implement the change",
        "description": (
            "edit the target file, add tests, run them, and stage the "
            "change for commit on the task branch"
        ),
    }
]


def _wire_dev_task_svc(
    task_id, *, status: str, assigned_to=None, plan=None, parent_task_id=None
):
    """Build a TaskService AsyncMock pre-wired with claim-guard side effects.

    Defaults `agent_for` → developer/backend and the three list-* methods to
    empty lists so claim-guard short-circuits never fire unintentionally.
    Also wires ``session.begin_nested()`` so VerbRunner's savepoint context
    manager works against the mock.
    """
    task_svc = AsyncMock()
    task_svc.get.return_value = MagicMock(
        status=status,
        assigned_to=assigned_to,
        plan=plan,
        id=task_id,
        title="t",
        task_type="code",
        parent_task_id=parent_task_id,
        team="backend",
        commits=[],
        pr_number=None,
        branch_name="feature/backend/abc",
        quick_context=None,
    )
    task_svc.agent_for.return_value = MagicMock(
        role="developer", team="backend", slug=None
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
    # VerbRunner wraps composed atomic actions in
    # ``task.session.begin_nested()``. AsyncMock auto-attribute access
    # would return an unawaitable coroutine, breaking the
    # ``async with`` protocol. Overwrite session with a MagicMock that
    # implements the async-context-manager protocol explicitly.
    task_dep = base["task"]
    task_dep.session = MagicMock()
    task_dep.session.begin_nested = MagicMock(
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
    # C8: default-fresh journal:decision so PM-decision gate passes.
    # Tests that exercise the gate boundary stub their own value.
    # The check matches MagicMock and AsyncMock (the two default sentinel
    # types pytest's unittest.mock leaves on un-stubbed return_values).
    _ldef = base["journal"].latest_decision_at.return_value
    if type(_ldef).__name__ in ("MagicMock", "AsyncMock"):
        base["journal"].latest_decision_at.return_value = datetime.now(UTC)
    return ChoreographerDeps(**base)


# ---------------------------------------------------------------------------
# _emit_rejection: ok envelope passes through unchanged (line 158)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_rejection_passes_through_ok_envelope() -> None:
    deps = _make_deps()
    c = Choreographer(deps)
    ok = Envelope.ok(status="x", task_id=None, next="n", context_briefing={})
    result = await c._emit_rejection(ok, agent_id=uuid4(), task_id=None, verb="x")
    assert result is ok
    deps.audit.log_event.assert_not_called()


# ---------------------------------------------------------------------------
# i_will_work_on: claim() raises Exception → invalid_state (lines 369-378)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_will_work_on_pending_claim_raises_returns_invalid_state() -> None:
    """When the runner re-raises a RuntimeError from claim(), the verb body
    catches it and surfaces an invalid_state envelope with the runner's
    message. Pre-spec the verb body produced "claim failed during
    finalization"; the spec-driven body produces "verb runner failed:
    <exc>" so the agent still gets a remediation hint instead of a 500.
    """
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = _wire_dev_task_svc(task_id, status="pending")
    task_svc.claim.side_effect = RuntimeError("workspace down")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    env = await c.i_will_work_on(agent_id, task_id, plan="plan", steps=_STEPS)
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "verb runner failed" in body["message"]
    assert "workspace down" in body["message"]


@pytest.mark.asyncio
async def test_i_will_work_on_pending_claim_returns_none_invalid_state() -> None:
    """Lines 379-384: claim returns None → invalid_state."""
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = _wire_dev_task_svc(task_id, status="pending")
    task_svc.claim.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    env = await c.i_will_work_on(agent_id, task_id, plan="plan", steps=_STEPS)
    body = env.as_dict()
    assert body["error"] == "invalid_state"


@pytest.mark.asyncio
async def test_i_will_work_on_pending_no_plan_tracing_gap() -> None:
    """Lines 385-393: pending task, no plan → tracing_gap."""
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = _wire_dev_task_svc(task_id, status="pending", assigned_to=agent_id)
    claimed_task = MagicMock(
        status="pending",
        assigned_to=agent_id,
        plan=None,
        id=task_id,
        title="t",
        task_type="code",
    )
    task_svc.claim.return_value = claimed_task
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    env = await c.i_will_work_on(agent_id, task_id, plan=None, steps=_STEPS)
    body = env.as_dict()
    assert body["error"] == "tracing_gap"


@pytest.mark.asyncio
async def test_i_will_work_on_start_returns_none_invalid_state() -> None:
    """Lines 396-398: start returns None → start_failed_envelope."""
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = _wire_dev_task_svc(task_id, status="pending", assigned_to=agent_id)
    claimed_task = MagicMock(
        status="pending",
        assigned_to=agent_id,
        plan="some plan",
        id=task_id,
        title="t",
        task_type="code",
    )
    task_svc.claim.return_value = claimed_task
    task_svc.set_plan.return_value = claimed_task
    task_svc.start.return_value = None  # start fails
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    env = await c.i_will_work_on(agent_id, task_id, plan="ok plan", steps=_STEPS)
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "start failed" in body["message"]


# ---------------------------------------------------------------------------
# _i_will_work_on_needs_revision: claim returns None → invalid_state (lines 427-433)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_needs_revision_branch_claim_fails_invalid_state() -> None:
    """Lines 427-433: needs_revision, not assigned, claim fails → invalid_state."""
    agent_id = uuid4()
    task_id = uuid4()
    other_id = uuid4()
    task_svc = _wire_dev_task_svc(
        task_id, status="needs_revision", assigned_to=other_id, plan="p"
    )
    task_svc.claim.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    env = await c.i_will_work_on(agent_id, task_id, plan="ok", steps=_STEPS)
    body = env.as_dict()
    assert body["error"] == "invalid_state"


@pytest.mark.asyncio
async def test_needs_revision_branch_start_fails() -> None:
    """Line 436: start returns None in needs_revision branch."""
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = _wire_dev_task_svc(
        task_id, status="needs_revision", assigned_to=agent_id, plan="p"
    )
    task_svc.start.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    env = await c.i_will_work_on(agent_id, task_id, plan="ok", steps=_STEPS)
    body = env.as_dict()
    assert body["error"] == "invalid_state"


# ---------------------------------------------------------------------------
# _i_will_work_on_claimed: start fails → start_failed_envelope (line 454)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claimed_branch_returns_start_failed() -> None:
    """Line 454: start fails in claimed branch."""
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = _wire_dev_task_svc(
        task_id, status="claimed", assigned_to=agent_id, plan="p"
    )
    task_svc.start.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    env = await c.i_will_work_on(agent_id, task_id, plan="ok", steps=_STEPS)
    body = env.as_dict()
    assert body["error"] == "invalid_state"


# ---------------------------------------------------------------------------
# i_will_work_on with in_progress assigned to self → idempotent (line 491)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_will_work_on_in_progress_assigned_to_self_idempotent() -> None:
    """Line 491: in_progress assigned_to=agent → idempotent re-entry pass through."""
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = _wire_dev_task_svc(
        task_id, status="in_progress", assigned_to=agent_id, plan="p"
    )
    task_svc.heartbeat = AsyncMock()
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    env = await c.i_will_work_on(agent_id, task_id, plan="ok", steps=_STEPS)
    body = env.as_dict()
    # No error — re-entry pass.
    assert "error" not in body or body.get("error") is None


# ---------------------------------------------------------------------------
# i_will_plan: pending claim returns None (line 1155)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_will_plan_pm_with_already_active_task_rejects() -> None:
    """The already_active_guard still fires on i_will_plan even though
    pm_cannot_execute_code is skipped. Covers _impl.py:1106-1108
    (with-briefing wrap of the guard rejection).
    """
    pm_id = uuid4()
    task_id = uuid4()
    other_task_id = uuid4()
    target = MagicMock(
        status="pending",
        assigned_to=pm_id,
        plan=None,
        id=task_id,
        title="t",
        team="backend",
        parent_task_id=None,
        task_type="planning",
    )
    busy_task = MagicMock(id=other_task_id, status="in_progress")
    task_svc = AsyncMock()
    task_svc.get.return_value = target
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.list_in_progress_for_agent.return_value = [busy_task]
    task_svc.list_paused_for_agent.return_value = []
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    env = await c.i_will_plan(
        pm_id,
        task_id,
        plan="x" * 30,
        rich_plan={
            "approach": (
                "Decompose the planning task into backend and frontend "
                "developer-claimable subtasks. Backend lands first, QA "
                "reviews each PR after it opens, documentation follows, then "
                "complete and submit up. Strict sequencing with no cross-cell "
                "dependencies beyond the stated ordering."
            ),
            "sub_tasks": [
                {
                    "title": "Slice A",
                    "description": (
                        "be-dev-1 implements the backend API change with "
                        "tests and opens the leaf PR for QA review."
                    ),
                }
            ],
        },
    )
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "in_progress task" in body["message"]


@pytest.mark.asyncio
async def test_i_will_plan_cell_pm_on_code_typed_parent_succeeds() -> None:
    """Regression for the smoke-test deadlock (2026-05-08 trace).

    When a cell PM tries to plan a code-typed parent task, the verb must
    succeed — PMs PLAN code work and DELEGATE the execution; they don't
    execute. The pre-fix `pm_cannot_execute_code_guard` was wrongly fired
    on `i_will_plan` (the planning verb) instead of being scoped to
    `i_will_work_on` (the execution verb), causing a deadlock: cell PM
    couldn't plan → couldn't transition parent to in_progress → couldn't
    delegate (delegate requires parent in_progress).
    """
    pm_id = uuid4()
    task_id = uuid4()
    task = MagicMock(
        status="pending",
        assigned_to=pm_id,
        plan=None,
        id=task_id,
        title="Backend slice: Git workflow smoke test",
        team="backend",
        parent_task_id=uuid4(),  # subtask of the main_pm root
        task_type="code",  # ← the trigger; pre-fix this rejected with
        #                    "Cell Pm cannot claim code tasks"
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.get_subtasks.return_value = []
    # Claim + start succeed so we can verify the verb runs end-to-end.
    started_task = MagicMock(
        status="in_progress",
        assigned_to=pm_id,
        id=task_id,
        title=task.title,
        team="backend",
        task_type="code",
    )
    task_svc.claim.return_value = task
    task_svc.start.return_value = started_task
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    env = await c.i_will_plan(
        pm_id,
        task_id,
        plan="Decompose into 2 dev subtasks.",
        rich_plan={
            "approach": (
                "Split code-typed parent into developer-claimable subtasks: "
                "one for API, one for test coverage validation."
            ),
            "sub_tasks": [
                {"title": "API subtask", "description": "Implement the endpoint"},
            ],
        },
    )
    body = env.as_dict()
    # The PM-cannot-execute-code rejection must NOT fire on i_will_plan.
    assert body.get("error") != "not_authorized", (
        f"i_will_plan was rejected for a code-typed parent; envelope: {body}"
    )


@pytest.mark.asyncio
async def test_i_will_plan_pending_claim_fails() -> None:
    pm_id = uuid4()
    task_id = uuid4()
    task = MagicMock(
        status="pending",
        assigned_to=pm_id,
        plan=None,
        id=task_id,
        title="t",
        team="backend",
        parent_task_id=None,
        task_type="planning",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.get_subtasks.return_value = []
    task_svc.claim.return_value = None  # claim fails
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    env = await c.i_will_plan(
        pm_id,
        task_id,
        plan="my plan that is long enough",
        rich_plan={
            "approach": (
                "Decompose the planning task into backend and frontend "
                "developer-claimable subtasks. Backend lands first, QA "
                "reviews each PR after it opens, documentation follows, then "
                "complete and submit up. Strict sequencing with no cross-cell "
                "dependencies beyond the stated ordering."
            ),
            "sub_tasks": [
                {
                    "title": "Slice A",
                    "description": (
                        "be-dev-1 implements the backend API change with "
                        "tests and opens the leaf PR for QA review."
                    ),
                }
            ],
        },
    )
    body = env.as_dict()
    assert body["error"] == "invalid_state"


# ---------------------------------------------------------------------------
# delegate: parent not found (line 1208)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegate_parent_not_found() -> None:
    pm_id = uuid4()
    parent_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    env = await c.delegate(
        pm_id,
        parent_id,
        DelegateInputs(
            title="Implement endpoint",
            description="Add /v1/foo endpoint with passing tests please",
            assigned_to="be-dev-1",
            team="backend",
            task_type="code",
            nature="technical",
            acceptance_criteria=["GET /v1/foo returns 200 with body"],
        ),
    )
    body = env.as_dict()
    assert body["error"] == "not_found"


# ---------------------------------------------------------------------------
# _delegate_role_guards: unknown role rejection (line 1271)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegate_unknown_role_rejected() -> None:
    pm_id = uuid4()
    parent_id = uuid4()
    parent = MagicMock(
        status="in_progress",
        assigned_to=pm_id,
        project_id=uuid4(),
        title="p",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = parent
    task_svc.agent_for.return_value = MagicMock(role="developer", team="backend")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    env = await c.delegate(
        pm_id,
        parent_id,
        DelegateInputs(
            title="Implement endpoint",
            description="Add /v1/foo endpoint with passing tests please",
            assigned_to="be-dev-1",
            team="backend",
            task_type="code",
            nature="technical",
            acceptance_criteria=["GET /v1/foo returns 200 with body"],
        ),
    )
    body = env.as_dict()
    assert body["error"] == "not_authorized"


# ---------------------------------------------------------------------------
# _delegate_static_guards: unknown agent slug → invalid_state (line 1300)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegate_parent_no_project_rejected() -> None:
    """Line 1306: parent.project_id is None → invalid_state."""
    pm_id = uuid4()
    parent_id = uuid4()
    parent = MagicMock(
        status="in_progress",
        assigned_to=pm_id,
        project_id=None,
        title="p",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = parent
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    env = await c.delegate(
        pm_id,
        parent_id,
        DelegateInputs(
            title="Implement endpoint",
            description="Add /v1/foo endpoint with passing tests please",
            assigned_to="be-dev-1",
            team="backend",
            task_type="code",
            nature="technical",
            acceptance_criteria=["GET /v1/foo returns 200 with body"],
        ),
    )
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "no project_id" in body["message"]


# ---------------------------------------------------------------------------
# _validate_delegation_chain: unknown role → "role X cannot delegate" (line 1446)
# ---------------------------------------------------------------------------


def test_validate_delegation_chain_unknown_role() -> None:
    deps = _make_deps()
    c = Choreographer(deps)
    err = c._validate_delegation_chain("auditor", "be-dev-1")
    assert err is not None
    assert "auditor" in err


# ---------------------------------------------------------------------------
# submit_up: task not found (line 1458)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_up_task_not_found() -> None:
    pm_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    env = await c.submit_up(pm_id, task_id, notes="x" * 30)
    body = env.as_dict()
    assert body["error"] == "not_found"


# ---------------------------------------------------------------------------
# submit_up: submit_pm_review returns None (line 1477)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_up_submit_pm_review_fails() -> None:
    pm_id = uuid4()
    task_id = uuid4()
    task = MagicMock(
        status="in_progress",
        assigned_to=pm_id,
        branch_name="feature/backend/abc",
        pr_number=None,
        title="t",
        team="backend",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.all_subtasks_terminal.return_value = True
    task_svc.submit_pm_review.return_value = None  # service returns None
    journal = AsyncMock()
    journal.has_decision_for_task.return_value = True
    journal.latest_decision_at.return_value = datetime.now(UTC)
    git = AsyncMock()
    git.create_pr = AsyncMock()
    deps = _make_deps(task=task_svc, journal=journal, git=git)
    c = Choreographer(deps)
    env = await c.submit_up(pm_id, task_id, notes="x" * 30)
    body = env.as_dict()
    assert body["error"] == "invalid_state"


# ---------------------------------------------------------------------------
# _submit_up_ownership_guard wrong role (line 1520)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_up_wrong_role_rejected() -> None:
    pm_id = uuid4()
    task_id = uuid4()
    task = MagicMock(
        status="in_progress",
        assigned_to=pm_id,
        title="t",
        team="backend",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = MagicMock(role="main_pm", team=None)
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    env = await c.submit_up(pm_id, task_id, notes="x" * 30)
    body = env.as_dict()
    assert body["error"] == "not_authorized"


# ---------------------------------------------------------------------------
# _submit_up_state_guard: no branch_name (line 1556)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_up_no_branch_rejected() -> None:
    pm_id = uuid4()
    task_id = uuid4()
    task = MagicMock(
        status="in_progress",
        assigned_to=pm_id,
        branch_name=None,
        pr_number=None,
        title="t",
        team="backend",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.all_subtasks_terminal.return_value = True
    journal = AsyncMock()
    journal.has_decision_for_task.return_value = True
    journal.latest_decision_at.return_value = datetime.now(UTC)
    deps = _make_deps(task=task_svc, journal=journal)
    c = Choreographer(deps)
    env = await c.submit_up(pm_id, task_id, notes="x" * 30)
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "no branch" in body["message"]


# ---------------------------------------------------------------------------
# _pm_next_hint: each branch (lines 1612-1616)
# ---------------------------------------------------------------------------


def test_pm_next_hint_pending() -> None:
    deps = _make_deps()
    c = Choreographer(deps)
    hint = c._pm_next_hint("pending", "tid")
    assert "i_will_plan" in hint


def test_pm_next_hint_paused() -> None:
    deps = _make_deps()
    c = Choreographer(deps)
    hint = c._pm_next_hint("paused", "tid")
    assert "subtasks" in hint or "complete" in hint


def test_pm_next_hint_blocked() -> None:
    deps = _make_deps()
    c = Choreographer(deps)
    hint = c._pm_next_hint("blocked", "tid")
    assert "unblock" in hint


def test_pm_next_hint_awaiting_pm_review() -> None:
    deps = _make_deps()
    c = Choreographer(deps)
    hint = c._pm_next_hint("awaiting_pm_review", "tid")
    assert "complete" in hint


def test_pm_next_hint_unknown_status() -> None:
    deps = _make_deps()
    c = Choreographer(deps)
    hint = c._pm_next_hint("unknown_status", "tid")
    assert "unknown_status" in hint


# ---------------------------------------------------------------------------
# triage_all main_pm — awaiting Main PM tasks branch (lines 1662-1663)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_triage_all_returns_awaiting_main_pm_when_no_blocked() -> None:
    pm_id = uuid4()
    awaiting_task = MagicMock(
        id=uuid4(), status="awaiting_pm_review", title="x", team="backend"
    )
    task_svc = AsyncMock()
    task_svc.list_blocked_all_teams.return_value = []
    task_svc.list_awaiting_main_pm_all.return_value = [awaiting_task]
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    env = await c.triage_all(pm_id)
    body = env.as_dict()
    assert body["task_id"] == str(awaiting_task.id)
    assert "complete" in body["next"]


# ---------------------------------------------------------------------------
# unblock: task not found (line 1682)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unblock_task_not_found() -> None:
    pm_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    env = await c.unblock(pm_id, task_id)
    body = env.as_dict()
    assert body["error"] == "not_found"


# ---------------------------------------------------------------------------
# _cell_pm_complete_guard: wrong status (line 1743)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cell_pm_complete_wrong_status() -> None:
    """Line 1743: status not awaiting_pm_review."""
    pm_id = uuid4()
    task_id = uuid4()
    task = MagicMock(
        status="in_progress",
        assigned_to=pm_id,
        title="t",
        team="backend",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    env = await c.cell_pm_complete(pm_id, task_id, notes="x" * 30)
    body = env.as_dict()
    assert body["error"] == "invalid_state"


# ---------------------------------------------------------------------------
# cell_pm_complete: task not found (line 1782)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cell_pm_complete_not_found() -> None:
    pm_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    env = await c.cell_pm_complete(pm_id, task_id, notes="x" * 30)
    body = env.as_dict()
    assert body["error"] == "not_found"


# ---------------------------------------------------------------------------
# _maybe_advance_parent_to_pm_review: silent skips (lines 1834, 1840, 1843)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maybe_advance_parent_skips_when_parent_missing() -> None:
    parent_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    await c._maybe_advance_parent_to_pm_review(parent_id, "backend")
    task_svc.reassign.assert_not_called()


@pytest.mark.asyncio
async def test_maybe_advance_parent_skips_when_subtasks_not_terminal() -> None:
    parent_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = MagicMock(team="backend")
    task_svc.all_subtasks_terminal.return_value = False
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    await c._maybe_advance_parent_to_pm_review(parent_id, "backend")
    task_svc.reassign.assert_not_called()


@pytest.mark.asyncio
async def test_maybe_advance_parent_skips_when_no_team() -> None:
    parent_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = MagicMock(team=None)
    task_svc.all_subtasks_terminal.return_value = True
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    # leaf_team also None → triggers line 1840 short-circuit.
    await c._maybe_advance_parent_to_pm_review(parent_id, None)
    task_svc.reassign.assert_not_called()


@pytest.mark.asyncio
async def test_maybe_advance_parent_skips_when_no_pm_for_team() -> None:
    parent_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = MagicMock(team="backend")
    task_svc.all_subtasks_terminal.return_value = True
    task_svc.cell_pm_for_team.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    await c._maybe_advance_parent_to_pm_review(parent_id, "backend")
    task_svc.reassign.assert_not_called()


# ---------------------------------------------------------------------------
# _main_pm_complete_guard: not assigned (1851), wrong status (1859)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_main_pm_complete_not_assigned() -> None:
    main_pm_id = uuid4()
    other_id = uuid4()
    task_id = uuid4()
    task = MagicMock(
        status="awaiting_pm_review",
        assigned_to=other_id,
        parent_task_id=None,
        title="t",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    env = await c.main_pm_complete(main_pm_id, task_id, notes="x" * 30)
    body = env.as_dict()
    assert body["error"] == "not_authorized"


@pytest.mark.asyncio
async def test_main_pm_complete_wrong_status() -> None:
    # #183: in_progress is now an accepted source (root resumed from paused);
    # use paused — a genuinely non-completable status — to exercise the guard.
    main_pm_id = uuid4()
    task_id = uuid4()
    task = MagicMock(
        status="paused",
        assigned_to=main_pm_id,
        parent_task_id=None,
        title="t",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    env = await c.main_pm_complete(main_pm_id, task_id, notes="x" * 30)
    body = env.as_dict()
    assert body["error"] == "invalid_state"


# ---------------------------------------------------------------------------
# _main_pm_complete_guard: missing decision journal (lines 1885-1889)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_main_pm_complete_missing_journal_decision() -> None:
    main_pm_id = uuid4()
    task_id = uuid4()
    task = MagicMock(
        status="awaiting_pm_review",
        assigned_to=main_pm_id,
        parent_task_id=None,
        title="t",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    journal = AsyncMock()
    journal.has_decision_for_task.return_value = False
    journal.latest_decision_at.return_value = None
    deps = _make_deps(task=task_svc, journal=journal)
    c = Choreographer(deps)
    env = await c.main_pm_complete(main_pm_id, task_id, notes="x" * 30)
    body = env.as_dict()
    assert body["error"] == "tracing_gap"


# ---------------------------------------------------------------------------
# main_pm_complete: not_found (line 1908)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_main_pm_complete_not_found() -> None:
    main_pm_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    env = await c.main_pm_complete(main_pm_id, task_id, notes="x" * 30)
    body = env.as_dict()
    assert body["error"] == "not_found"


# ---------------------------------------------------------------------------
# _emit_rejection: correlation_id from contextvars (line 170)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_rejection_includes_correlation_id() -> None:
    """When structlog contextvars holds a correlation_id, it gets stamped."""
    deps = _make_deps()
    c = Choreographer(deps)
    rejection = Envelope.invalid_state(message="m", remediate="r", context_briefing={})
    structlog.contextvars.bind_contextvars(correlation_id="cid-123")
    try:
        await c._emit_rejection(rejection, agent_id=uuid4(), task_id=None, verb="x")
    finally:
        structlog.contextvars.unbind_contextvars("correlation_id")
    deps.audit.log_event.assert_awaited()
    call_kwargs = deps.audit.log_event.await_args.kwargs
    assert call_kwargs["details"]["correlation_id"] == "cid-123"


# ---------------------------------------------------------------------------
# _i_will_work_on_claimed: guard returns (line 451) — already-active blocker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claimed_branch_already_active_guard() -> None:
    """Line 451: in_progress task elsewhere blocks claim of another."""
    agent_id = uuid4()
    task_id = uuid4()
    other_id = uuid4()
    in_prog = MagicMock(id=other_id, status="in_progress", title="other")
    task_svc = _wire_dev_task_svc(
        task_id, status="claimed", assigned_to=agent_id, plan="p"
    )
    task_svc.list_in_progress_for_agent.return_value = [in_prog]
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    env = await c.i_will_work_on(agent_id, task_id, plan="ok", steps=_STEPS)
    body = env.as_dict()
    assert body["error"] == "invalid_state"


# ---------------------------------------------------------------------------
# Pure helper: skill matching string entries (lines 802-803)
# ---------------------------------------------------------------------------


def test_resolve_skill_string_entries() -> None:
    deps = _make_deps()
    c = Choreographer(deps)
    agent = MagicMock(skills=["python", "rust"], capabilities=None)
    result = c._resolve_skill(agent, ["go", "python"])
    assert result == "python"


# ---------------------------------------------------------------------------
# i_will_plan: claim returns None for pending task (line 1155 — emit_rejection)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_will_plan_pending_claim_returns_none_emit_rejection() -> None:
    """When claim() returns None inside the runner, the savepoint rolls
    back and the runner-failure path surfaces as invalid_state. Pre-spec
    this branched into a hand-rolled "claim failed" message; now it is
    emitted via _claim_plan_start_run's exception handler.
    """
    pm_id = uuid4()
    task_id = uuid4()
    task = MagicMock(
        status="pending",
        assigned_to=pm_id,
        plan=None,
        id=task_id,
        title="t",
        team="backend",
        parent_task_id=None,
        task_type="planning",
        quick_context=None,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = MagicMock(
        id=pm_id, role="cell_pm", team="backend", slug=None
    )
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.get_subtasks.return_value = []
    task_svc.claim.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    env = await c.i_will_plan(
        pm_id,
        task_id,
        plan="my plan that is long enough",
        rich_plan={
            "approach": (
                "Decompose the planning task into backend and frontend "
                "developer-claimable subtasks. Backend lands first, QA "
                "reviews each PR after it opens, documentation follows, then "
                "complete and submit up. Strict sequencing with no cross-cell "
                "dependencies beyond the stated ordering."
            ),
            "sub_tasks": [
                {
                    "title": "Slice A",
                    "description": (
                        "be-dev-1 implements the backend API change with "
                        "tests and opens the leaf PR for QA review."
                    ),
                }
            ],
        },
    )
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "verb runner failed" in body["message"]


# ---------------------------------------------------------------------------
# _submit_up_ownership_guard: not_assigned (line 1520)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_up_not_assigned_rejected() -> None:
    """Line 1520: cell_pm calling submit_up but task assigned to another agent."""
    pm_id = uuid4()
    task_id = uuid4()
    other_id = uuid4()
    task = MagicMock(
        status="in_progress",
        assigned_to=other_id,
        title="t",
        team="backend",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    env = await c.submit_up(pm_id, task_id, notes="x" * 30)
    body = env.as_dict()
    assert body["error"] == "not_authorized"
    assert "not assigned" in body["message"]


# ---------------------------------------------------------------------------
# Task 3: Envelope introspection — verb returns carry current_state +
# valid_next_verbs so agents stop trial-and-erroring.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_will_work_on_envelope_carries_introspection_on_success() -> None:
    """Successful claim+start path stamps current_state + valid_next_verbs."""
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = _wire_dev_task_svc(task_id, status="pending", assigned_to=agent_id)
    claimed_task = MagicMock(
        status="in_progress",
        assigned_to=agent_id,
        plan="ok plan",
        id=task_id,
        title="t",
        task_type="code",
    )
    task_svc.claim.return_value = claimed_task
    task_svc.set_plan.return_value = claimed_task
    task_svc.start.return_value = claimed_task
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    env = await c.i_will_work_on(agent_id, task_id, plan="ok plan", steps=_STEPS)
    body = env.as_dict()
    assert body["error"] is None
    assert body["current_state"] == "in_progress"
    assert isinstance(body["valid_next_verbs"], list)
    # `valid_next_verbs` lists lifecycle INTENT verbs; `commit` is a
    # content tool (do_server), not an intent, so the canonical spec
    # excludes it. `open_pr` and `i_am_done` are the in_progress intents.
    assert "open_pr" in body["valid_next_verbs"]
    assert "i_am_done" in body["valid_next_verbs"]


@pytest.mark.asyncio
async def test_i_will_work_on_envelope_carries_introspection_on_rejection() -> None:
    """A wrong-state rejection still stamps current_state + valid_next_verbs
    so the agent learns what verbs are actually valid right now."""
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = _wire_dev_task_svc(task_id, status="completed", assigned_to=agent_id)
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    env = await c.i_will_work_on(agent_id, task_id, plan="x", steps=_STEPS)
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert body["current_state"] == "completed"
    assert isinstance(body["valid_next_verbs"], list)
    # Lifecycle verbs are NOT in the list for a completed task.
    assert "i_will_work_on" not in body["valid_next_verbs"]


@pytest.mark.asyncio
async def test_open_pr_does_not_create_pr_if_no_commits() -> None:
    """Atomic invariant: if commits[] is empty, open_pr must NOT call
    git.create_pr. Pre-fix this was already true at the verb level, but
    this test pins it as a regression: any future refactor that
    re-orders precondition vs side effect breaks the test."""
    dev_id = uuid4()
    task_id = uuid4()
    task = MagicMock(
        status="in_progress",
        assigned_to=dev_id,
        commits=[],
        pr_number=None,
        branch_name="feature/backend/abc",
        id=task_id,
        title="t",
        team="backend",
        task_type="code",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = MagicMock(role="developer", team="backend")
    git_svc = AsyncMock()
    git_svc.create_pr = AsyncMock()
    git_svc.push_branch = AsyncMock()
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)
    env = await c.open_pr(dev_id, task_id)
    body = env.as_dict()
    # Spec's PRECONDITION_COMMITS now produces tracing_gap rather than the
    # previous bespoke invalid_state. The atomicity invariant the test
    # pins (no git side effect when commits=[]) is unchanged.
    assert body["error"] == "tracing_gap"
    assert body["missing"] == ["commits>=1"]
    git_svc.create_pr.assert_not_called()
    git_svc.push_branch.assert_not_called()


@pytest.mark.asyncio
async def test_i_will_work_on_missing_plan_does_not_claim_pending_task() -> None:
    """Atomic invariant (Task 5 pattern, Bug A from 2026-05-09 smoke):
    if `plan` is missing on the FIRST i_will_work_on call against a
    pending task, the task must NOT be claimed. Pre-fix the verb ran
    claim() BEFORE checking plan, leaving the task in `claimed` with
    no plan — and `_i_will_work_on_claimed` had no recovery path so
    the agent looped forever on `start failed`.
    """
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = _wire_dev_task_svc(task_id, status="pending")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    env = await c.i_will_work_on(agent_id, task_id, plan=None, steps=_STEPS)
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "plan" in body["missing"]
    (
        task_svc.claim.assert_not_called(),
        ("claim() ran before plan precondition was satisfied — atomicity broken"),
    )


@pytest.mark.asyncio
async def test_i_will_work_on_claimed_with_no_plan_accepts_recovery_plan() -> None:
    """Recovery path (Bug A from 2026-05-09 smoke): if the task is in
    `claimed` state without a plan (e.g. from a prior partial-claim race
    or an orchestrator restart), a fresh i_will_work_on call WITH plan
    must set the plan and then start, not just call start() against a
    plan-less task.
    """
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = _wire_dev_task_svc(
        task_id, status="claimed", assigned_to=agent_id, plan=None
    )
    started = MagicMock(
        status="in_progress",
        assigned_to=agent_id,
        plan="recovery plan",
        id=task_id,
        title="t",
        task_type="code",
    )
    task_svc.set_plan.return_value = started
    task_svc.start.return_value = started
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    env = await c.i_will_work_on(agent_id, task_id, plan="recovery plan", steps=_STEPS)
    body = env.as_dict()
    assert body["error"] is None, f"expected success, got {body}"
    task_svc.set_plan.assert_awaited_once()
