"""Scenario 3: the pr_fail revision loop and the root → CEO chain.

3a: the reviewer REJECTS the assembled cell PR (`pr_fail` with concrete
issues) → needs_revision; the PM resumes, re-submits, and the second gate
pass rides through to the merge — the loop the live fleet burned tokens on
when any link mis-routed.

3b: after the cell lands on the root branch, the Main PM submits the root
(root → master PR), the reviewer gate-passes it, the Main PM's `complete`
escalates the root parent to the CEO, and the REAL CEO endpoint
(`POST /api/tasks/{id}/approve-and-merge`) squash-merges to master —
`hello.txt` ends up on the origin's master, the whole company loop closed
with no LLM anywhere.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import httpx
from tests.e2e_smoke.arcs import (
    dispatcher_assign,
    origin_commit,
    origin_file,
    reviewer_gate_pass_arc,
    seed_company,
    seed_hierarchy,
    seed_project,
    task_state,
)
from tests.e2e_smoke.harness import ScriptedAgent, expect_ok
from tests.e2e_smoke.test_pm_merge_chain import _land_child, _pm_merges_cell

if TYPE_CHECKING:
    from tests.e2e_smoke.harness import E2EStack


def test_pr_fail_revision_loop(e2e_stack: E2EStack) -> None:
    stack = e2e_stack
    company = seed_company(stack)
    project_id, project_slug = seed_project(stack, company)
    h = seed_hierarchy(stack, company, project_id)
    pm = _land_child(stack, company, project_slug, h)
    cell_id = str(h["cell_id"])

    expect_ok(
        pm.flow(
            "submit_up",
            task_id=cell_id,
            notes=(
                "All children terminal and merged into the cell branch; "
                "assembling the cell PR for the in-path review gate."
            ),
        ),
        "pm submit_up (first)",
    )

    reviewer = ScriptedAgent(
        stack, company.pr_reviewer_id, "pr-reviewer-1", "pr_reviewer"
    )
    expect_ok(
        reviewer.flow("claim_gate_review", task_id=cell_id),
        "reviewer claim_gate_review (first)",
    )
    expect_ok(
        reviewer.do(
            "note",
            scope="learning",
            task_id=cell_id,
            text=(
                "Gate review learning: the assembled diff is missing a "
                "trailing newline convention the root branch enforces — "
                "sending back with a concrete fix."
            ),
        ),
        "reviewer learning note (fail pass)",
    )
    expect_ok(
        reviewer.flow(
            "pr_fail",
            task_id=cell_id,
            issues=[
                "hello.txt should end with exactly one trailing newline "
                "per the root branch's file conventions."
            ],
        ),
        "reviewer pr_fail",
    )
    assert task_state(stack, h["cell_id"])["status"] == "needs_revision"

    # The revision dispatcher routes the assembled task back to its PM;
    # mirror that hand-back, then the PM resumes and re-submits.
    dispatcher_assign(stack, h["cell_id"], company.cell_pm_id)
    expect_ok(
        pm.flow(
            "i_will_plan",
            task_id=cell_id,
            plan=(
                "Address the gate's concrete issue and re-submit the cell PR "
                "for a clean pass through the in-path review gate."
            ),
            approach=(
                "Take the reviewer's single concrete finding — hello.txt must "
                "end with exactly one trailing newline per the root branch's "
                "file conventions — verify the file on the cell branch already "
                "satisfies it, re-check the assembled diff against the root "
                "branch for any other convention drift, and then re-run "
                "submit_up so the gate reviews a corrected, freshly assembled "
                "cell PR."
            ),
            sub_tasks=[
                {
                    "title": "Verify the newline convention",
                    "description": (
                        "Confirm hello.txt on the cell branch ends with exactly "
                        "one trailing newline as the reviewer's finding requires."
                    ),
                },
                {
                    "title": "Re-submit the assembled PR",
                    "description": (
                        "Run submit_up again so the freshness and integrity "
                        "checks re-assemble the cell PR for a clean gate pass."
                    ),
                },
            ],
        ),
        "pm i_will_plan after pr_fail",
    )
    # The unchanged-PR hard gate (0.14.0) refuses a resubmit until new work
    # advances the cell branch HEAD — land the dev's fix, then resubmit.
    origin_commit(
        stack,
        h["cell_branch"],
        "hello.txt",
        "Hello from the merge chain, with tidy newline conventions!\n",
        f"[{str(h['child_id'])[:8]}] fix: normalize hello.txt trailing newline",
    )
    expect_ok(
        pm.flow(
            "submit_up",
            task_id=cell_id,
            notes=(
                "Revision addressed: file conventions verified against the "
                "root branch; re-assembling the cell PR for the gate."
            ),
        ),
        "pm submit_up (resubmit)",
    )
    assert task_state(stack, h["cell_id"])["status"] == "awaiting_pr_review"

    reviewer_gate_pass_arc(stack, company, h["cell_id"])
    _pm_merges_cell(stack, company, pm, h)


def test_root_chain_lands_on_master_via_ceo(e2e_stack: E2EStack) -> None:
    stack = e2e_stack
    company = seed_company(stack)
    project_id, project_slug = seed_project(stack, company)
    h = seed_hierarchy(stack, company, project_id)

    # Cell lands on the root branch exactly as scenario 2 proved.
    pm = _land_child(stack, company, project_slug, h)
    expect_ok(
        pm.flow(
            "submit_up",
            task_id=str(h["cell_id"]),
            notes=(
                "All children terminal and merged into the cell branch; "
                "assembling the cell PR for the in-path review gate."
            ),
        ),
        "pm submit_up",
    )
    reviewer_gate_pass_arc(stack, company, h["cell_id"])
    _pm_merges_cell(stack, company, pm, h)

    # --- Main PM: submit the root → master PR, gate, complete → escalate ----
    main_pm = ScriptedAgent(stack, company.main_pm_id, "main-pm", "main_pm")
    root_id = str(h["root_id"])
    expect_ok(
        main_pm.flow(
            "submit_root",
            task_id=root_id,
            notes=(
                "Every cell task is terminal and assembled on the root "
                "branch; opening the root PR against master for the gate."
            ),
        ),
        "main_pm submit_root",
    )
    root = task_state(stack, h["root_id"])
    assert root["status"] == "awaiting_pr_review", root
    assert root["pr_number"], root

    reviewer_gate_pass_arc(stack, company, h["root_id"])
    dispatcher_assign(stack, h["root_id"], company.main_pm_id)
    expect_ok(
        main_pm.flow(
            "complete",
            task_id=root_id,
            notes=(
                "Gate passed on the assembled root PR; approving the root "
                "parent and escalating to the CEO for the merge decision."
            ),
        ),
        "main_pm complete root",
    )
    assert task_state(stack, h["root_id"])["status"] == "awaiting_ceo_approval"

    # --- the human gate: the REAL CEO endpoint merges to master --------------
    resp = httpx.post(
        f"{stack.base_url}/api/tasks/{root_id}/approve-and-merge",
        headers={
            "X-Agent-ID": str(company.ceo_id),
            "X-Agent-Role": "ceo",
        },
        timeout=60,
    )
    assert resp.status_code == HTTPStatus.OK, (
        f"approve-and-merge: {resp.status_code} {resp.text[:1500]}"
    )
    assert task_state(stack, h["root_id"])["status"] == "completed"
    assert origin_file(stack, "master", "hello.txt"), (
        "the CEO merge did not land hello.txt on master"
    )
