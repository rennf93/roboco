"""Reusable scripted-agent arcs + seeding for the e2e smoke scenarios.

The company is seeded ONCE per stack session (canonical slugs — the A2A
permission model resolves roles/teams from the static ``agents_config``
registry, so slugs must match it). Projects and tasks are seeded per test
with unique slugs so scenarios never collide on constraints.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

from tests.e2e_smoke.harness import E2EStack, ScriptedAgent, expect_error, expect_ok

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class Company:
    """Seeded canonical agents (ids) — one per stack session."""

    dev_id: Any
    qa_id: Any
    doc_id: Any
    cell_pm_id: Any
    main_pm_id: Any
    pr_reviewer_id: Any


_COMPANY_CACHE: dict[str, Company] = {}


def seed_company(stack: E2EStack) -> Company:
    """Seed the canonical agents once; return their ids on every call."""
    if "company" in _COMPANY_CACHE:
        return _COMPANY_CACHE["company"]

    from roboco.db.tables import AgentTable
    from roboco.models import AgentRole, AgentStatus, Team

    out = Company()

    async def _run(session: AsyncSession) -> None:
        def agent(slug: str, role: AgentRole, team: Team | None) -> AgentTable:
            row = AgentTable(
                id=uuid4(),
                name=slug,
                slug=slug,
                role=role,
                team=team,
                status=AgentStatus.ACTIVE,
                model_config={},
                system_prompt=slug,
                capabilities=[],
                permissions={},
                metrics={},
            )
            session.add(row)
            return row

        dev = agent("be-dev-1", AgentRole.DEVELOPER, Team.BACKEND)
        qa = agent("be-qa", AgentRole.QA, Team.BACKEND)
        doc = agent("be-doc", AgentRole.DOCUMENTER, Team.BACKEND)
        cell_pm = agent("be-pm", AgentRole.CELL_PM, Team.BACKEND)
        main_pm = agent("main-pm", AgentRole.MAIN_PM, None)
        reviewer = agent("pr-reviewer-1", AgentRole.PR_REVIEWER, None)
        await session.flush()
        out.dev_id = dev.id
        out.qa_id = qa.id
        out.doc_id = doc.id
        out.cell_pm_id = cell_pm.id
        out.main_pm_id = main_pm.id
        out.pr_reviewer_id = reviewer.id

    stack.run_db(_run)
    _COMPANY_CACHE["company"] = out
    return out


def seed_project(stack: E2EStack, company: Company) -> tuple[Any, str]:
    """Seed a project rooted at the shared bare origin; unique slug per test."""
    from roboco.db.tables import ProjectTable
    from roboco.models import Team
    from roboco.utils.crypto import encrypt_token

    slug = f"e2e-proj-{uuid4().hex[:6]}"
    holder: dict[str, Any] = {}

    async def _run(session: AsyncSession) -> None:
        project = ProjectTable(
            id=uuid4(),
            name=f"E2E {slug}",
            slug=slug,
            git_url=str(stack.origin),
            default_branch="master",
            protected_branches=["master"],
            assigned_cell=Team.BACKEND,
            created_by=company.main_pm_id,
            is_active=True,
            git_token_encrypted=encrypt_token("e2e-dummy-token"),
        )
        session.add(project)
        await session.flush()
        holder["id"] = project.id

    stack.run_db(_run)
    return holder["id"], slug


def seed_task(stack: E2EStack, **overrides: Any) -> Any:
    """Seed one task row; caller passes the fields that matter."""
    from roboco.db.tables import TaskTable
    from roboco.models import Team
    from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType

    fields: dict[str, Any] = {
        "id": uuid4(),
        "acceptance_criteria": ["done"],
        "status": TaskStatus.PENDING,
        "priority": 2,
        "task_type": TaskType.CODE,
        "nature": TaskNature.TECHNICAL,
        "estimated_complexity": Complexity.LOW,
        "team": Team.BACKEND,
        "confirmed_by_human": True,
    }
    fields.update(overrides)

    async def _run(session: AsyncSession) -> None:
        session.add(TaskTable(**fields))

    stack.run_db(_run)
    return fields["id"]


def task_state(stack: E2EStack, task_id: Any) -> dict[str, Any]:
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


def dispatcher_assign(stack: E2EStack, task_id: Any, agent_id: Any) -> None:
    """Mirror the dispatcher's claim-for-PM lane (_dispatch_pm_review_work):
    pr_pass clears ownership by design and the orchestrator re-claims the
    task for the owning PM before spawning it."""
    from roboco.db.tables import TaskTable
    from sqlalchemy import select

    async def _run(session: AsyncSession) -> None:
        row = (
            await session.execute(select(TaskTable).where(TaskTable.id == task_id))
        ).scalar_one()
        row.assigned_to = agent_id
        row.active_claimant_id = agent_id

    stack.run_db(_run)


def origin_branch(stack: E2EStack, name: str, start: str = "master") -> None:
    """Create + push a branch in the shared origin via the admin clone."""
    from tests.e2e_smoke.harness import _git

    admin = stack.github.admin_clone
    _git(admin, "fetch", "origin", "--prune")
    _git(admin, "checkout", "-B", name, f"origin/{start}")
    _git(admin, "push", "origin", name)


def origin_file(stack: E2EStack, branch: str, path: str) -> str | None:
    """Read a file's content at a branch tip in the origin, or None."""
    import subprocess

    from tests.e2e_smoke.harness import _git

    try:
        return _git(stack.github.origin, "show", f"{branch}:{path}")
    except subprocess.CalledProcessError:
        return None


