"""Tier 3 — end-to-end happy paths against the real test DB.

Each test exercises the spec → choreographer → TaskService → DB stack
with Alembic migrations applied. Catches "spec says X, DB constraint
says Y" mismatches the unit-tier parametrized parity suite cannot
detect.

Companion to ``tests/integration/test_full_lifecycle_real_db.py``.
That file walks one task through the dev chain end to end; this file
isolates each major lifecycle path into
its own test so a regression on, say, QA-fail does not also blow up
the doc-handoff test.

Mocks: only the git layer (workspace + PR ops) is stubbed because the
test DB has no checkout. The spec, choreographer, VerbRunner, and
TaskService are real — those are the layers Task 30 verifies.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.foundation.policy.lifecycle import Status
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    TaskNature,
    TaskStatus,
    TaskType,
    Team,
)
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps
from roboco.services.task import TaskService

# A developer fresh claim must carry a substantive step checklist.
_STEPS = [
    {
        "title": "Implement the change",
        "description": (
            "edit the target file, add tests, run them, and stage the "
            "change for commit on the task branch"
        ),
    }
]

_GOOD_PLAN = (
    "Implement the task on its feature branch: edit the target module, add or "
    "update unit tests covering the change, run the suite locally, then commit "
    "on the branch and open a PR. Keep the diff focused on the acceptance "
    "criteria and verify it before submitting for QA."
)
_GOOD_TC = ["Follow the existing module's patterns; keep the change minimal."]
_GOOD_RISKS = [
    {
        "risk": "Scope creep balloons the diff and slows review.",
        "mitigation": "Touch only the files the acceptance criteria require.",
    }
]

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


_BRANCH = "feature/backend/healthz"
_PR_NUMBER = 8
_PR_URL = "https://github.com/example/life/pull/8"
# QA notes must clear settings.qa_notes_min_chars (default 80).
_QA_PASS_NOTES = (
    "Reviewed the diff; route returns 200 OK with timestamp. Tests cover "
    "both acceptance criteria. Approving."
)
_QA_FAIL_NOTES_PREFIX = (
    "Reviewed the diff; route returns 500 on the timestamp branch and the "
    "second acceptance criterion is not exercised by the new tests. "
)


class _StubGit:
    """Deterministic GitService stub.

    Mirrors ``test_full_lifecycle_real_db.py``'s _StubGit. Mutates the
    test's TaskTable row directly so the choreographer reads consistent
    pr_number / commits state without disk or network I/O.
    """

    def __init__(self, session: Any, task: TaskTable) -> None:
        self._session = session
        self._task = task

    async def commit(
        self,
        *,
        branch_name: str,
        message: str,
        task_id: UUID,
        files: list[str] | None = None,
        actor_agent_id: Any = None,
    ) -> dict[str, Any]:
        del branch_name, files, actor_agent_id
        sha = uuid4().hex[:40]
        commits = list(self._task.commits or [])
        commits.append({"sha": sha, "message": message, "task_id": str(task_id)})
        self._task.commits = commits
        await self._session.flush()
        return {
            "sha": sha,
            "message": message,
            "files_changed": 1,
            "insertions": 1,
            "deletions": 0,
        }

    async def push_branch(
        self, branch_name: str, *, actor_agent_id: Any = None
    ) -> tuple[str, int]:
        del branch_name, actor_agent_id
        return ("ok", 0)

    async def push_task_branch(self, agent_id: UUID, task_id: UUID) -> int:
        del agent_id, task_id
        return 0

    async def create_pr(
        self,
        branch_name: str,
        *,
        parent: str,
        is_root_pr: bool,
        actor_agent_id: Any = None,
    ) -> dict[str, Any]:
        del branch_name, parent, actor_agent_id
        self._task.pr_number = _PR_NUMBER
        self._task.pr_url = _PR_URL
        self._task.pr_created = True
        await self._session.flush()
        return {"pr_number": _PR_NUMBER, "pr_url": _PR_URL, "is_root_pr": is_root_pr}

    async def diff(
        self, *, branch_name: str, base: Any = None, actor_agent_id: Any = None
    ) -> str:
        del branch_name, base, actor_agent_id
        return "stub diff"

    async def list_changed_files(
        self, *, branch_name: str, base: Any = None, actor_agent_id: Any = None
    ) -> list[str]:
        del branch_name, base, actor_agent_id
        return []

    async def pr_target(self, pr_number: int, *, actor_agent_id: Any = None) -> str:
        del pr_number, actor_agent_id
        return "main"

    async def pr_merge(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        return {
            "merged": True,
            "sha": uuid4().hex[:40],
            "merge_commit_sha": uuid4().hex[:40],
        }


def _mock_evidence_repo() -> Any:
    repo = AsyncMock()
    for method in (
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
        "journal_highlights_for_task",
    ):
        getattr(repo, method).return_value = []
    return repo


def _mock_journal_with_reflect() -> Any:
    """Journal stub that reports reflect/learning/decision entries present.

    ``latest_decision_at`` is anchored to ``datetime.now(UTC)`` so the C8
    recency window on the PM-decision gate accepts it.
    """
    journal = AsyncMock()
    journal.has_reflect_for_task.return_value = True
    journal.has_learning_for_task.return_value = True
    journal.has_decision_for_task.return_value = True
    journal.has_struggle_for_task.return_value = False
    journal.latest_decision_at.return_value = datetime.now(UTC)
    return journal


def _mock_work_session() -> Any:
    """WorkSession stub: stable file list, no unpushed commits."""
    ws = AsyncMock()
    ws.files_changed.return_value = ["roboco/api/routes/health.py"]
    ws.has_unpushed_commits.return_value = False
    return ws


def _build_choreographer(
    db_session: Any, task: TaskTable, task_service: TaskService
) -> Choreographer:
    """Wire a real Choreographer with the supplied TaskService + stubbed git.

    Caller owns the TaskService so it can use it for direct DB reads
    (``task_service.get(task_id)``) — sharing one instance keeps the
    session contract clean and avoids "two TaskServices, two views"
    surprises.
    """
    deps = ChoreographerDeps(
        task=task_service,
        work_session=_mock_work_session(),
        git=_StubGit(db_session, task),
        a2a=AsyncMock(),
        journal=_mock_journal_with_reflect(),
        audit=AsyncMock(),
        evidence_repo=_mock_evidence_repo(),
    )
    return Choreographer(deps)


async def _seed_agents_and_project(
    db_session: AsyncSession,
) -> dict[str, Any]:
    """Seed system + project + dev/qa/doc/cell_pm agents.

    Slugs match ``agents_config.ESCALATION_CHAIN`` so ``i_am_blocked``
    and ``escalate_up`` find a real escalation target. The agents-config
    chain is the source of truth at runtime; matching it here exercises
    the same lookup the gateway uses in production.
    """
    system_agent = AgentTable(
        id=uuid4(),
        name="System",
        slug=f"system-{uuid4().hex[:8]}",
        role=AgentRole.SYSTEM,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="system",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(system_agent)
    await db_session.flush()

    project = ProjectTable(
        id=uuid4(),
        name="Lifecycle Test Project",
        slug=f"life-{uuid4().hex[:8]}",
        git_url="https://github.com/example/life.git",
        default_branch="main",
        protected_branches=["main"],
        assigned_cell=Team.BACKEND,
        created_by=system_agent.id,
        is_active=True,
    )
    db_session.add(project)
    await db_session.flush()

    dev_agent = AgentTable(
        id=uuid4(),
        name="BE Dev 1",
        slug="be-dev-1",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=["python"],
        permissions={},
        metrics={},
    )
    qa_agent = AgentTable(
        id=uuid4(),
        name="BE QA",
        slug="be-qa",
        role=AgentRole.QA,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="qa",
        capabilities=["review"],
        permissions={},
        metrics={},
    )
    doc_agent = AgentTable(
        id=uuid4(),
        name="BE Doc",
        slug="be-doc",
        role=AgentRole.DOCUMENTER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="doc",
        capabilities=["docs"],
        permissions={},
        metrics={},
    )
    cell_pm_agent = AgentTable(
        id=uuid4(),
        name="BE Cell PM",
        slug="be-pm",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="cell_pm",
        capabilities=["coord"],
        permissions={},
        metrics={},
    )
    db_session.add_all([dev_agent, qa_agent, doc_agent, cell_pm_agent])
    await db_session.flush()

    return {
        "system_agent": system_agent,
        "project": project,
        "dev_agent": dev_agent,
        "qa_agent": qa_agent,
        "doc_agent": doc_agent,
        "cell_pm_agent": cell_pm_agent,
    }


def _build_task(
    *,
    project_id: UUID,
    creator_id: UUID,
    assignee_id: UUID | None,
    status: TaskStatus,
) -> TaskTable:
    """Construct a backend code-typed task pinned to ``status``.

    ``acceptance_criteria_status`` carries the stub artefact rows the
    pre-merge gate inspects; supplying them here keeps the per-test
    setup readable.
    """
    return TaskTable(
        id=uuid4(),
        title="Add /healthz endpoint",
        description="Return 200 OK from /healthz",
        status=status,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        team=Team.BACKEND,
        project_id=project_id,
        created_by=creator_id,
        assigned_to=assignee_id,
        branch_name=_BRANCH,
        acceptance_criteria=["Returns 200", "Includes timestamp"],
        acceptance_criteria_status=[
            {"criterion": "Returns 200", "referencing_artifact_id": "stub"},
            {"criterion": "Includes timestamp", "referencing_artifact_id": "stub"},
        ],
    )


@pytest_asyncio.fixture
async def lifecycle_setup(
    db_session: AsyncSession,
) -> AsyncIterator[dict[str, Any]]:
    """Seed agents + project + a single PENDING task assigned to the dev."""
    seeded = await _seed_agents_and_project(db_session)
    task = _build_task(
        project_id=seeded["project"].id,
        creator_id=seeded["system_agent"].id,
        assignee_id=seeded["dev_agent"].id,
        status=TaskStatus.PENDING,
    )
    db_session.add(task)
    await db_session.flush()
    seeded["task"] = task
    yield seeded


# ---------------------------------------------------------------------------
# 1. Dev path: pending → claimed → in_progress → verifying → awaiting_qa
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dev_full_chain_through_awaiting_qa(
    db_session: AsyncSession, lifecycle_setup: dict[str, Any]
) -> None:
    """pending → claimed → in_progress → verifying → awaiting_qa.

    Drives ``i_will_work_on`` (claim+plan+start), a stubbed commit,
    ``open_pr``, then ``i_am_done`` which auto-runs submit_verification
    + submit_qa. Asserts the final DB row sits at ``awaiting_qa``.
    """
    task = lifecycle_setup["task"]
    dev_agent = lifecycle_setup["dev_agent"]
    task_service = TaskService(db_session)
    stub_git = _StubGit(db_session, task)
    deps = ChoreographerDeps(
        task=task_service,
        work_session=_mock_work_session(),
        git=stub_git,
        a2a=AsyncMock(),
        journal=_mock_journal_with_reflect(),
        audit=AsyncMock(),
        evidence_repo=_mock_evidence_repo(),
    )
    c = Choreographer(deps)

    env = await c.i_will_work_on(
        dev_agent.id,
        task.id,
        plan=_GOOD_PLAN,
        steps=_STEPS,
        technical_considerations=_GOOD_TC,
        risks=_GOOD_RISKS,
    )
    assert env.error is None, f"i_will_work_on failed: {env.message}"
    assert env.status == Status.IN_PROGRESS.value

    # Commit + record progress so open_pr's commits-precondition holds.
    await stub_git.commit(
        branch_name=_BRANCH,
        message=f"[{str(task.id)[:8]}] feat(api): add /healthz",
        task_id=task.id,
    )
    await task_service.add_progress(task.id, dev_agent.id, "implemented /healthz")

    env = await c.open_pr(dev_agent.id, task.id)
    assert env.error is None, f"open_pr failed: {env.message}"

    # i_am_done now obligates the developer's dev_notes section — the agent
    # fills it via note(scope='handoff') first. record_section_note is the
    # service call that write-path makes.
    await task_service.record_section_note(
        task.id,
        "developer",
        {"summary": "Implemented /healthz and added a test for the happy path."},
    )

    env = await c.i_am_done(dev_agent.id, task.id, "tests pass; route works")
    assert env.error is None, f"i_am_done failed: {env.message}"
    assert env.status == Status.AWAITING_QA.value

    final = await task_service.get(task.id)
    assert final is not None
    assert str(final.status) == Status.AWAITING_QA.value
    # i_am_done auto-runs submit_qa, which hands the task off to the
    # backend QA agent (production behaviour — see ``_notify_qa``).
    # Resolve via the same lookup the choreographer uses so the
    # assertion is robust to other test fixtures that may have seeded
    # additional QA agents on the BACKEND team (e.g. smoke_test_batch
    # commits its agents, so they outlive their session).
    resolved_qa = await task_service.qa_agent_for_team(Team.BACKEND)
    assert resolved_qa is not None
    assert final.assigned_to == resolved_qa.id


# ---------------------------------------------------------------------------
# 2. QA pass path: awaiting_qa → claimed → awaiting_documentation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qa_pass_path(
    db_session: AsyncSession, lifecycle_setup: dict[str, Any]
) -> None:
    """awaiting_qa → claim_review → pass_review → awaiting_documentation.

    ``claim_review`` keeps status at AWAITING_QA (specialised qa_claim
    sets assignment without transitioning) so the spec's source-status
    requirement on ``qa_pass`` still matches downstream.
    """
    task = lifecycle_setup["task"]
    qa_agent = lifecycle_setup["qa_agent"]
    doc_agent = lifecycle_setup["doc_agent"]

    # Pin the task at awaiting_qa with a PR + commit so the QA gates
    # (pr exists, commits non-empty) all pass.
    task.status = TaskStatus.AWAITING_QA
    task.pr_number = _PR_NUMBER
    task.pr_url = _PR_URL
    task.commits = [
        {"sha": uuid4().hex[:40], "message": "feat: /healthz", "task_id": str(task.id)}
    ]
    task.self_verified = True
    await db_session.flush()

    task_service = TaskService(db_session)
    c = _build_choreographer(db_session, task, task_service)

    env = await c.claim_review(qa_agent.id, task.id)
    assert env.error is None, f"claim_review failed: {env.message}"
    after_claim = await task_service.get(task.id)
    assert after_claim is not None
    assert str(after_claim.status) == Status.AWAITING_QA.value
    assert after_claim.assigned_to == qa_agent.id

    env = await c.pass_review(
        qa_agent.id,
        task.id,
        notes=_QA_PASS_NOTES,
        ac_verdicts=[f"verified: {crit}" for crit in task.acceptance_criteria],
    )
    assert env.error is None, f"pass_review failed: {env.message}"
    assert env.status == Status.AWAITING_DOCUMENTATION.value

    final = await task_service.get(task.id)
    assert final is not None
    assert str(final.status) == Status.AWAITING_DOCUMENTATION.value
    # pass_review reassigns to the team's documenter for handoff. Look
    # up via the same path the choreographer uses (robust to other
    # tests' committed BACKEND documenters).
    resolved_doc = await task_service.documenter_for_team(Team.BACKEND)
    assert resolved_doc is not None
    assert final.assigned_to == resolved_doc.id
    del doc_agent  # asserted indirectly via documenter_for_team.


# ---------------------------------------------------------------------------
# 3. QA fail path: awaiting_qa → claimed → needs_revision
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qa_fail_path(
    db_session: AsyncSession, lifecycle_setup: dict[str, Any]
) -> None:
    """awaiting_qa → claim_review → fail_review(issues) → needs_revision.

    ``fail_review`` reassigns to the original developer so they can
    revise; that lookup walks ``quick_context``'s
    ``original_developer:<slug>`` marker, which the dev path stamps
    on i_will_work_on. Here we set it directly so the assertion is
    deterministic without re-running the dev chain.
    """
    task = lifecycle_setup["task"]
    qa_agent = lifecycle_setup["qa_agent"]
    dev_agent = lifecycle_setup["dev_agent"]

    task.status = TaskStatus.AWAITING_QA
    task.pr_number = _PR_NUMBER
    task.pr_url = _PR_URL
    task.commits = [
        {"sha": uuid4().hex[:40], "message": "feat: /healthz", "task_id": str(task.id)}
    ]
    task.self_verified = True
    # ``extract_original_developer`` parses a UUID off this line; the
    # spec layer's slug-based self-review check is a separate code path
    # (``_extract_original_developer`` in qa.py) which only fires when
    # an actor's slug equals this value, so a UUID here doesn't trip it.
    task.orchestration_markers = {"original_developer": str(dev_agent.id)}
    await db_session.flush()

    task_service = TaskService(db_session)
    c = _build_choreographer(db_session, task, task_service)

    env = await c.claim_review(qa_agent.id, task.id)
    assert env.error is None, f"claim_review failed: {env.message}"

    issues = ["Returns 500 on the timestamp branch", "Missing test for the second AC"]
    # fail_review's notes are derived from issues; QA pass-gate also
    # requires notes >= 80 chars, so we send a leading explanation as
    # the issues list — the verb concatenates them and easily clears
    # the threshold.
    long_issues = [_QA_FAIL_NOTES_PREFIX + issues[0], issues[1]]
    env = await c.fail_review(qa_agent.id, task.id, issues=long_issues)
    assert env.error is None, f"fail_review failed: {env.message}"
    assert env.status == Status.NEEDS_REVISION.value

    final = await task_service.get(task.id)
    assert final is not None
    assert str(final.status) == Status.NEEDS_REVISION.value
    # fail_qa reassigns to the original developer so they can revise.
    assert final.assigned_to == dev_agent.id


# ---------------------------------------------------------------------------
# 4. Doc path: awaiting_documentation → claimed → awaiting_pm_review
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_doc_path(
    db_session: AsyncSession, lifecycle_setup: dict[str, Any]
) -> None:
    """awaiting_documentation → claim_doc_task → i_documented → awaiting_pm_review.

    ``claim_doc_task`` keeps status at AWAITING_DOCUMENTATION (doc_claim
    is assignment-only, mirroring qa_claim). ``i_documented`` flips to
    AWAITING_PM_REVIEW and reassigns to the cell PM for that team.
    """
    task = lifecycle_setup["task"]
    doc_agent = lifecycle_setup["doc_agent"]
    cell_pm_agent = lifecycle_setup["cell_pm_agent"]

    task.status = TaskStatus.AWAITING_DOCUMENTATION
    task.pr_number = _PR_NUMBER
    task.pr_url = _PR_URL
    task.pr_created = True
    task.qa_verified = True
    task.assigned_to = None  # documenter must claim from unassigned.
    task.commits = [
        {"sha": uuid4().hex[:40], "message": "feat: /healthz", "task_id": str(task.id)}
    ]
    await db_session.flush()

    task_service = TaskService(db_session)
    c = _build_choreographer(db_session, task, task_service)

    env = await c.claim_doc_task(doc_agent.id, task.id)
    assert env.error is None, f"claim_doc_task failed: {env.message}"
    after_claim = await task_service.get(task.id)
    assert after_claim is not None
    assert str(after_claim.status) == Status.AWAITING_DOCUMENTATION.value
    assert after_claim.assigned_to == doc_agent.id

    env = await c.i_documented(
        doc_agent.id,
        task.id,
        notes="Documented /healthz behaviour in docs/api/health.md",
        files=["docs/api/health.md"],
    )
    assert env.error is None, f"i_documented failed: {env.message}"
    assert env.status == Status.AWAITING_PM_REVIEW.value

    final = await task_service.get(task.id)
    assert final is not None
    assert str(final.status) == Status.AWAITING_PM_REVIEW.value
    # i_documented hands off to the cell PM for the team. Resolve via
    # the same lookup the choreographer uses (robust to other tests'
    # committed BACKEND cell PMs).
    resolved_pm = await task_service.cell_pm_for_team(Team.BACKEND)
    assert resolved_pm is not None
    assert final.assigned_to == resolved_pm.id
    del cell_pm_agent  # asserted indirectly via cell_pm_for_team.


# ---------------------------------------------------------------------------
# 5. PM complete (Cell PM, simple task): awaiting_pm_review → completed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pm_complete_simple_task(
    db_session: AsyncSession, lifecycle_setup: dict[str, Any]
) -> None:
    """awaiting_pm_review → cell_pm complete → completed.

    A cell PM completing a non-root awaiting_pm_review task transitions
    straight to COMPLETED — there is no cell→main escalation in
    ``complete`` (the cell→main hand-off, when intended, uses
    ``submit_up``).
    """
    task = lifecycle_setup["task"]
    cell_pm_agent = lifecycle_setup["cell_pm_agent"]

    task.status = TaskStatus.AWAITING_PM_REVIEW
    task.pr_number = _PR_NUMBER
    task.pr_url = _PR_URL
    task.pr_created = True
    task.qa_verified = True
    task.docs_complete = True
    task.assigned_to = cell_pm_agent.id
    task.commits = [
        {"sha": uuid4().hex[:40], "message": "feat: /healthz", "task_id": str(task.id)}
    ]
    await db_session.flush()

    task_service = TaskService(db_session)
    c = _build_choreographer(db_session, task, task_service)

    env = await c.complete(
        cell_pm_agent.id, task.id, notes="LGTM — merging the leaf PR."
    )
    assert env.error is None, f"complete failed: {env.message}"
    assert env.status == Status.COMPLETED.value

    final = await task_service.get(task.id)
    assert final is not None
    assert str(final.status) == Status.COMPLETED.value


async def _seed_reviewer(db_session: AsyncSession) -> AgentTable:
    """Add + flush a backend in-path PR-review-gate reviewer for the gate tests.

    Flushed here so a later ``task.assigned_to = reviewer.id`` update can't race
    the reviewer INSERT in the same unit-of-work and trip the FK constraint.
    """
    reviewer = AgentTable(
        id=uuid4(),
        name="BE PR Reviewer",
        slug="be-pr-reviewer",
        role=AgentRole.PR_REVIEWER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="reviewer",
        capabilities=["review"],
        permissions={},
        metrics={},
    )
    db_session.add(reviewer)
    await db_session.flush()
    return reviewer


@pytest.mark.asyncio
async def test_pr_review_gate_pass_path(
    db_session: AsyncSession, lifecycle_setup: dict[str, Any]
) -> None:
    """in_progress → submit_for_review → awaiting_pr_review → pr_gate_claim →
    pr_pass → awaiting_pm_review.

    Drives the new TaskService transitions through the real enforcement layer
    (validate_task_transition + validate_git_requirements) and the DB — the
    layers the mocked unit tests can't exercise.
    """
    task = lifecycle_setup["task"]
    cell_pm_agent = lifecycle_setup["cell_pm_agent"]
    reviewer = await _seed_reviewer(db_session)
    task.status = TaskStatus.IN_PROGRESS
    task.pr_number = _PR_NUMBER
    task.pr_url = _PR_URL
    task.pr_created = True
    task.assigned_to = cell_pm_agent.id
    task.claimed_by = cell_pm_agent.id
    await db_session.flush()

    svc = TaskService(db_session)
    reviewer_id = UUID(str(reviewer.id))

    entered = await svc.submit_for_review(
        cell_pm_agent.id, task.id, notes="cell assembled; entering review"
    )
    assert entered is not None
    assert str(entered.status) == Status.AWAITING_PR_REVIEW.value

    claimed = await svc.pr_gate_claim(reviewer_id, task.id)
    assert claimed is not None
    assert str(claimed.status) == Status.AWAITING_PR_REVIEW.value
    assert claimed.assigned_to == reviewer.id

    passed = await svc.pr_pass(reviewer_id, task.id, notes="integration verified")
    assert passed is not None
    assert str(passed.status) == Status.AWAITING_PM_REVIEW.value
    assert passed.assigned_to is None  # cleared so the PM-closure dispatch routes

    final = await svc.get(task.id)
    assert final is not None
    assert str(final.status) == Status.AWAITING_PM_REVIEW.value


@pytest.mark.asyncio
async def test_pr_review_gate_fail_path(
    db_session: AsyncSession, lifecycle_setup: dict[str, Any]
) -> None:
    """awaiting_pr_review → pr_fail → needs_revision, with issues recorded for
    the PM's revision."""
    task = lifecycle_setup["task"]
    reviewer = await _seed_reviewer(db_session)
    task.status = TaskStatus.AWAITING_PR_REVIEW
    task.assigned_to = reviewer.id
    task.claimed_by = reviewer.id
    await db_session.flush()

    svc = TaskService(db_session)
    failed = await svc.pr_fail(
        UUID(str(reviewer.id)),
        task.id,
        notes="integration seam is broken",
        issues=["FE sends task_id as a string where the BE requires a UUID"],
    )
    assert failed is not None
    assert str(failed.status) == Status.NEEDS_REVISION.value
    # The reviewer's issues land in its OWN slot now (not dev_notes/qa_notes).
    assert "string where the BE requires a UUID" in (failed.pr_reviewer_notes or "")
    assert (failed.notes_structured or {}).get("pr_review", {}).get("verdict") == (
        "failed"
    )


