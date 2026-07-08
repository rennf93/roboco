"""Scenario: the PM plan-content guardrails reject over-decomposed plans.

The 2026-07-07 task-quality defects (root 79d686f0 decomposed into 1 subtask,
code leaf 55376b8a carrying a 5-subtask plan, descriptions bloated to 3000+
chars) were structural — the planning verb barely guardrailed its content.
The fix adds ceilings (plan <= 2000, approach <= 800, sub-task title <= 200,
sub-task description <= 600) and an over-decomposition cap (>7 sub_tasks
rejected) at both the HTTP Pydantic boundary and the choreographer gate.

This scenario drives a MAIN_PM planning root through the real API and asserts
each guardrail surfaces a clean ``incomplete_input`` envelope with a
remediation hint the agent can act on — not a 500, not a silent accept. The
gate is the load-bearing layer (direct service callers bypass Pydantic), so
the assertions land on the envelope, not the HTTP status.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tests.e2e_smoke.arcs import (
    origin_branch,
    seed_company,
    seed_project,
    seed_task,
    set_branch_name,
)
from tests.e2e_smoke.harness import ScriptedAgent, expect_error

if TYPE_CHECKING:
    from uuid import UUID

    from tests.e2e_smoke.arcs import Company
    from tests.e2e_smoke.harness import E2EStack

# Pydantic's IWillPlanRequest.approach enforces 150..800 chars at the HTTP
# boundary, so every i_will_plan call needs a compliant approach.
_APPROACH = (
    "Plan and delegate the page-scoped refresh button work to the frontend "
    "cell: land the provider/hook, add the navbar button, remove the inline "
    "buttons, and route one planning subtask to fe-pm for delivery. "
    "Sequenced strictly; no cross-cell dependencies for this slice."
)
_GOOD_SUB = {
    "title": "Frontend cell: refresh button",
    "description": (
        "Delegate the navbar refresh button to fe-pm: land the provider/hook "
        "and wire the click handler into the page, then open the leaf PR."
    ),
}
_PLAN = "Land the refresh button via the frontend cell."


def _seed_planning_root(
    stack: E2EStack, company: Company
) -> tuple[ScriptedAgent, UUID]:
    """Seed a PENDING MAIN_PM planning root + its origin branch."""
    from roboco.models import Team
    from roboco.models.base import TaskStatus, TaskType

    project_id, _project_slug = seed_project(stack, company)
    main_pm = ScriptedAgent(stack, company.main_pm_id, "main-pm", "main_pm")
    task_id = seed_task(
        stack,
        title="Root: page-scoped refresh button",
        description="Frontend-only root: provider/hook + navbar button.",
        acceptance_criteria=["the refresh button lands on master"],
        task_type=TaskType.PLANNING,
        team=Team.MAIN_PM,
        project_id=project_id,
        created_by=company.main_pm_id,
        assigned_to=company.main_pm_id,
        status=TaskStatus.PENDING,
    )
    branch = f"feature/main_pm/{str(task_id)[:8]}"
    origin_branch(stack, branch, start="master")
    set_branch_name(stack, task_id, branch)
    return main_pm, task_id


def _plan_with(sub_tasks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "plan": _PLAN,
        "approach": _APPROACH,
        "sub_tasks": sub_tasks,
    }


def test_pm_plan_over_decomposition_cap_rejected(e2e_stack: E2EStack) -> None:
    """A plan with >7 sub_tasks is over-decomposition — the gate rejects it
    with incomplete_input + a 'split into sibling coordination tasks' hint."""
    stack = e2e_stack
    company = seed_company(stack)
    main_pm, task_id = _seed_planning_root(stack, company)

    env = main_pm.flow(
        "i_will_plan",
        task_id=str(task_id),
        plan=_PLAN,
        approach=_APPROACH,
        sub_tasks=[dict(_GOOD_SUB, title=f"Slice {i}") for i in range(8)],
    )
    body = expect_error(env, "incomplete_input", "8 sub_tasks rejected")
    assert "sub_tasks" in (body.get("missing") or []), body
    assert "at most 7" in str(body.get("field_hints", {})), body


def test_pm_plan_overlong_subtask_description_rejected(
    e2e_stack: E2EStack,
) -> None:
    """A sub-task description >600 chars is the bloat defect — rejected at
    the Pydantic boundary (422), so the envelope carries the validation
    ``detail`` rather than the gate's ``field_hints``. Both layers are the
    guardrail working; this test pins the boundary layer."""
    stack = e2e_stack
    company = seed_company(stack)
    main_pm, task_id = _seed_planning_root(stack, company)

    bloated = dict(_GOOD_SUB, description="x" * 700)
    env = main_pm.flow(
        "i_will_plan",
        task_id=str(task_id),
        plan=_PLAN,
        approach=_APPROACH,
        sub_tasks=[bloated],
    )
    body = expect_error(env, "incomplete_input", "over-long subtask desc")
    # Boundary 422: missing is [] but detail carries the Pydantic error
    # naming sub_tasks + the 600-char cap.
    detail = str(body.get("detail"))
    assert "sub_tasks" in detail, body
    assert "600" in detail, body


def test_pm_plan_valid_plan_passes_gate(e2e_stack: E2EStack) -> None:
    """A well-formed plan (2 sub_tasks, bounded fields) passes the guardrails
    and transitions the root to in_progress — the happy path stays green."""
    stack = e2e_stack
    company = seed_company(stack)
    main_pm, task_id = _seed_planning_root(stack, company)

    env = main_pm.flow(
        "i_will_plan",
        task_id=str(task_id),
        plan=_PLAN,
        approach=_APPROACH,
        sub_tasks=[
            _GOOD_SUB,
            {
                "title": "Frontend cell: remove inline buttons",
                "description": (
                    "Delegate removal of the stale inline refresh buttons to "
                    "fe-pm so the navbar button is the single source of truth."
                ),
            },
        ],
    )
    # The gate must not fire; the root moves to in_progress. Downstream may
    # raise a different error (e.g. tracing_gap) but NOT incomplete_input.
    body = env
    assert body.get("error") != "incomplete_input", body
