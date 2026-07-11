"""Scenario: the sibling-sequence claim gate holds independent of edges.

CEO directive: sequence is the bar, full stop. A same-parent sibling with a
strictly lower sequence must hold the later sibling's claim even when NO
dependency edge was ever wired between them — the live bug this guardrails:
a PM delegated a batch of revision subtasks by sequence alone (0..3, no
depends_on edges), and a later sequence claimed in parallel with an earlier,
still-open one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tests.e2e_smoke.arcs import (
    origin_branch,
    seed_company,
    seed_project,
    seed_task,
    set_branch_name,
    task_state,
)
from tests.e2e_smoke.harness import ScriptedAgent, expect_error

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from tests.e2e_smoke.harness import E2EStack

# Pydantic's IWillPlanRequest.approach enforces >= 150 chars, and the PM
# sub_tasks gate requires a real (>= 60 char) description, so both blocked
# and unblocked calls need compliant payloads — the sequence gate must be
# the thing that fires, not an earlier content gate.
_APPROACH = (
    "Plan revision 1 once revision 0 reaches a terminal state. This batch "
    "wires no dependency edges between its siblings, only sequence, so the "
    "claim gate alone must hold the delegation order end to end."
)
_SUB_TASKS = [
    {
        "title": "Land revision 1",
        "description": (
            "Apply the second sequenced revision once revision 0 is terminal "
            "and open its leaf PR against the cell branch."
        ),
    }
]


def _cancel(stack: E2EStack, task_id: Any) -> None:
    """Direct terminal-status write — mirrors dispatcher_assign/set_branch_name's
    style (arcs.py): stands in for the real dev->QA->doc->PM chain reaching a
    terminal state, out of scope for this gate-only scenario."""
    from roboco.db.tables import TaskTable
    from roboco.models.base import TaskStatus
    from sqlalchemy import select

    async def _run(session: AsyncSession) -> None:
        row = (
            await session.execute(select(TaskTable).where(TaskTable.id == task_id))
        ).scalar_one()
        row.status = TaskStatus.CANCELLED

    stack.run_db(_run)


def test_sibling_sequence_blocks_claim_until_earlier_sibling_terminal(
    e2e_stack: E2EStack,
) -> None:
    from roboco.models import Team
    from roboco.models.base import TaskType

    stack = e2e_stack
    company = seed_company(stack)
    project_id, _project_slug = seed_project(stack, company)
    main_pm = ScriptedAgent(stack, company.main_pm_id, "main-pm", "main_pm")

    parent_id = seed_task(
        stack,
        title="Revision batch",
        description="Coordinates a sequenced batch of revision subtasks.",
        acceptance_criteria=["every revision lands"],
        task_type=TaskType.PLANNING,
        team=Team.MAIN_PM,
        project_id=project_id,
        created_by=company.main_pm_id,
        assigned_to=company.main_pm_id,
    )
    seq0_id = seed_task(
        stack,
        title="Revision 0",
        description="First sequenced revision subtask.",
        acceptance_criteria=["revision 0 lands"],
        task_type=TaskType.PLANNING,
        team=Team.MAIN_PM,
        project_id=project_id,
        parent_task_id=parent_id,
        sequence=0,
        created_by=company.main_pm_id,
        assigned_to=company.main_pm_id,
    )
    seq1_id = seed_task(
        stack,
        title="Revision 1",
        description="Second sequenced revision subtask.",
        acceptance_criteria=["revision 1 lands"],
        task_type=TaskType.PLANNING,
        team=Team.MAIN_PM,
        project_id=project_id,
        parent_task_id=parent_id,
        sequence=1,
        created_by=company.main_pm_id,
        assigned_to=company.main_pm_id,
    )
    branch = f"feature/main_pm/{str(seq1_id)[:8]}"
    origin_branch(stack, branch, start="master")
    set_branch_name(stack, seq1_id, branch)
    # No wire_dependency() call anywhere in this test — sequence alone must
    # hold the order; seq0 stays PENDING (open, non-terminal).

    expect_error(
        main_pm.flow(
            "i_will_plan",
            task_id=str(seq1_id),
            plan="Land revision 1 once revision 0 is terminal.",
            approach=_APPROACH,
            sub_tasks=_SUB_TASKS,
        ),
        "invalid_state",
        "main_pm i_will_plan seq-1 while seq-0 open (no dependency edge)",
    )
    assert task_state(stack, seq1_id)["status"] == "pending"

    _cancel(stack, seq0_id)

    env = main_pm.flow(
        "i_will_plan",
        task_id=str(seq1_id),
        plan="Land revision 1 now that revision 0 is terminal.",
        approach=_APPROACH,
        sub_tasks=_SUB_TASKS,
    )
    assert env.get("error") != "invalid_state", env
    assert task_state(stack, seq1_id)["status"] == "in_progress", env
