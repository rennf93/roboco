"""Scenario 2: the PM merge chain over a root → cell → dev hierarchy.

The dev child rides the scenario-1 arc into the cell parent's branch; the
cell PM completes it (auto-merge child PR into the cell branch — a real
squash on the origin), then ``submit_up`` assembles the cell → root PR
into ``awaiting_pr_review``; the PR reviewer gate-passes it; the cell PM
``complete``s the cell task, merging cell → root. This is the exact
PM→reviewer→PM turn sequence the wave-1 turn cut will shorten — the
BEFORE-net.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from tests.e2e_smoke.arcs import (
    dev_arc,
    dispatcher_assign,
    doc_arc,
    origin_branch,
    origin_file,
    qa_arc,
    seed_company,
    seed_project,
    seed_task,
    task_state,
)
from tests.e2e_smoke.harness import ScriptedAgent, expect_ok

if TYPE_CHECKING:
    from tests.e2e_smoke.harness import E2EStack


def test_pm_merge_chain_to_root_branch(e2e_stack: E2EStack) -> None:
    stack = e2e_stack
    company = seed_company(stack)
    project_id, project_slug = seed_project(stack, company)

    from roboco.models.base import TaskStatus

    # Root (Main-PM delivery root) and cell parent are seeded mid-flight —
    # the PM planning/delegation lane is scenario 3's subject, not this one.
    # Branch names follow the real convention: the task-short-id chain.
    root_id = uuid4()
    cell_id = uuid4()
    root_branch = f"feature/backend/{str(root_id)[:8]}"
    cell_branch = f"{root_branch}--{str(cell_id)[:8]}"
    origin_branch(stack, root_branch, start="master")
    origin_branch(stack, cell_branch, start=root_branch)
    seed_task(
        stack,
        id=root_id,
        title="Delivery root: greeting program",
        description=(
            "Root coordination task assembling the greeting feature across "
            "the backend cell for the smoke harness merge-chain scenario."
        ),
        acceptance_criteria=["the greeting feature lands on the root branch"],
        project_id=project_id,
        created_by=company.main_pm_id,
        assigned_to=company.main_pm_id,
        status=TaskStatus.IN_PROGRESS,
        branch_name=root_branch,
        active_claimant_id=company.main_pm_id,
    )

    seed_task(
        stack,
        id=cell_id,
        title="Backend slice: greeting file",
        description=(
            "Cell task assembling the backend slice of the greeting feature; "
            "one dev leaf writes the file, the cell PM assembles and submits."
        ),
        acceptance_criteria=["hello.txt exists at the repo root"],
        project_id=project_id,
        created_by=company.main_pm_id,
        assigned_to=company.cell_pm_id,
        parent_task_id=root_id,
        status=TaskStatus.IN_PROGRESS,
        branch_name=cell_branch,
        active_claimant_id=company.cell_pm_id,
    )

    child_id = seed_task(
        stack,
        title="Write hello.txt",
        description=(
            "Create hello.txt with a friendly greeting at the repo root so "
            "the merge-chain scenario has a real change to assemble upward."
        ),
        acceptance_criteria=["hello.txt exists at the repo root"],
        project_id=project_id,
        created_by=company.cell_pm_id,
        parent_task_id=cell_id,
        assigned_to=company.dev_id,
    )

    # --- child: the scenario-1 arc, based on the cell branch ---------------
    dev_arc(
        stack,
        company,
        project_slug,
        child_id,
        work=("hello.txt", "Hello from the merge chain!\n"),
    )
    qa_arc(stack, company, child_id)
    doc_arc(stack, company, child_id, filename="hello.txt")

    # The child's PR must target the CELL branch (ancestor resolution) —
    # branch NAMES derive from the task-id chain, so assert on the base ref.
    child = task_state(stack, child_id)
    child_pr = stack.github.prs[child["pr_number"]]
    assert child_pr["base"]["ref"] == cell_branch, (
        f"child PR should target the cell branch: {child_pr['base']} / {child}"
    )

    # --- cell PM: complete the child (auto-merges its PR into cell branch) --
    pm = ScriptedAgent(stack, company.cell_pm_id, "be-pm", "cell_pm")
    expect_ok(
        pm.flow(
            "complete",
            task_id=str(child_id),
            notes=(
                "Child verified: QA passed with per-criterion verdicts and "
                "docs are complete; merging the leaf PR into the cell branch."
            ),
        ),
        "pm complete child",
    )
    assert task_state(stack, child_id)["status"] == "completed"
    assert origin_file(stack, cell_branch, "hello.txt"), (
        "child merge did not land hello.txt on the cell branch"
    )

    # --- cell PM: submit the assembled cell PR (→ awaiting_pr_review) -------
    expect_ok(
        pm.flow(
            "submit_up",
            task_id=str(cell_id),
            notes=(
                "All children terminal and merged into the cell branch; "
                "assembling the cell PR against the root branch for the "
                "in-path review gate."
            ),
        ),
        "pm submit_up",
    )
    cell = task_state(stack, cell_id)
    assert cell["status"] == "awaiting_pr_review", cell
    assert cell["pr_number"], cell

    # --- PR reviewer: in-path gate ------------------------------------------
    reviewer = ScriptedAgent(
        stack, company.pr_reviewer_id, "pr-reviewer-1", "pr_reviewer"
    )
    expect_ok(
        reviewer.flow("claim_gate_review", task_id=str(cell_id)),
        "reviewer claim_gate_review",
    )
    expect_ok(
        reviewer.do(
            "note",
            scope="learning",
            task_id=str(cell_id),
            text=(
                "Gate review learning: the assembled cell diff is exactly the "
                "child's additive file with the integrity marker present; "
                "squash-merge assembly verified against the root branch."
            ),
        ),
        "reviewer learning note",
    )
    expect_ok(
        reviewer.flow(
            "pr_pass",
            task_id=str(cell_id),
            notes=(
                "Assembled diff reviewed against the root branch: exactly the "
                "child's additive file, integrity markers present, no scope "
                "creep — passing to the PM for merge."
            ),
        ),
        "reviewer pr_pass",
    )
    assert task_state(stack, cell_id)["status"] == "awaiting_pm_review"

    # pr_pass clears ownership by design; the orchestrator's
    # _dispatch_pm_review_work re-claims the task for the owning PM before
    # spawning it — mirror that dispatcher lane here.
    dispatcher_assign(stack, cell_id, company.cell_pm_id)

    # --- cell PM: final merge (cell → root branch) ---------------------------
    expect_ok(
        pm.flow(
            "complete",
            task_id=str(cell_id),
            notes=(
                "Gate passed; merging the assembled cell PR into the root "
                "branch and closing the cell task."
            ),
        ),
        "pm complete cell",
    )
    assert task_state(stack, cell_id)["status"] == "completed"
    assert origin_file(stack, root_branch, "hello.txt"), (
        "cell merge did not land hello.txt on the root branch"
    )
