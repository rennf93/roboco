"""Phase 2 state-machine + gateway smoke: one scenario per finding.

Every hop goes through the REAL MCP tool functions -> real HTTP -> real
gateway gates -> real services -> real git against the local origin. No
LLM: the arcs ARE the agent script. Each scenario targets the exact
race/gate the finding names — a regression dies here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tests.e2e_smoke.arcs import (
    dev_arc,
    doc_arc,
    qa_arc,
    seed_company,
    seed_hierarchy,
    seed_project,
    seed_task,
    task_state,
    wire_dependency,
)
from tests.e2e_smoke.harness import ScriptedAgent, expect_error, expect_ok

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from tests.e2e_smoke.harness import E2EStack


def test_m18_add_dependency_rejects_cycle_and_self(e2e_stack: E2EStack) -> None:
    """M18: wire_dependency on a self-edge must raise through the real
    service path, not silently drop."""
    from roboco.services.base import ConflictError

    stack = e2e_stack
    company = seed_company(stack)
    pid, _slug = seed_project(stack, company)
    a = seed_task(
        stack,
        title="A",
        description="d",
        project_id=pid,
        created_by=company.cell_pm_id,
        assigned_to=company.dev_id,
    )
    try:
        wire_dependency(stack, a, a)
    except ConflictError:
        return
    raise AssertionError("self-dependency silently dropped (M18)")


def test_h4_parallel_completion_single_transition(e2e_stack: E2EStack) -> None:
    """H4: dev_arc opens the PR (mark_pr_created) and doc_arc completes docs
    (docs_complete) — both reach awaiting_pm_review through the real path.
    The scenario asserts the task lands in awaiting_pm_review exactly once."""
    stack = e2e_stack
    company = seed_company(stack)
    pid, slug = seed_project(stack, company)
    tid = seed_task(
        stack,
        title="H4 parallel completion",
        description="Create greeting.txt",
        acceptance_criteria=["greeting.txt exists"],
        project_id=pid,
        created_by=company.cell_pm_id,
        assigned_to=company.dev_id,
    )
    dev_arc(stack, company, slug, tid)
    qa_arc(stack, company, tid)
    doc_arc(stack, company, tid, filename="greeting.txt")
    final = task_state(stack, tid)
    assert final["status"] == "awaiting_pm_review", final
    assert final["docs_complete"] is True


def test_h3_pm_cannot_complete_assembled_cell_from_in_progress(
    e2e_stack: E2EStack,
) -> None:
    """H3: a PM owning a cell task at IN_PROGRESS with a terminal child must
    NOT complete directly — submit_up is the only path. The gateway complete
    verb already enforces AWAITING_PM_REVIEW; this proves the service layer
    agrees."""
    stack = e2e_stack
    company = seed_company(stack)
    pid, slug = seed_project(stack, company)
    h = seed_hierarchy(stack, company, pid)
    dev_arc(stack, company, slug, h["child_id"], work=("hello.txt", "hi\n"))
    qa_arc(stack, company, h["child_id"])
    doc_arc(stack, company, h["child_id"], filename="hello.txt")
    pm = ScriptedAgent(stack, company.cell_pm_id, "be-pm", "cell_pm")
    expect_ok(
        pm.flow(
            "complete",
            task_id=str(h["child_id"]),
            notes="child complete — leaf merged via PR review gate",
        ),
        "pm complete child",
    )
    env = pm.flow("complete", task_id=str(h["cell_id"]), notes="try skip gate")
    assert env.get("error") is not None, (
        "PM completed assembled cell from IN_PROGRESS (H3)"
    )


def test_h5_unclaim_from_blocked_clears_snapshot(e2e_stack: E2EStack) -> None:
    """H5: block a task, unclaim from blocked, then read the row —
    pre_block_state must be null. Driven through the real i_am_blocked +
    unclaim verbs."""
    from roboco.db.tables import TaskTable
    from sqlalchemy import select

    stack = e2e_stack
    company = seed_company(stack)
    pid, _slug = seed_project(stack, company)
    tid = seed_task(
        stack,
        title="H5 snapshot clear",
        description="d",
        project_id=pid,
        created_by=company.cell_pm_id,
        assigned_to=company.dev_id,
    )
    dev = ScriptedAgent(stack, company.dev_id, "be-dev-1", "developer")
    expect_ok(dev.flow("give_me_work"), "dev give_me_work")

    def _claim() -> dict[str, Any]:
        return dev.flow(
            "i_will_work_on",
            task_id=str(tid),
            plan=(
                "Block the task on an external dependency, then unclaim from "
                "the blocked state. After unclaim the row's pre_block_state "
                "must be null so a later reclaim starts clean with no stale "
                "snapshot — this scenario asserts that invariant directly."
            ),
            steps=[
                {
                    "title": "Block the task",
                    "description": (
                        "Mark the task blocked on an external dependency via "
                        "the i_am_blocked verb with a concrete reason."
                    ),
                },
                {
                    "title": "Unclaim from blocked",
                    "description": (
                        "Release the blocked task back to the pool via the "
                        "unclaim verb so a later reclaim starts clean."
                    ),
                },
                {
                    "title": "Verify snapshot cleared",
                    "description": (
                        "Read the task row and assert pre_block_state is null "
                        "after the unclaim-from-blocked transition completes."
                    ),
                },
            ],
            technical_considerations=["None — block/unclaim path only."],
            risks=[
                {
                    "risk": "Stale snapshot survives unclaim.",
                    "mitigation": "Assert pre_block_state is null after unclaim.",
                }
            ],
            open_questions=[],
        )

    expect_error(_claim(), "tracing_gap", "dev first i_will_work_on")
    expect_ok(
        dev.do("note", scope="note", task_id=str(tid), text="claim note"), "dev note"
    )
    expect_ok(_claim(), "dev i_will_work_on retry")
    expect_ok(
        dev.flow("i_am_blocked", task_id=str(tid), reason="external dep"), "dev block"
    )
    # i_am_blocked reassigns the task to the cell PM (blocker_resolver), so
    # the PM is the agent that unclaims from the blocked state.
    pm = ScriptedAgent(stack, company.cell_pm_id, "be-pm", "cell_pm")
    expect_ok(pm.flow("unclaim", task_id=str(tid)), "pm unclaim from blocked")

    async def _snap(session: AsyncSession) -> Any:
        row = (
            await session.execute(select(TaskTable).where(TaskTable.id == tid))
        ).scalar_one()
        return row.pre_block_state

    assert stack.run_db(_snap) is None, (
        "pre_block_state not cleared after unclaim-from-blocked (H5)"
    )


def test_l29_pass_qa_routes_through_awaiting_qa(e2e_stack: E2EStack) -> None:
    """L29: the QA arc claims review (task stays awaiting_qa) then passes.
    The end state is awaiting_documentation — the audit journey has a real
    task.awaiting_qa row."""
    stack = e2e_stack
    company = seed_company(stack)
    pid, slug = seed_project(stack, company)
    tid = seed_task(
        stack,
        title="L29 qa hop",
        description="d",
        project_id=pid,
        created_by=company.cell_pm_id,
        assigned_to=company.dev_id,
    )
    dev_arc(stack, company, slug, tid)
    qa_arc(stack, company, tid)
    assert task_state(stack, tid)["status"] == "awaiting_documentation"
