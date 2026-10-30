"""The DevOps infra review gate + the delegation lane, end to end.

Three arcs over the REAL choreographer verbs (no LLM, the smoke-suite way):

1. The gate: an assembled cell→root PR whose diff touches the project's
   infra declaration (default globs — ``docker/**`` needs no conventions.yml)
   REFUSES the primary reviewer's ``pr_pass`` until devops-1 co-claims via
   ``claim_gate_review`` and records a ``passed`` ``record_devops_review``
   verdict for the current head; then ``pr_pass`` composes. Also walks the
   two rejections the gate exists for: the verdict-less ``pr_pass`` and the
   co-claim-less ``record_devops_review``.
2. Flag-off inertia: the identical infra-touching chain with
   ``ROBOCO_DEVOPS_ENABLED`` off passes the gate with no devops involvement
   — the default-off contract, byte-for-byte today.
3. The delegation lane: a PM-assigned infra leaf is claimed and delivered by
   devops-1 through the normal authoring lifecycle (the self-review
   exclusion means its own PR never needs a devops verdict).

Unit tests (test_devops_gate_*) pin the predicate/markers in isolation; this
file pins the verb-chain seams: role-scoped manifests, co-claim vs primary
claim on the same task, the tracing requirements on the verdict verb, and
the flag read at ``pr_pass`` time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.e2e_smoke.arcs import (
    dev_arc,
    dispatcher_assign,
    doc_arc,
    qa_arc,
    seed_company,
    seed_hierarchy,
    seed_project,
    seed_task,
    task_state,
)
from tests.e2e_smoke.harness import ScriptedAgent, expect_error, expect_ok

if TYPE_CHECKING:
    import pytest
    from tests.e2e_smoke.arcs import Company
    from tests.e2e_smoke.harness import E2EStack

# Matches DEFAULT_INFRA_GLOBS ("docker/**") without shipping a conventions.yml
# into the harness repo: the effective-globs read falls back to the shipped
# defaults, exactly the deployment shape a project that never declared
# anything still gets.
_INFRA_FILE = "docker/roboco-sentinel.yml"
_INFRA_CONTENT = "sentinel:\n  enabled: true\n  interval: 300\n"


def _land_infra_child(
    stack: E2EStack, company: Company, project_slug: str, h: dict
) -> ScriptedAgent:
    """Child writes an INFRA-path file and lands via dev→QA→doc→PM-complete
    (the merge-chain recipe, with the file chosen to trip the gate)."""
    dev_arc(
        stack,
        company,
        project_slug,
        h["child_id"],
        work=(_INFRA_FILE, _INFRA_CONTENT),
    )
    qa_arc(stack, company, h["child_id"])
    doc_arc(stack, company, h["child_id"], filename=_INFRA_FILE)

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
    return pm


def _submit_cell(stack: E2EStack, pm: ScriptedAgent, h: dict) -> None:
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


def test_infra_gate_blocks_then_passes_with_devops_verdict(
    e2e_stack: E2EStack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Flag ON + infra diff: pr_pass refuses until the devops co-claim +
    verdict exist, then composes. The two rejections the gate exists for are
    walked explicitly: the verdict-less pr_pass, and the record attempt
    before any co-claim."""
    from roboco.config import settings

    monkeypatch.setattr(settings, "devops_enabled", True)
    stack = e2e_stack
    company = seed_company(stack)
    project_id, project_slug = seed_project(stack, company)
    h = seed_hierarchy(stack, company, project_id)

    pm = _land_infra_child(stack, company, project_slug, h)
    _submit_cell(stack, pm, h)
    cell_id = str(h["cell_id"])

    reviewer = ScriptedAgent(
        stack, company.pr_reviewer_id, "pr-reviewer-1", "pr_reviewer"
    )
    expect_ok(
        reviewer.flow("claim_gate_review", task_id=cell_id),
        "reviewer claim_gate_review",
    )
    expect_ok(
        reviewer.do(
            "note",
            scope="learning",
            task_id=cell_id,
            text=(
                "Gate review learning: the assembled diff carries the child's "
                "docker/ change; infra paths are in play, so the devops "
                "verdict must exist before this gate can pass."
            ),
        ),
        "reviewer learning note",
    )
    # The gate's whole point: the primary reviewer CANNOT pass it alone.
    env = expect_error(
        reviewer.flow(
            "pr_pass",
            task_id=cell_id,
            notes=(
                "Assembled diff reviewed: the docker/ change is present; "
                "however the DevOps infra review has not been recorded yet, "
                "so this pass must be refused by the gate."
            ),
        ),
        "invalid_state",
        "pr_pass without devops verdict",
    )
    assert "infra" in str(env.get("message", "")), env

    devops = ScriptedAgent(stack, company.devops_id, "devops-1", "devops")
    # Claim first, then decide: the verdict verb demands the co-claim marker.
    expect_error(
        devops.flow(
            "record_devops_review",
            task_id=cell_id,
            notes="Verdict recorded without any co-claim — must be refused.",
        ),
        "not_authorized",
        "record_devops_review before co-claim",
    )

    expect_ok(
        devops.flow("claim_gate_review", task_id=cell_id),
        "devops co-claim",
    )
    expect_ok(
        devops.do(
            "note",
            scope="learning",
            task_id=cell_id,
            text=(
                "Infra review learning: the assembled docker/ change only "
                "adds a sentinel config file — no compose topology, no "
                "image, no workflow wiring; the change is contained and "
                "reversible, so the infra risk is low."
            ),
        ),
        "devops learning note",
    )
    expect_ok(
        devops.flow(
            "record_devops_review",
            task_id=cell_id,
            notes=(
                "Infra review passed: the assembled diff adds one declarative "
                "docker/ config file. No compose service, volume, network, "
                "or workflow change; nothing touches the deploy topology. "
                "Recorded for the current PR head."
            ),
        ),
        "devops record_devops_review",
    )

    # The reviewer still owns the gate (the co-claim is marker-based): its
    # pr_pass now composes.
    expect_ok(
        reviewer.flow(
            "pr_pass",
            task_id=cell_id,
            notes=(
                "Assembled diff reviewed against the base branch: the docker/ "
                "config is declarative and contained; the DevOps verdict is "
                "recorded for the current head — passing to the PM for merge."
            ),
        ),
        "reviewer pr_pass after verdict",
    )
    assert task_state(stack, h["cell_id"])["status"] == "awaiting_pm_review"