# ---------------------------------------------------------------------------
# 6. PM escalate: awaiting_pm_review → awaiting_ceo_approval
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pm_escalate_to_ceo_path(
    db_session: AsyncSession, lifecycle_setup: dict[str, Any]
) -> None:
    """awaiting_pm_review → main_pm complete on a root task → awaiting_ceo_approval.

    Main PM completing a root task (no parent) opens the master PR if
    needed and calls ``task.escalate_to_ceo``, leaving the task at
    AWAITING_CEO_APPROVAL with ``assigned_to=None``. CEO approval is
    human-in-the-loop (UI-driven), so the test stops there.
    """
    task = lifecycle_setup["task"]
    project = lifecycle_setup["project"]
    system_agent = lifecycle_setup["system_agent"]

    main_pm_agent = AgentTable(
        id=uuid4(),
        name="Main PM",
        slug="main-pm",
        role=AgentRole.MAIN_PM,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="main_pm",
        capabilities=["coord"],
        permissions={},
        metrics={},
    )
    db_session.add(main_pm_agent)
    await db_session.flush()
    del project, system_agent  # only needed for fixture wiring above.

    task.status = TaskStatus.AWAITING_PM_REVIEW
    task.pr_number = _PR_NUMBER
    task.pr_url = _PR_URL
    task.pr_created = True
    task.qa_verified = True
    task.docs_complete = True
    task.parent_task_id = None  # explicit — escalate_to_ceo refuses subtasks.
    task.assigned_to = main_pm_agent.id
    task.commits = [
        {"sha": uuid4().hex[:40], "message": "feat: /healthz", "task_id": str(task.id)}
    ]
    await db_session.flush()

    task_service = TaskService(db_session)
    c = _build_choreographer(db_session, task, task_service)

    # SQLAlchemy column-typed `id` attributes need an explicit UUID
    # cast for mypy under the project's strict config — the values are
    # already real ``uuid.UUID`` at runtime.
    env = await c.complete(
        UUID(str(main_pm_agent.id)),
        UUID(str(task.id)),
        notes="Root task ready for CEO approval — escalating.",
    )
    assert env.error is None, f"complete failed: {env.message}"
    assert env.status == Status.AWAITING_CEO_APPROVAL.value

    final = await task_service.get(task.id)
    assert final is not None
    assert str(final.status) == Status.AWAITING_CEO_APPROVAL.value
    # main_pm_complete clears assigned_to so the orchestrator does not
    # respawn an agent while the task waits on the human CEO.
    assert final.assigned_to is None


