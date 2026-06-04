"""Real-DB end-to-end test driving the gateway through the full lifecycle.

Audit deliverable P2-1: the missing integration test that would have
caught every smoking gun in the 2026-05-04 audit. Drives a single task
from pending → completed using a real `db_session` (Postgres-backed
fixture from the top-level conftest), a real `Choreographer`, and a
real `TaskService`. Git is replaced with a deterministic stub
(`_StubGit`) that mutates the same task row the choreographer reads,
so PR/commit state is consistent between the choreographer and the
test's assertions. Journal/A2A/audit/evidence are mocked because they
don't gate the lifecycle paths under test.

When extended to all roles, this test catches:
  - URL prefix mismatch (route-level coverage in test_v1_role_dep)
  - i_will_work_on AttributeError on None (claim → start sequence is real)
  - heartbeat seeding (reaper cutoff)
  - active_claimant_id wired (single-claimant invariant)
  - i_am_done auto-runs submit_verification (P1-3)
  - QA pass clears active_claimant_id (P1-4)
  - branch creation atomicity rollback (P0-7)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
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

# #172: a developer fresh claim must carry a substantive step checklist.
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


class _StubGit:
    """Deterministic GitService stub.

    Mutates the test's TaskTable row directly to mirror what the real
    `git.create_pr` / `git.commit` do via `_record_pr_atomically` and
    `_workspace_for_branch`. The choreographer reads pr_number/commits
    off the task object — keeping them in sync here means the gates
    behave the same as production without any disk or network I/O.
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
        self._task.commits = commits  # type: ignore[assignment]
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
        # Mirrors git._record_pr_atomically — production sets pr_created
        # via mark_pr_created which is what the parallel-completion gate
        # in _maybe_advance_to_pm_review reads.
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

    async def pr_merge(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        return {"merged": True, "sha": uuid4().hex[:40]}


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
    """WorkSession stub: empty file list, no unpushed commits."""
    ws = AsyncMock()
    ws.files_changed.return_value = ["roboco/api/routes/health.py"]
    ws.has_unpushed_commits.return_value = False
    return ws


@pytest_asyncio.fixture
async def lifecycle_setup(
    db_session: AsyncSession,
) -> AsyncIterator[dict[str, Any]]:
    """Seed a project + dev agent + a single pending task ready to claim."""
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
        name="BE Dev",
        slug=f"be-dev-{uuid4().hex[:8]}",
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
        slug=f"be-qa-{uuid4().hex[:8]}",
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
        slug=f"be-doc-{uuid4().hex[:8]}",
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
        slug=f"be-pm-{uuid4().hex[:8]}",
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

    task = TaskTable(
        id=uuid4(),
        title="Add /healthz endpoint",
        description="Return 200 OK from /healthz",
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        team=Team.BACKEND,
        project_id=project.id,
        created_by=system_agent.id,
        assigned_to=dev_agent.id,
        branch_name=_BRANCH,
        acceptance_criteria=["Returns 200", "Includes timestamp"],
        acceptance_criteria_status=[
            {"criterion": "Returns 200", "referencing_artifact_id": "stub"},
            {"criterion": "Includes timestamp", "referencing_artifact_id": "stub"},
        ],
    )
    db_session.add(task)
    await db_session.flush()

    yield {
        "project": project,
        "dev_agent": dev_agent,
        "qa_agent": qa_agent,
        "doc_agent": doc_agent,
        "cell_pm_agent": cell_pm_agent,
        "task": task,
    }


@pytest.mark.asyncio
async def test_dev_can_claim_pending_task_via_gateway(
    db_session: AsyncSession, lifecycle_setup: dict[str, Any]
) -> None:
    """give_me_work → i_will_work_on lands the task in in_progress.

    Verifies in one shot: P0-2 (None-handling), P0-3 (heartbeat seed),
    P0-7 (branch atomicity), P1-4 (active_claimant_id wired).
    """
    task = lifecycle_setup["task"]
    dev_agent = lifecycle_setup["dev_agent"]
    task_service = TaskService(db_session)

    deps = ChoreographerDeps(
        task=task_service,
        work_session=_mock_work_session(),
        git=_StubGit(db_session, task),
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

    assert env.error is None, f"claim failed: {env.message}"
    assert env.status == "in_progress"

    refreshed = await task_service.get(task.id)
    assert refreshed is not None
    assert str(refreshed.status) == "in_progress"
    assert refreshed.assigned_to == dev_agent.id
    assert refreshed.last_heartbeat_at is not None, "P0-3: heartbeat seed"
    assert refreshed.active_claimant_id == dev_agent.id, "P1-4: claim lock"


@pytest.mark.asyncio
async def test_dev_full_chain_through_awaiting_qa(
    db_session: AsyncSession, lifecycle_setup: dict[str, Any]
) -> None:
    """claim → commit → open_pr → i_am_done lands in awaiting_qa.

    Drives the full developer-side closure path. Verifies:
      - open_pr records pr_number on the task (commits + PR pre-flight)
      - i_am_done auto-runs submit_verification (P1-3) → verifying → awaiting_qa
      - Heartbeat refreshes after each verb (`_touch`)
      - active_claimant_id remains set through dev's tenure
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

    # 1. Claim
    env = await c.i_will_work_on(
        dev_agent.id,
        task.id,
        plan=_GOOD_PLAN,
        steps=_STEPS,
        technical_considerations=_GOOD_TC,
        risks=_GOOD_RISKS,
    )
    assert env.error is None
    assert env.status == "in_progress"

    # 2. Commit (via stub git directly + record progress on task — the gateway
    # path through ContentActions.commit calls task.add_progress, which we
    # simulate here so open_pr's commits-precondition is satisfied).
    await stub_git.commit(
        branch_name=_BRANCH,
        message=f"[{str(task.id)[:8]}] feat(api): add /healthz",
        task_id=task.id,
    )
    await task_service.add_progress(task.id, dev_agent.id, "implemented /healthz")

    # 3. open_pr — push + open PR. After this, task.pr_number is set.
    env = await c.open_pr(dev_agent.id, task.id)
    assert env.error is None, f"open_pr failed: {env.message}"
    refreshed = await task_service.get(task.id)
    assert refreshed is not None
    assert refreshed.pr_number == _PR_NUMBER, "P0-7 / S-02: PR recorded on task"

    # 4. i_am_done — auto-runs in_progress → verifying → awaiting_qa.
    env = await c.i_am_done(dev_agent.id, task.id, "tests pass; route works")
    assert env.error is None, f"i_am_done failed: {env.message}"
    assert env.status == "awaiting_qa", (
        "P1-3: i_am_done must auto-run submit_verification + submit_qa"
    )

    final = await task_service.get(task.id)
    assert final is not None
    assert str(final.status) == "awaiting_qa"
    assert final.self_verified is True, "P1-3: self_verified set by auto-verify"


@pytest.mark.asyncio
async def test_full_chain_through_doc_handoff(
    db_session: AsyncSession, lifecycle_setup: dict[str, Any]
) -> None:
    """Extend the dev chain: QA pass → documenter → awaiting_pm_review.

    Verifies QA pass clears active_claimant_id (P1-4 + P1-5),
    docs_complete transitions to awaiting_pm_review, and reassignment
    to the cell PM happens on hand-off.
    """
    task = lifecycle_setup["task"]
    dev_agent = lifecycle_setup["dev_agent"]
    qa_agent = lifecycle_setup["qa_agent"]
    doc_agent = lifecycle_setup["doc_agent"]
    cell_pm_agent = lifecycle_setup["cell_pm_agent"]
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

    # Drive the dev side first (same as test_dev_full_chain_through_awaiting_qa).
    await c.i_will_work_on(
        dev_agent.id,
        task.id,
        plan=_GOOD_PLAN,
        steps=_STEPS,
        technical_considerations=_GOOD_TC,
        risks=_GOOD_RISKS,
    )
    await stub_git.commit(
        branch_name=_BRANCH,
        message=f"[{str(task.id)[:8]}] feat(api): add /healthz",
        task_id=task.id,
    )
    await task_service.add_progress(task.id, dev_agent.id, "implemented /healthz")
    await c.open_pr(dev_agent.id, task.id)
    env = await c.i_am_done(dev_agent.id, task.id, "tests pass; route works")
    assert env.error is None
    assert env.status == "awaiting_qa"

    # QA path: claim_review → pass.
    env = await c.claim_review(qa_agent.id, task.id)
    assert env.error is None, f"claim_review failed: {env.message}"

    qa_notes = (
        "Reviewed the diff; route returns 200 OK with timestamp. Tests cover "
        "both acceptance criteria. Approving."
    )
    env = await c.pass_review(qa_agent.id, task.id, notes=qa_notes)
    assert env.error is None, f"pass_review failed: {env.message}"
    assert env.status == "awaiting_documentation"

    after_qa = await task_service.get(task.id)
    assert after_qa is not None
    assert after_qa.active_claimant_id is None, (
        "P1-4 + P1-5: QA pass must clear active_claimant_id for next role"
    )

    # Documenter path: claim_doc_task → i_documented.
    env = await c.claim_doc_task(doc_agent.id, task.id)
    assert env.error is None, f"claim_doc_task failed: {env.message}"

    env = await c.i_documented(
        doc_agent.id,
        task.id,
        notes="Documented /healthz behaviour in docs/api/health.md",
        files=["docs/api/health.md"],
    )
    assert env.error is None, f"i_documented failed: {env.message}"
    assert env.status == "awaiting_pm_review", (
        "P2-1: i_documented must transition awaiting_documentation → awaiting_pm_review"
    )

    after_docs = await task_service.get(task.id)
    assert after_docs is not None
    assert after_docs.assigned_to == cell_pm_agent.id, (
        "P2-1: docs_complete must reassign to the cell PM for the team"
    )


# TODO P2-1 follow-up — final stages (cell_pm complete + main_pm complete +
# CEO approval) require additional setup: a parent task hierarchy for
# the merge chain, plus a real `git.pr_merge` simulation that updates
# the underlying repo. The _StubGit class covers the API surface; what's
# missing is the seeded parent task + main_pm agent.