def test_infra_gate_flag_off_is_inert(e2e_stack: E2EStack) -> None:
    """The identical infra-touching chain with the flag OFF (the shipped
    default): the reviewer's pr_pass composes with NO devops involvement —
    nothing demands a verdict, nothing wedges."""
    stack = e2e_stack
    company = seed_company(stack)
    project_id, project_slug = seed_project(stack, company)
    h = seed_hierarchy(stack, company, project_id)

    pm = _land_infra_child(stack, company, project_slug, h)
    _submit_cell(stack, pm, h)
    cell_id = str(h["cell_id"])

    reviewer = ScriptedAgent(
        stack, company.pr_reviewer_id, "pr-reviewer-1", "pr_reviewer"
    )
    expect_ok(
        reviewer.flow("claim_gate_review", task_id=cell_id),
        "reviewer claim_gate_review",
    )
    expect_ok(
        reviewer.do(
            "note",
            scope="learning",
            task_id=cell_id,
            text=(
                "Gate review learning: the assembled diff carries the child's "
                "docker/ change; with the devops flag off the gate is inert "
                "and this review stands alone."
            ),
        ),
        "reviewer learning note",
    )
    expect_ok(
        reviewer.flow(
            "pr_pass",
            task_id=cell_id,
            notes=(
                "Assembled diff reviewed against the base branch: the docker/ "
                "config change is declarative and contained; no devops gate "
                "is armed, so this review passes to the PM for merge."
            ),
        ),
        "reviewer pr_pass with flag off",
    )
    assert task_state(stack, h["cell_id"])["status"] == "awaiting_pm_review"


