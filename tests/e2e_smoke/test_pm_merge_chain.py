"""Scenarios 2 + 2b: the PM merge chain, with and without the submit turn.

Scenario 2 (the BEFORE-net): the classic chain — the cell PM completes the
child, calls ``submit_up`` itself, the reviewer gate-passes, the PM merges.

Scenario 2b (the turn cut): the child lands the same way, but the SUBMIT
turn never happens as an agent call — the orchestrator's
``_try_auto_submit`` runs the real submit verb through the real API as the
owning PM, and the chain continues reviewer → PM merge. One PM turn fewer
per assembled parent, with every gate intact.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx
from tests.e2e_smoke.arcs import (
    dev_arc,
    dispatcher_assign,
    doc_arc,
    origin_file,
    qa_arc,
    reviewer_gate_pass_arc,
    seed_company,
    seed_hierarchy,
    seed_project,
    task_state,
)
from tests.e2e_smoke.harness import ScriptedAgent, expect_ok

if TYPE_CHECKING:
    import pytest
    from tests.e2e_smoke.arcs import Company
    from tests.e2e_smoke.harness import E2EStack


def _land_child(
    stack: E2EStack, company: Company, project_slug: str, h: dict
) -> ScriptedAgent:
    """Run the child through dev→QA→doc and the PM's child-completion merge."""
    dev_arc(
        stack,
        company,
        project_slug,
        h["child_id"],
        work=("hello.txt", "Hello from the merge chain!\n"),
    )
    qa_arc(stack, company, h["child_id"])
    doc_arc(stack, company, h["child_id"], filename="hello.txt")

    # The child's PR must target the CELL branch (ancestor resolution) —
    # branch NAMES derive from the task-id chain, so assert on the base ref.
    child = task_state(stack, h["child_id"])
    child_pr = stack.github.prs[child["pr_number"]]
    assert child_pr["base"]["ref"] == h["cell_branch"], (
        f"child PR should target the cell branch: {child_pr['base']} / {child}"
    )

    # The dispatcher's _dispatch_pm_review_work lane re-claims an
    # awaiting_pm_review leaf for the owning cell PM before spawning it
    # (_claim_task_for_agent); the complete guard requires assigned_to==PM.
    dispatcher_assign(stack, h["child_id"], company.cell_pm_id)
    pm = ScriptedAgent(stack, company.cell_pm_id, "be-pm", "cell_pm")
    expect_ok(
        pm.flow(
            "complete",
            task_id=str(h["child_id"]),
            notes=(
                "Child verified: QA passed with per-criterion verdicts and "
                "docs are complete; merging the leaf PR into the cell branch."
            ),
        ),
        "pm complete child",
    )
    assert task_state(stack, h["child_id"])["status"] == "completed"
    assert origin_file(stack, h["cell_branch"], "hello.txt"), (
        "child merge did not land hello.txt on the cell branch"
    )
    return pm


def _pm_merges_cell(
    stack: E2EStack, company: Company, pm: ScriptedAgent, h: dict
) -> None:
    """Dispatcher re-claim (mirrored) + the PM's final merge turn."""
    dispatcher_assign(stack, h["cell_id"], company.cell_pm_id)
    expect_ok(
        pm.flow(
            "complete",
            task_id=str(h["cell_id"]),
            notes=(
                "Gate passed; merging the assembled cell PR into the root "
                "branch and closing the cell task."
            ),
        ),
        "pm complete cell",
    )
    assert task_state(stack, h["cell_id"])["status"] == "completed"
    assert origin_file(stack, h["root_branch"], "hello.txt"), (
        "cell merge did not land hello.txt on the root branch"
    )


def test_pm_merge_chain_to_root_branch(e2e_stack: E2EStack) -> None:
    stack = e2e_stack
    company = seed_company(stack)
    project_id, project_slug = seed_project(stack, company)
    h = seed_hierarchy(stack, company, project_id)

    pm = _land_child(stack, company, project_slug, h)

    # --- cell PM: submit the assembled cell PR (the turn 2b cuts) -----------
    expect_ok(
        pm.flow(
            "submit_up",
            task_id=str(h["cell_id"]),
            notes=(
                "All children terminal and merged into the cell branch; "
                "assembling the cell PR against the root branch for the "
                "in-path review gate."
            ),
        ),
        "pm submit_up",
    )
    cell = task_state(stack, h["cell_id"])
    assert cell["status"] == "awaiting_pr_review", cell
    assert cell["pr_number"], cell

    reviewer_gate_pass_arc(stack, company, h["cell_id"])
    _pm_merges_cell(stack, company, pm, h)


def test_auto_submit_cuts_the_pm_turn(
    e2e_stack: E2EStack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wave-1 turn cut, end to end: no agent calls submit_up — the
    orchestrator's ``_try_auto_submit`` drives the REAL submit verb through
    the REAL API as the owning PM, and the gate chain continues unchanged."""
    from roboco.config import settings
    from roboco.runtime.orchestrator import AgentOrchestrator

    stack = e2e_stack
    company = seed_company(stack)
    project_id, project_slug = seed_project(stack, company)
    h = seed_hierarchy(stack, company, project_id)

    pm = _land_child(stack, company, project_slug, h)

    # --- the cut: the orchestrator submits system-side ----------------------
    monkeypatch.setattr(settings, "api_url", stack.base_url)
    monkeypatch.setattr(settings, "pr_gate_auto_submit_enabled", True)
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._tick_handled_tasks = set()
    orch._bg_tasks = set()

    cell_task_dict = {
        "id": str(h["cell_id"]),
        "team": "backend",
        "branch_name": h["cell_branch"],
        "project_id": str(project_id),
        "assigned_to": str(company.cell_pm_id),
        "status": "in_progress",
    }

    async def _go() -> bool:
        async with httpx.AsyncClient(timeout=60) as client:
            return await orch._try_auto_submit(client, cell_task_dict, "be-pm")

    assert asyncio.run(_go()) is True, "auto-submit should accept a clean parent"

    cell = task_state(stack, h["cell_id"])
    assert cell["status"] == "awaiting_pr_review", cell
    assert cell["pr_number"], cell

    # --- unchanged tail: reviewer gate + the PM's one remaining turn ---------
    reviewer_gate_pass_arc(stack, company, h["cell_id"])
    _pm_merges_cell(stack, company, pm, h)
