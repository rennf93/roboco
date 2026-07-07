"""Scenario 4: the MegaTask umbrella — sequencing hold, serial root-subtask
completion, and the branchless umbrella close.

The umbrella (``batch_id`` set, ``parent_task_id=None``, no project/branch/PR
of its own) groups two root-subtasks (``batch_id`` set, parented under the
umbrella, each a real Main-PM delivery root with its own project/branch/PR —
``is_batch_root_subtask``). RS2 depends on RS1 (the serial-wave-chain edge
production wires via ``TaskService.add_dependency``). The scenario:

1. Proves the sequencing hold: RS2 cannot be planned while RS1 is open.
2. Drives RS1 through the REAL dev→QA→doc→cell→root→CEO chain to master.
3. Proves the hold clears once RS1 is terminal, then drives RS2 through the
   identical chain (a genuinely serial MegaTask — RS1's root PR merges to
   master before RS2 is ever claimed).
4. Closes the umbrella itself via the branchless path: ``complete`` escalates
   it straight from ``in_progress`` (no ``submit_root`` — an umbrella
   assembles no PR of its own) to ``awaiting_ceo_approval``, and the CEO
   closes it with ``POST /ceo-approve`` (NOT ``approve-and-merge`` — there is
   no PR to merge).
"""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx
from tests.e2e_smoke.arcs import (
    dev_arc,
    dispatcher_assign,
    doc_arc,
    origin_branch,
    origin_file,
    qa_arc,
    reviewer_gate_pass_arc,
    seed_cell_and_dev,
    seed_company,
    seed_project,
    seed_task,
    set_branch_name,
    task_state,
    wait_for_status,
    wire_dependency,
)
from tests.e2e_smoke.harness import ScriptedAgent, expect_error, expect_ok

if TYPE_CHECKING:
    from tests.e2e_smoke.arcs import Company
    from tests.e2e_smoke.harness import E2EStack


@dataclass
class World:
    """The constant per-test context, bundled so the seeding helpers below
    stay under the argument-count lint bar."""

    stack: E2EStack
    company: Company
    project_id: Any
    project_slug: str


# Pydantic's IWillPlanRequest.approach enforces >= 150 chars at the HTTP
# boundary regardless of which internal gate would fire first, so every
# i_will_plan call (including the one expected to be REJECTED by the
# dependency guard) needs a compliant approach.
_APPROACH = (
    "Serialize the two root-subtasks of this MegaTask: land RS1's cell work, "
    "submit its root PR, pass the in-path review gate, escalate it to the "
    "CEO, and merge to master before RS2 — whose branch depends on RS1's "
    "completion — is released to repeat the identical sequence on its own "
    "root branch against the same project's master."
)


def _seed_umbrella(stack: E2EStack, company: Company) -> tuple[Any, Any]:
    """Branchless MegaTask umbrella: no project/product/cell-map, no branch,
    no PR of its own — ``is_batch_umbrella`` (batch_id set, no parent)."""
    from roboco.models import Team
    from roboco.models.base import TaskStatus, TaskType

    batch_id = uuid4()
    umbrella_id = seed_task(
        stack,
        title="MegaTask: ship the greeting feature in two waves",
        description=(
            "Umbrella coordinating two sequenced root-subtasks that each "
            "land a greeting file on the same project's master branch, one "
            "after the other."
        ),
        acceptance_criteria=["every root-subtask lands on master"],
        task_type=TaskType.PLANNING,
        team=Team.MAIN_PM,
        batch_id=batch_id,
        parent_task_id=None,
        created_by=company.main_pm_id,
        assigned_to=company.main_pm_id,
        status=TaskStatus.IN_PROGRESS,
        active_claimant_id=company.main_pm_id,
    )
    return umbrella_id, batch_id


def _seed_root_subtask_in_progress(
    world: World, *, umbrella_id: Any, batch_id: Any, wave: int
) -> dict[str, Any]:
    """RS1: pre-seeded mid-flight exactly like ``seed_hierarchy``'s root,
    plus the ``batch_id``/``parent_task_id`` that make it a root-subtask."""
    from roboco.models import Team
    from roboco.models.base import TaskStatus, TaskType

    stack, company = world.stack, world.company
    root_id = uuid4()
    root_branch = f"feature/main_pm/{str(root_id)[:8]}"
    origin_branch(stack, root_branch, start="master")
    seed_task(
        stack,
        id=root_id,
        title=f"Root-subtask {wave}: greeting wave {wave}",
        description=(
            f"MegaTask root-subtask (wave {wave}): assembles the backend "
            "cell's greeting file and lands it on this project's master via "
            "the normal root→CEO chain."
        ),
        acceptance_criteria=["the greeting feature lands on the root branch"],
        task_type=TaskType.PLANNING,
        team=Team.MAIN_PM,
        batch_id=batch_id,
        parent_task_id=umbrella_id,
        project_id=world.project_id,
        created_by=company.main_pm_id,
        assigned_to=company.main_pm_id,
        status=TaskStatus.IN_PROGRESS,
        branch_name=root_branch,
        active_claimant_id=company.main_pm_id,
    )
    return {"root_id": root_id, "root_branch": root_branch}