# ---------------------------------------------------------------------------
# 7. Block + unblock + restore: in_progress → blocked → in_progress
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_block_then_unblock_restore(
    db_session: AsyncSession, lifecycle_setup: dict[str, Any]
) -> None:
    """in_progress → i_am_blocked → unblock(restore=True) → in_progress.

    ``i_am_blocked`` runs the spec's ``block`` action which delegates
    to ``task_service.escalate``: the task is reassigned to the dev's
    escalation target (``be-pm`` per ``ESCALATION_CHAIN``) and marked
    BLOCKED with the original dev stashed in ``blocker_raised_by``.
    PM ``unblock(restore=True)`` then falls through to the legacy
    ``unblock`` path (no ``pre_block_state`` snapshot exists for chain
    escalations), restoring assignment to the dev and flipping back
    to IN_PROGRESS.
    """
    task = lifecycle_setup["task"]
    dev_agent = lifecycle_setup["dev_agent"]
    cell_pm_agent = lifecycle_setup["cell_pm_agent"]

    task_service = TaskService(db_session)
    c = _build_choreographer(db_session, task, task_service)

    # Drive into in_progress via the real claim+start sequence.
    env = await c.i_will_work_on(
        dev_agent.id,
        task.id,
        plan=_GOOD_PLAN,
        steps=_STEPS,
        technical_considerations=_GOOD_TC,
        risks=_GOOD_RISKS,
    )
    assert env.error is None, f"i_will_work_on failed: {env.message}"
    assert env.status == Status.IN_PROGRESS.value

    env = await c.i_am_blocked(
        dev_agent.id,
        task.id,
        reason="external dependency on auth library upgrade",
    )
    assert env.error is None, f"i_am_blocked failed: {env.message}"
    assert env.status == Status.BLOCKED.value

    blocked = await task_service.get(task.id)
    assert blocked is not None
    assert str(blocked.status) == Status.BLOCKED.value
    # Escalation reassigns to the cell PM (be-pm) and stashes the dev
    # as blocker_raised_by so unblock can hand the task back.
    assert blocked.assigned_to == cell_pm_agent.id
    assert blocked.blocker_raised_by == dev_agent.id

    env = await c.unblock(
        cell_pm_agent.id, task.id, "block resolved upstream; restoring", restore=True
    )
    assert env.error is None, f"unblock failed: {env.message}"
    assert env.status == Status.IN_PROGRESS.value

    restored = await task_service.get(task.id)
    assert restored is not None
    assert str(restored.status) == Status.IN_PROGRESS.value
    # legacy unblock restores assigned_to from blocker_raised_by — the
    # original dev gets the task back so the orchestrator respawns them.
    assert restored.assigned_to == dev_agent.id