# ---------------------------------------------------------------------------
# Arcs — each drives one role through one lifecycle segment, gates and all
# ---------------------------------------------------------------------------


def dev_arc(
    stack: E2EStack,
    company: Company,
    project_slug: str,
    task_id: Any,
    *,
    work: tuple[str, str] = ("greeting.txt", "Hello from the e2e smoke agent!\n"),
) -> None:
    """PENDING (pre-assigned) → awaiting_qa: claim, work, commit, PR, submit."""
    filename, content = work
    tid = str(task_id)
    dev = ScriptedAgent(stack, company.dev_id, "be-dev-1", "developer")

    env = expect_ok(dev.flow("give_me_work"), "dev give_me_work")
    assert env.get("task_id") == tid, f"expected task {tid}, got: {env}"

    def _claim() -> dict[str, Any]:
        return dev.flow(
            "i_will_work_on",
            task_id=tid,
            plan=(
                f"Create {filename} at the repository root with the required "
                "content, commit it on the task branch with the task-prefixed "
                "message, push the branch to origin, open the pull request "
                "against the base branch, and self-verify every acceptance "
                "criterion by re-reading the committed file content."
            ),
            steps=[
                {
                    "title": f"Write {filename}",
                    "description": (
                        f"Create {filename} at the repo root containing the "
                        "required content for the acceptance criteria."
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
                        "Open the pull request against the base branch and "
                        "re-read the file to confirm the criteria hold."
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

    # Real choreography: the composed claim succeeds and stays; the
    # post-claim tracing gate demands the claim-time note; the retry
    # short-circuits as re-entry.
    expect_error(_claim(), "tracing_gap", "dev first i_will_work_on")
    expect_ok(
        dev.do(
            "note",
            scope="note",
            task_id=tid,
            text=(
                "Initial assessment: a single additive text file at the repo "
                "root satisfies the acceptance criteria; no existing code is "
                "touched, so risk is minimal and the plan is a three-step "
                "write/commit/PR sequence."
            ),
        ),
        "dev note at claim",
    )
    expect_ok(_claim(), "dev i_will_work_on retry")

    workspace = stack.workspace_of(project_slug, "backend", "be-dev-1")
    workdir = workspace / ".worktrees" / tid[:8]
    assert workdir.is_dir(), f"per-task worktree missing at {workdir}"
    (workdir / filename).write_text(content)

    expect_ok(
        dev.do(
            "commit",
            message=f"feat: add {filename} with the required greeting content",
            files=[filename],
        ),
        "dev commit",
    )
    expect_ok(
        dev.do(
            "note",
            scope="note",
            task_id=tid,
            text=(
                f"{filename} written and committed on the task branch; "
                "opening the PR next, then self-verifying the acceptance "
                "criteria before submit."
            ),
        ),
        "dev progress note",
    )
    env = expect_ok(dev.flow("open_pr", task_id=tid), "dev open_pr")
    assert task_state(stack, task_id)["pr_number"], f"no PR recorded: {env}"

    criteria = _criteria_text(stack, task_id)
    expect_ok(
        dev.do(
            "note",
            scope="decision",
            task_id=tid,
            text=(
                "Verified every acceptance criterion on the branch: "
                + criteria
                + " — all hold against the committed content. Decision: no "
                "further changes needed; the file is self-contained."
            ),
        ),
        "dev during-work decision note",
    )
    expect_ok(
        dev.do(
            "note",
            text="Handoff summary below (section carries the content).",
            scope="handoff",
            task_id=tid,
            section={
                "summary": (
                    f"Built {filename} at the repo root on the task branch; "
                    "PR is open against the base branch; single additive "
                    "commit, no risks beyond trivial content review."
                )
            },
        ),
        "dev handoff section",
    )
    expect_ok(
        dev.do(
            "note",
            scope="reflect",
            task_id=tid,
            text=(
                "Reflection: implemented the task exactly per plan — wrote "
                "the file, committed on the task branch, opened the PR, and "
                "self-verified the acceptance criteria against the committed "
                "content."
            ),
        ),
        "dev reflect note",
    )
    expect_ok(dev.flow("i_am_done", task_id=tid), "dev i_am_done")
    assert task_state(stack, task_id)["status"] == "awaiting_qa"


def _criteria_text(stack: E2EStack, task_id: Any) -> str:
    from roboco.db.tables import TaskTable
    from sqlalchemy import select

    async def _run(session: AsyncSession) -> list[str]:
        row = (
            await session.execute(select(TaskTable).where(TaskTable.id == task_id))
        ).scalar_one()
        return list(row.acceptance_criteria or [])

    crits: list[str] = stack.run_db(_run)
    return "; ".join(f'"{c}"' for c in crits)


def qa_arc(stack: E2EStack, company: Company, task_id: Any) -> None:
    """awaiting_qa → awaiting_documentation."""
    tid = str(task_id)
    qa = ScriptedAgent(stack, company.qa_id, "be-qa", "qa")
    expect_ok(qa.flow("claim_review", task_id=tid), "qa claim_review")
    expect_ok(
        qa.do(
            "note",
            scope="learning",
            task_id=tid,
            text=(
                "Review learning: the change is a single additive file; diff "
                "inspection on the PR confirms the acceptance criteria with "
                "no side effects on existing files."
            ),
        ),
        "qa learning note",
    )

    async def _crits(session: AsyncSession) -> list[str]:
        from roboco.db.tables import TaskTable
        from sqlalchemy import select

        row = (
            await session.execute(select(TaskTable).where(TaskTable.id == task_id))
        ).scalar_one()
        return list(row.acceptance_criteria or [])

    criteria: list[str] = stack.run_db(_crits)
    expect_ok(
        qa.flow(
            "pass_review",
            task_id=tid,
            notes=(
                "Verified the PR diff on the origin: the committed change "
                "satisfies every acceptance criterion; no regressions in the "
                "diff, and the branch contains exactly the described commit."
            ),
            ac_verdicts=[
                f"{c} — verified against the PR diff on the origin." for c in criteria
            ],
        ),
        "qa pass_review",
    )
    assert task_state(stack, task_id)["status"] == "awaiting_documentation"


def doc_arc(stack: E2EStack, company: Company, task_id: Any, *, filename: str) -> None:
    """awaiting_documentation → awaiting_pm_review."""
    tid = str(task_id)
    doc = ScriptedAgent(stack, company.doc_id, "be-doc", "documenter")
    expect_ok(doc.flow("claim_doc_task", task_id=tid), "doc claim_doc_task")
    expect_ok(
        doc.flow(
            "i_documented",
            task_id=tid,
            files=[filename],
            notes=(
                f"Documented the change: {filename} carries the user-facing "
                "content; no API surface changed, README untouched by design."
            ),
        ),
        "doc i_documented",
    )
    state = task_state(stack, task_id)
    assert state["status"] == "awaiting_pm_review", state
    assert state["docs_complete"] is True, state