def _seed_root_subtask_pending(
    world: World, *, umbrella_id: Any, batch_id: Any, wave: int
) -> Any:
    """RS2: a real Main-PM root-subtask, PENDING (unclaimed, no branch yet —
    it is claimed for real once its dependency clears, exercising the claim
    path scenarios 1-3 skip by pre-seeding roots mid-flight)."""
    from roboco.models import Team
    from roboco.models.base import TaskStatus, TaskType

    stack, company = world.stack, world.company
    root_id = uuid4()
    seed_task(
        stack,
        id=root_id,
        title=f"Root-subtask {wave}: greeting wave {wave}",
        description=(
            f"MegaTask root-subtask (wave {wave}): depends on the prior "
            "root-subtask (the serial wave-chain edge) and repeats the same "
            "root→CEO chain once the dependency clears."
        ),
        acceptance_criteria=["the greeting feature lands on the root branch"],
        task_type=TaskType.PLANNING,
        team=Team.MAIN_PM,
        batch_id=batch_id,
        parent_task_id=umbrella_id,
        project_id=world.project_id,
        created_by=company.main_pm_id,
        assigned_to=company.main_pm_id,
        status=TaskStatus.PENDING,
    )
    return root_id


def _land_and_merge_cell(
    stack: E2EStack,
    company: Company,
    project_slug: str,
    h: dict[str, Any],
    *,
    filename: str,
) -> ScriptedAgent:
    """dev → QA → docs → cell-PM completes the child (the child-landing half
    of ``test_pm_merge_chain._land_child``, parameterized by filename so two
    root-subtasks sharing one project never collide on the same additive
    file when both eventually merge into the same master)."""
    dev_arc(
        stack,
        company,
        project_slug,
        h["child_id"],
        work=(filename, f"Hello from {filename}!\n"),
    )
    qa_arc(stack, company, h["child_id"])
    doc_arc(stack, company, h["child_id"], filename=filename)

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
    assert origin_file(stack, h["cell_branch"], filename), (
        "child merge did not land the file on the cell branch"
    )
    return pm


def _merge_cell_to_root(
    stack: E2EStack,
    company: Company,
    pm: ScriptedAgent,
    h: dict[str, Any],
    *,
    filename: str,
) -> None:
    """Dispatcher re-claim (mirrored) + the PM's cell→root merge turn (the
    ``test_pm_merge_chain._pm_merges_cell`` shape, parameterized by filename)."""
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
    assert origin_file(stack, h["root_branch"], filename), (
        "cell merge did not land the file on the root branch"
    )


def _land_root_subtask(world: World, root: dict[str, Any], *, filename: str) -> None:
    """Full cell→dev delivery under an already-claimed root-subtask."""
    stack, company, project_slug = world.stack, world.company, world.project_slug
    h = seed_cell_and_dev(stack, company, world.project_id, root, filename=filename)
    pm = _land_and_merge_cell(stack, company, project_slug, h, filename=filename)
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
    _merge_cell_to_root(stack, company, pm, h, filename=filename)


def _submit_review_escalate_merge(
    stack: E2EStack, company: Company, main_pm: ScriptedAgent, root_id: Any
) -> None:
    """submit_root → reviewer gate → main_pm complete → CEO approve-and-merge.

    The root analogue of ``test_root_ceo_chain``'s root→master chain, reused
    verbatim for each root-subtask (each opens + merges its OWN root→master
    PR — a MegaTask root-subtask behaves exactly like a plain Main-PM root
    for git/CEO purposes, see ``is_batch_root_subtask``)."""
    rid = str(root_id)
    expect_ok(
        main_pm.flow(
            "submit_root",
            task_id=rid,
            notes=(
                "Every cell task is terminal and assembled on the root "
                "branch; opening the root PR against master for the gate."
            ),
        ),
        "main_pm submit_root",
    )
    root_state = task_state(stack, root_id)
    assert root_state["status"] == "awaiting_pr_review", root_state
    assert root_state["pr_number"], root_state

    reviewer_gate_pass_arc(stack, company, root_id)
    dispatcher_assign(stack, root_id, company.main_pm_id)
    expect_ok(
        main_pm.flow(
            "complete",
            task_id=rid,
            notes=(
                "Gate passed on the assembled root PR; approving the "
                "root-subtask and escalating to the CEO for the merge "
                "decision."
            ),
        ),
        "main_pm complete root-subtask",
    )
    assert task_state(stack, root_id)["status"] == "awaiting_ceo_approval"

    resp = httpx.post(
        f"{stack.base_url}/api/tasks/{rid}/approve-and-merge",
        headers={"X-Agent-ID": str(company.ceo_id), "X-Agent-Role": "ceo"},
        timeout=60,
    )
    assert resp.status_code == HTTPStatus.OK, (
        f"approve-and-merge: {resp.status_code} {resp.text[:1500]}"
    )
    assert task_state(stack, root_id)["status"] == "completed"


