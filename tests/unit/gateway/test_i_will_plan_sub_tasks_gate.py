"""Direct choreographer-layer unit tests for _pm_sub_tasks_gate.

Critical #2: the HTTP-layer test (test_i_will_plan_rich_required.py) mocks
the choreographer and only exercises Pydantic validation. These tests call
Choreographer.i_will_plan() directly so the gate logic in _pm_sub_tasks_gate
is exercised at the correct layer.

Also covers Important #1: approach is now enforced inside the gate (not only
at the HTTP Pydantic boundary) so direct service-layer callers (MCP, test
fixtures, orchestrator-internal) cannot bypass it.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps

_MIN_APPROACH_LEN = 20


# ---------------------------------------------------------------------------
# Shared fixture helpers — same pattern as test_choreographer_pm_extras.py
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
    return ChoreographerDeps(**base)


def _pm_task_svc(task_id: object, *, role: str = "cell_pm") -> AsyncMock:
    """Build a TaskService mock for a PM caller with a pending planning task."""
    task_svc = AsyncMock()
    task_svc.get.return_value = MagicMock(
        id=task_id,
        status="pending",
        plan=None,
        assigned_to=None,
        task_type="planning",
        parent_task_id=None,
        sequence=0,
        team="backend",
        commits=[],
        pr_number=None,
        branch_name=None,
        quick_context=None,
    )
    task_svc.agent_for.return_value = MagicMock(
        id=uuid4(), role=role, team="backend", slug=None
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


# ---------------------------------------------------------------------------
# Test 1: PM with empty sub_tasks → incomplete_input (sub_tasks in missing)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pm_with_empty_sub_tasks_gets_incomplete_input() -> None:
    """cell_pm with rich_plan.sub_tasks=[] must be rejected by _pm_sub_tasks_gate.

    The gate fires for PM roles when sub_tasks is absent or empty; the returned
    envelope must carry error='incomplete_input' with 'sub_tasks' in missing.
    """
    pm_id = uuid4()
    task_id = uuid4()
    task_svc = _pm_task_svc(task_id, role="cell_pm")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_plan(
        pm_id,
        task_id,
        plan="decompose work",
        rich_plan={
            "approach": "Single-cell decomposition covering the full vertical slice.",
            "sub_tasks": [],  # empty — gate must fire
        },
    )
    body = env.as_dict()
    assert body["error"] == "incomplete_input", body
    assert "sub_tasks" in (body.get("missing") or []), body


# ---------------------------------------------------------------------------
# Test 2: PM with rich_plan=None → incomplete_input
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pm_with_missing_rich_plan_gets_incomplete_input() -> None:
    """main_pm passing rich_plan=None must be rejected by _pm_sub_tasks_gate.

    The gate treats missing rich_plan the same as missing sub_tasks: the
    envelope must carry error='incomplete_input' with 'sub_tasks' in missing.
    """
    pm_id = uuid4()
    task_id = uuid4()
    task_svc = _pm_task_svc(task_id, role="main_pm")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_plan(
        pm_id,
        task_id,
        plan="decompose work",
        rich_plan=None,  # not supplied at all
    )
    body = env.as_dict()
    assert body["error"] == "incomplete_input", body
    assert "sub_tasks" in (body.get("missing") or []), body


# ---------------------------------------------------------------------------
# Test 3: PM with filled sub_tasks → gate passes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pm_with_filled_sub_tasks_passes_gate() -> None:
    """cell_pm with at least one sub_task must NOT be rejected by the gate.

    The call may still fail on downstream gates (spec lifecycle, journal
    decision), but the sub_tasks gate itself must not fire. We assert
    error != 'incomplete_input' and that the gate did not block.
    """
    pm_id = uuid4()
    task_id = uuid4()
    task_svc = _pm_task_svc(task_id, role="cell_pm")
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
    task_svc.claim.return_value = claimed
    task_svc.set_plan.return_value = claimed
    task_svc.start.return_value = started
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_plan(
        pm_id,
        task_id,
        plan="decompose work",
        rich_plan={
            "approach": "Three-cell decomposition: backend, frontend, ux.",
            "sub_tasks": [{"title": "Backend slice", "description": "API + DB"}],
        },
    )
    body = env.as_dict()
    # The gate must not have fired; the call may fail on other checks but not
    # with incomplete_input from the sub_tasks gate.
    assert body.get("error") != "incomplete_input", body


# ---------------------------------------------------------------------------
# Test 4: Developer with empty sub_tasks → gate does NOT fire
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_developer_with_empty_sub_tasks_passes_gate() -> None:
    """Developers are not required to supply sub_tasks; the gate must skip them.

    A developer calling i_will_plan with an empty sub_tasks list should NOT
    get incomplete_input from _pm_sub_tasks_gate. The gate is PM-only;
    developers' plans are execution-shaped.
    """
    dev_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    code_task = MagicMock(
        id=task_id,
        status="pending",
        plan=None,
        assigned_to=None,
        task_type="code",
        parent_task_id=None,
        sequence=0,
        team="backend",
        commits=[],
        pr_number=None,
        branch_name=None,
        quick_context=None,
    )
    task_svc.get.return_value = code_task
    task_svc.agent_for.return_value = MagicMock(
        id=dev_id, role="developer", team="backend", slug=None
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
    claimed = MagicMock(
        id=task_id,
        status="claimed",
        plan=None,
        assigned_to=dev_id,
        task_type="code",
    )
    started = MagicMock(
        id=task_id,
        status="in_progress",
        plan={"text": "x"},
        assigned_to=dev_id,
        task_type="code",
    )
    task_svc.claim.return_value = claimed
    task_svc.set_plan.return_value = claimed
    task_svc.start.return_value = started
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_plan(
        dev_id,
        task_id,
        plan="implement the feature",
        rich_plan={
            "approach": "step-by-step TDD approach for the feature.",
            "sub_tasks": [],
        },
    )
    body = env.as_dict()
    # Gate must NOT fire for developers.
    assert body.get("error") != "incomplete_input", body


# ---------------------------------------------------------------------------
# Test 5 (Important #1): PM with sub_tasks filled but approach missing → rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pm_with_sub_tasks_but_missing_approach_gets_incomplete_input() -> None:
    """cell_pm with non-empty sub_tasks but no approach must be rejected.

    The gate enforces approach (>= _MIN_APPROACH_LEN chars) as well as
    sub_tasks. Direct service-layer callers bypass Pydantic so the gate is
    the last line of defense.
    """
    pm_id = uuid4()
    task_id = uuid4()
    task_svc = _pm_task_svc(task_id, role="cell_pm")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_plan(
        pm_id,
        task_id,
        plan="decompose work",
        rich_plan={
            "approach": "",  # empty — gate must fire
            "sub_tasks": [{"title": "Backend slice", "description": "API + DB"}],
        },
    )
    body = env.as_dict()
    assert body["error"] == "incomplete_input", body
    assert "approach" in (body.get("missing") or []), body


# ---------------------------------------------------------------------------
# Test 6 (Critical #1 ordering): re-entry by in_progress PM short-circuits
# BEFORE the gate, even with thin args (no sub_tasks)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pm_reentry_in_progress_short_circuits_before_gate() -> None:
    """A PM whose container crashed re-calls i_will_plan with thin args (no sub_tasks).

    If the task is already in_progress and assigned to this PM, the idempotent
    re-entry path must fire and return OK before _pm_sub_tasks_gate is reached.
    Without the ordering fix the gate would fire first and reject with
    incomplete_input, breaking crash recovery.
    """
    pm_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    in_progress_task = MagicMock(
        id=task_id,
        status="in_progress",
        plan={"text": "already set"},
        assigned_to=pm_id,  # same PM — triggers re-entry
        task_type="planning",
        parent_task_id=None,
        sequence=0,
        team="backend",
        commits=[],
        pr_number=None,
        branch_name="feature/backend/abc",
        quick_context=None,
    )
    task_svc.get.return_value = in_progress_task
    task_svc.agent_for.return_value = MagicMock(
        id=pm_id, role="cell_pm", team="backend", slug=None
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
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    # Thin args: just plan="resume", no sub_tasks — as a respawned PM would send.
    env = await c.i_will_plan(
        pm_id,
        task_id,
        plan="resume",
        rich_plan=None,  # no sub_tasks because PM is resuming, not planning
    )
    body = env.as_dict()
    # Re-entry must short-circuit to OK; incomplete_input means gate fired first.
    assert body.get("error") is None, (
        f"Expected OK (re-entry short-circuit) but got "
        f"error={body.get('error')!r}. "
        "The gate is firing before _handle_pm_reentry — ordering bug not fixed."
    )
    assert body.get("status") == "in_progress", body
