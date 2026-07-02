"""Scenario 1: a leaf dev task walks claim → work → PR → QA → docs → PM queue.

Every hop goes through the REAL MCP tool functions → real HTTP → real
gateway gates → real services → real git against the local origin, with a
fake GitHub REST layer whose merges are real git merges. No LLM: this file
IS the agent script, and every rejection envelope is printed verbatim so a
seam regression names itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from tests.e2e_smoke.harness import (
    E2EStack,
    ScriptedAgent,
    expect_error,
    expect_ok,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.usefixtures("e2e_stack")

_PROJECT_SLUG = "e2e-proj"


class _Company:
    dev_id: Any
    qa_id: Any
    doc_id: Any
    cell_pm_id: Any
    project_id: Any
    task_id: Any


def _seed(stack: E2EStack) -> _Company:
    from roboco.db.tables import AgentTable, ProjectTable, TaskTable
    from roboco.models import AgentRole, AgentStatus, Team
    from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType
    from roboco.utils.crypto import encrypt_token

    out = _Company()

    async def _run(session: AsyncSession) -> None:
        def agent(slug: str, role: AgentRole) -> AgentTable:
            row = AgentTable(
                id=uuid4(),
                name=slug,
                slug=slug,
                role=role,
                team=Team.BACKEND,
                status=AgentStatus.ACTIVE,
                model_config={},
                system_prompt=slug,
                capabilities=[],
                permissions={},
                metrics={},
            )
            session.add(row)
            return row

        dev = agent("be-dev-1", AgentRole.DEVELOPER)
        qa = agent("be-qa", AgentRole.QA)
        doc = agent("be-doc", AgentRole.DOCUMENTER)
        pm = agent("be-pm", AgentRole.CELL_PM)
        await session.flush()

        project = ProjectTable(
            id=uuid4(),
            name="E2E Project",
            slug=_PROJECT_SLUG,
            git_url=str(stack.origin),
            default_branch="master",
            protected_branches=["master"],
            assigned_cell=Team.BACKEND,
            created_by=pm.id,
            is_active=True,
            git_token_encrypted=encrypt_token("e2e-dummy-token"),
        )
        session.add(project)
        await session.flush()

        task = TaskTable(
            id=uuid4(),
            title="Add the greeting module",
            description=(
                "Create greeting.txt with a friendly greeting so the smoke "
                "harness has a real file change to commit, push, and merge."
            ),
            acceptance_criteria=[
                "greeting.txt exists at the repo root",
                "its content greets the reader",
            ],
            status=TaskStatus.PENDING,
            priority=2,
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.LOW,
            project_id=project.id,
            created_by=pm.id,
            team=Team.BACKEND,
            confirmed_by_human=True,
            # The pool→agent routing lane is the orchestrator dispatcher's
            # job (not under test here); a dev container is always spawned
            # with its task already routed, which give_me_work serves via
            # the pre-assigned-pending lane.
            assigned_to=dev.id,
        )
        session.add(task)
        await session.flush()

        out.dev_id = dev.id
        out.qa_id = qa.id
        out.doc_id = doc.id
        out.cell_pm_id = pm.id
        out.project_id = project.id
        out.task_id = task.id

    stack.run_db(_run)
    return out


def _task_state(stack: E2EStack, task_id: Any) -> dict[str, Any]:
    from roboco.db.tables import TaskTable
    from sqlalchemy import select

    async def _run(session: AsyncSession) -> dict[str, Any]:
        row = (
            await session.execute(select(TaskTable).where(TaskTable.id == task_id))
        ).scalar_one()
        return {
            "status": str(row.status),
            "branch_name": row.branch_name,
            "pr_number": row.pr_number,
            "docs_complete": row.docs_complete,
            "assigned_to": row.assigned_to,
        }

    state: dict[str, Any] = stack.run_db(_run)
    return state


def test_leaf_dev_task_reaches_pm_review(e2e_stack: E2EStack) -> None:
    stack = e2e_stack
    ids = _seed(stack)
    task_id = str(ids.task_id)

    # --- developer: discover, claim, work, PR, submit -----------------------
    dev = ScriptedAgent(stack, ids.dev_id, "be-dev-1", "developer")

    env = expect_ok(dev.flow("give_me_work"), "dev give_me_work")
    assert env.get("task_id") == task_id, f"expected our task, got: {env}"

    def _claim() -> dict:
        return dev.flow(
            "i_will_work_on",
            task_id=task_id,
            plan=(
                "Create greeting.txt at the repository root containing a "
                "friendly greeting, commit it on the task branch with the "
                "task-prefixed message, push the branch to origin, open the "
                "pull request against master, and self-verify both acceptance "
                "criteria by re-reading the committed file content."
            ),
            steps=[
                {
                    "title": "Write greeting.txt",
                    "description": (
                        "Create greeting.txt at the repo root containing a "
                        "friendly greeting for the reader."
                    ),
                },
                {
                    "title": "Commit and push",
                    "description": (
                        "Commit the new file on the task branch with a "
                        "task-prefixed message and push it to origin."
                    ),
                },
                {
                    "title": "Open PR and self-verify",
                    "description": (
                        "Open the pull request against master and re-read the "
                        "file to confirm both acceptance criteria hold."
                    ),
                },
            ],
            technical_considerations=["Plain text file; no build impact."],
            risks=[
                {
                    "risk": "None of substance — purely additive file.",
                    "mitigation": "Self-verify the file content before submit.",
                }
            ],
            open_questions=[],
        )

    # The composed claim succeeds and STAYS; the post-claim tracing gate
    # then demands the claim-time journal note — the real agent choreography
    # is claim → tracing_gap → note (now claim-held) → retry short-circuits.
    expect_error(_claim(), "tracing_gap", "dev first i_will_work_on")
    expect_ok(
        dev.do(
            "note",
            scope="note",
            task_id=task_id,
            text=(
                "Initial assessment: a single additive text file at the repo "
                "root satisfies both acceptance criteria; no existing code is "
                "touched, so risk is minimal and the plan is a three-step "
                "write/commit/PR sequence."
            ),
        ),
        "dev note at claim",
    )
    expect_ok(_claim(), "dev i_will_work_on retry")
    state = _task_state(stack, ids.task_id)
    assert state["status"] in ("claimed", "in_progress"), state
    assert state["branch_name"], f"claim did not set a branch: {state}"

    workspace = stack.workspace_of(_PROJECT_SLUG, "backend", "be-dev-1")
    assert workspace.is_dir(), f"workspace clone missing at {workspace}"
    # F123: the agent works in the per-task worktree, not the clone root.
    workdir = workspace / ".worktrees" / task_id[:8]
    assert workdir.is_dir(), f"per-task worktree missing at {workdir}"
    (workdir / "greeting.txt").write_text("Hello from the e2e smoke agent!\n")

    expect_ok(
        dev.do(
            "commit",
            message="Add greeting.txt with a friendly greeting",
            files=["greeting.txt"],
        ),
        "dev commit",
    )
    expect_ok(
        dev.do(
            "note",
            scope="note",
            task_id=task_id,
            text=(
                "greeting.txt written and committed on the task branch; "
                "opening the PR next, then self-verifying the acceptance "
                "criteria before submit."
            ),
        ),
        "dev progress note",
    )
    env = expect_ok(dev.flow("open_pr", task_id=task_id), "dev open_pr")
    state = _task_state(stack, ids.task_id)
    assert state["pr_number"], f"open_pr did not record a PR: {state} / {env}"

    # The i_am_done tracing gate demands: a during-work journal entry, the
    # dev_notes handoff section, a reflect entry, and an artifact referencing
    # every acceptance criterion (quoted verbatim in the decision note).
    expect_ok(
        dev.do(
            "note",
            scope="decision",
            task_id=task_id,
            text=(
                "Verified both acceptance criteria on the branch: "
                '"greeting.txt exists at the repo root" holds (file committed '
                'at the root), and "its content greets the reader" holds '
                "(content is a friendly hello). Decision: no README change "
                "needed; the greeting file is self-contained."
            ),
        ),
        "dev during-work decision note",
    )
    expect_ok(
        dev.do(
            "note",
            text="Handoff summary below (section carries the content).",
            scope="handoff",
            task_id=task_id,
            section={
                "summary": (
                    "Built the greeting module: greeting.txt added at the "
                    "repo root with a friendly greeting. Key change is one "
                    "additive file on the task branch; PR is open against "
                    "master; no risks beyond trivial content review."
                )
            },
        ),
        "dev handoff section",
    )
    expect_ok(
        dev.do(
            "note",
            scope="reflect",
            task_id=task_id,
            text=(
                "Reflection: implemented the greeting task exactly per plan — "
                "wrote the file, committed on the task branch, opened the PR, "
                "and self-verified both acceptance criteria against the "
                "committed content."
            ),
        ),
        "dev reflect note",
    )
    expect_ok(dev.flow("i_am_done", task_id=task_id), "dev i_am_done")
    assert _task_state(stack, ids.task_id)["status"] == "awaiting_qa"

    # --- QA: claim the review, inspect, pass --------------------------------
    qa = ScriptedAgent(stack, ids.qa_id, "be-qa", "qa")
    expect_ok(qa.flow("claim_review", task_id=task_id), "qa claim_review")
    expect_ok(
        qa.do(
            "note",
            scope="learning",
            task_id=task_id,
            text=(
                "Review learning: the greeting change is a single additive "
                "file; diff inspection on the PR confirms both acceptance "
                "criteria with no side effects on existing files."
            ),
        ),
        "qa learning note",
    )
    expect_ok(
        qa.flow(
            "pass_review",
            task_id=task_id,
            notes=(
                "Verified the PR diff on the fake origin: greeting.txt exists "
                "at the repo root and greets the reader. Both acceptance "
                "criteria hold; no regressions in the diff, and the branch "
                "contains exactly the one additive commit described."
            ),
            ac_verdicts=[
                (
                    "greeting.txt exists at the repo root — verified in the "
                    "PR diff: the file is added at the repository root."
                ),
                (
                    "its content greets the reader — verified: the committed "
                    "content is a friendly hello message."
                ),
            ],
        ),
        "qa pass_review",
    )
    assert _task_state(stack, ids.task_id)["status"] == "awaiting_documentation"

    # --- documenter: claim, document -----------------------------------------
    doc = ScriptedAgent(stack, ids.doc_id, "be-doc", "documenter")
    expect_ok(doc.flow("claim_doc_task", task_id=task_id), "doc claim_doc_task")
    expect_ok(
        doc.flow(
            "i_documented",
            task_id=task_id,
            files=["greeting.txt"],
            notes=(
                "Documented the greeting module: greeting.txt carries the "
                "user-facing greeting; no API surface changed, README "
                "untouched by design."
            ),
        ),
        "doc i_documented",
    )

    final = _task_state(stack, ids.task_id)
    assert final["status"] == "awaiting_pm_review", final
    assert final["docs_complete"] is True, final