def test_megatask_umbrella_sequenced_close(e2e_stack: E2EStack) -> None:
    stack = e2e_stack
    company = seed_company(stack)
    project_id, project_slug = seed_project(stack, company)
    world = World(stack, company, project_id, project_slug)
    main_pm = ScriptedAgent(stack, company.main_pm_id, "main-pm", "main_pm")

    umbrella_id, batch_id = _seed_umbrella(stack, company)
    rs1 = _seed_root_subtask_in_progress(
        world, umbrella_id=umbrella_id, batch_id=batch_id, wave=1
    )
    rs2_id = _seed_root_subtask_pending(
        world, umbrella_id=umbrella_id, batch_id=batch_id, wave=2
    )
    # The serial wave-chain edge: RS2 waits for RS1, wired the same way
    # production sequencing does (TaskService.add_dependency), not a status
    # write.
    wire_dependency(stack, rs2_id, rs1["root_id"])

    # --- 1. SEQUENCING HOLD: RS2 cannot be planned while RS1 is open --------
    expect_error(
        main_pm.flow(
            "i_will_plan",
            task_id=str(rs2_id),
            plan="Land wave 2 once wave 1 is terminal.",
            approach=_APPROACH,
            sub_tasks=[],
        ),
        "invalid_state",
        "main_pm i_will_plan RS2 while RS1 open",
    )
    assert task_state(stack, rs2_id)["status"] == "pending"

    # --- 2. RS1: the REAL dev→QA→doc→cell→root→CEO chain to master ---------
    _land_root_subtask(world, rs1, filename="rs1.txt")
    _submit_review_escalate_merge(stack, company, main_pm, rs1["root_id"])
    assert origin_file(stack, "master", "rs1.txt"), "RS1 did not land on master"

    # --- 3. Hold clears: RS2 can now be planned (real claim + branch cut) --
    rs2_branch = f"feature/main_pm/{str(rs2_id)[:8]}"
    # Cut RS2's branch from the NOW-current master (post-RS1-merge) so the
    # claim needs no behind-base auto-sync — mirrors how a wave-2
    # root-subtask's branch is only meaningful once wave 1 has landed.
    origin_branch(stack, rs2_branch, start="master")
    set_branch_name(stack, rs2_id, rs2_branch)

    def _plan_rs2() -> dict[str, Any]:
        return main_pm.flow(
            "i_will_plan",
            task_id=str(rs2_id),
            plan="RS1 is terminal; land wave 2 now.",
            approach=_APPROACH,
            sub_tasks=[
                {
                    "title": "Assemble the backend cell",
                    "description": (
                        "Delegate the backend cell task that writes rs2.txt, "
                        "land it on the root branch, then submit the root PR "
                        "for the in-path review gate."
                    ),
                },
            ],
        )

    # Real choreography (mirrors dev_arc's claim-time note): the composed
    # claim succeeds and stays; the post-claim tracing gate demands the
    # claim-time journal:decision; the retry short-circuits as re-entry.
    expect_error(_plan_rs2(), "tracing_gap", "main_pm i_will_plan RS2 first attempt")
    expect_ok(
        main_pm.do(
            "note",
            scope="decision",
            task_id=str(rs2_id),
            text=(
                "Wave 1 (RS1) is completed and merged to master; wave 2 "
                "(RS2) repeats the identical backend-cell delegation on its "
                "own root branch against the same project."
            ),
        ),
        "main_pm decision note at claim",
    )
    expect_ok(
        _plan_rs2(),
        "main_pm i_will_plan RS2 after RS1 terminal",
    )
    rs2_state = task_state(stack, rs2_id)
    assert rs2_state["status"] == "in_progress", rs2_state
    assert rs2_state["branch_name"] == rs2_branch, rs2_state

    rs2 = {"root_id": rs2_id, "root_branch": rs2_branch}
    _land_root_subtask(world, rs2, filename="rs2.txt")
    _submit_review_escalate_merge(stack, company, main_pm, rs2_id)
    assert origin_file(stack, "master", "rs2.txt"), "RS2 did not land on master"

    # --- 4. UMBRELLA CLOSE: branchless complete → escalate → ceo-approve ---
    expect_ok(
        main_pm.flow(
            "complete",
            task_id=str(umbrella_id),
            notes=(
                "Both root-subtasks are completed and merged to master; "
                "closing the MegaTask umbrella and escalating for CEO "
                "sign-off."
            ),
        ),
        "main_pm complete umbrella",
    )
    escalated = wait_for_status(stack, umbrella_id, "awaiting_ceo_approval")
    assert escalated["pr_number"] is None, escalated

    resp = httpx.post(
        f"{stack.base_url}/api/tasks/{umbrella_id}/ceo-approve",
        headers={"X-Agent-ID": str(company.ceo_id), "X-Agent-Role": "ceo"},
        json={"notes": ("Both root-subtasks landed on master; MegaTask complete.")},
        timeout=60,
    )
    assert resp.status_code == HTTPStatus.OK, (
        f"ceo-approve: {resp.status_code} {resp.text[:1500]}"
    )
    final = wait_for_status(stack, umbrella_id, "completed")
    assert final["pr_number"] is None, final
    assert origin_file(stack, "master", "rs1.txt")
    assert origin_file(stack, "master", "rs2.txt")