# ---------------------------------------------------------------------------
# 8. Pause + resume: in_progress → paused → in_progress
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_then_resume(
    db_session: AsyncSession, lifecycle_setup: dict[str, Any]
) -> None:
    """in_progress → i_am_idle (auto-pause) → resume → in_progress.

    There is no agent-driven ``pause`` verb. ``i_am_idle`` auto-pauses
    every in_progress task the agent owns so the closure dispatcher can
    wake them on respawn. ``resume`` (composes=("resume",)) then flips
    the same task back to IN_PROGRESS for the same assignee.
    """
    task = lifecycle_setup["task"]
    dev_agent = lifecycle_setup["dev_agent"]

    task_service = TaskService(db_session)
    c = _build_choreographer(db_session, task, task_service)

    env = await c.i_will_work_on(
        dev_agent.id,
        task.id,
        plan=_GOOD_PLAN,
        steps=_STEPS,
        technical_considerations=_GOOD_TC,
        risks=_GOOD_RISKS,
    )
    assert env.error is None, f"i_will_work_on failed: {env.message}"
    assert env.status == Status.IN_PROGRESS.value

    env = await c.i_am_idle(dev_agent.id)
    assert env.error is None, f"i_am_idle failed: {env.message}"
    assert env.status == "idle"

    paused = await task_service.get(task.id)
    assert paused is not None
    assert str(paused.status) == Status.PAUSED.value
    # Auto-pause keeps assigned_to so resume can find the same claimant.
    assert paused.assigned_to == dev_agent.id

    env = await c.resume(dev_agent.id, task.id)
    assert env.error is None, f"resume failed: {env.message}"
    assert env.status == Status.IN_PROGRESS.value

    resumed = await task_service.get(task.id)
    assert resumed is not None
    assert str(resumed.status) == Status.IN_PROGRESS.value
    assert resumed.assigned_to == dev_agent.id