def test_delegation_lane_devops_claims_and_delivers(
    e2e_stack: E2EStack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PM delegation: an infra leaf assigned to devops-1 flows through the
    NORMAL authoring lifecycle (give_me_work → claim → worktree → commit →
    PR → i_am_done → awaiting_qa). The self-review exclusion means no devops
    verdict is ever demanded on its own work."""
    from roboco.config import settings

    monkeypatch.setattr(settings, "devops_enabled", True)
    stack = e2e_stack
    company = seed_company(stack)
    project_id, project_slug = seed_project(stack, company)

    task_id = seed_task(
        stack,
        title="Harden the sentinel compose config",
        description=(
            "Add the sentinel declarative config under docker/ so the "
            "monitoring stack picks it up on the next deploy rotation; "
            "purely additive, no service definition changes."
        ),
        acceptance_criteria=["docker/roboco-sentinel.yml exists and parses"],
        project_id=project_id,
        created_by=company.cell_pm_id,
        assigned_to=company.devops_id,
    )
    tid = str(task_id)

    devops = ScriptedAgent(stack, company.devops_id, "devops-1", "devops")
    env = expect_ok(devops.flow("give_me_work"), "devops give_me_work")
    assert env.get("task_id") == tid, f"expected task {tid}, got: {env}"

    def _claim() -> dict:
        return devops.flow(
            "i_will_work_on",
            task_id=tid,
            plan=(
                "Add docker/roboco-sentinel.yml with the sentinel block, "
                "commit it on the task branch, push, and open the PR against "
                "the base branch; the file is declarative YAML with no "
                "service or network changes."
            ),
            steps=[
                {
                    "title": "Write the sentinel config",
                    "description": (
                        "Create docker/roboco-sentinel.yml with the sentinel "
                        "block the monitoring stack reads."
                    ),
                },
                {
                    "title": "Commit, push, open the PR",
                    "description": (
                        "Commit the new file on the task branch, push it to "
                        "origin, and open the pull request against the base "
                        "branch."
                    ),
                },
            ],
            technical_considerations=["Declarative YAML; no runtime impact."],
            risks=[
                {
                    "risk": "None of substance — purely additive config file.",
                    "mitigation": "Self-verify the file content before submit.",
                }
            ],
            open_questions=[],
        )

    # Same claim-time tracing gate every author role walks.
    expect_error(_claim(), "tracing_gap", "devops first i_will_work_on")
    expect_ok(
        devops.do(
            "note",
            scope="note",
            task_id=tid,
            text=(
                "Initial assessment: a single additive YAML file under "
                "docker/ satisfies the acceptance criteria; no compose "
                "service, volume, or network is touched, so the infra risk "
                "is minimal and the plan is a write/commit/PR sequence."
            ),
        ),
        "devops note at claim",
    )
    expect_ok(_claim(), "devops i_will_work_on retry")

    workspace = stack.workspace_of(project_slug, "board", "devops-1")
    workdir = workspace / ".worktrees" / tid[:8]
    assert workdir.is_dir(), f"per-task worktree missing at {workdir}"
    (workdir / _INFRA_FILE).parent.mkdir(parents=True, exist_ok=True)
    (workdir / _INFRA_FILE).write_text(_INFRA_CONTENT)

    expect_ok(
        devops.do(
            "commit",
            message=f"feat: add {_INFRA_FILE} for the sentinel stack",
            files=[_INFRA_FILE],
        ),
        "devops commit",
    )
    expect_ok(devops.flow("open_pr", task_id=tid), "devops open_pr")
    assert task_state(stack, task_id)["pr_number"], "no PR recorded"

    expect_ok(
        devops.do(
            "note",
            scope="decision",
            task_id=tid,
            text=(
                "Verified the acceptance criterion on the branch: "
                "docker/roboco-sentinel.yml exists and parses as YAML. "
                "Decision: no further changes needed; the file is purely "
                "additive and self-contained."
            ),
        ),
        "devops decision note",
    )
    # The floating role authors the DEVELOPER handoff section (its i_am_done
    # demands dev_notes>=min; content_notes maps devops -> developer).
    expect_ok(
        devops.do(
            "note",
            text="Handoff summary below (section carries the content).",
            scope="handoff",
            task_id=tid,
            section={
                "summary": (
                    "Added the sentinel config under docker/ on the task "
                    "branch; PR is open against the base branch; single "
                    "additive commit, no topology risk."
                )
            },
        ),
        "devops handoff section",
    )
    expect_ok(
        devops.do(
            "note",
            scope="reflect",
            task_id=tid,
            text=(
                "Reflection: delivered the infra change exactly per plan — "
                "wrote the config, committed on the task branch, opened the "
                "PR, and self-verified the acceptance criterion against the "
                "committed content."
            ),
        ),
        "devops reflect note",
    )
    expect_ok(devops.flow("i_am_done", task_id=tid), "devops i_am_done")
    assert task_state(stack, task_id)["status"] == "awaiting_qa"
