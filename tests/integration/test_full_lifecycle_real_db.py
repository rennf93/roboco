"""Real-DB end-to-end test driving the gateway through the full lifecycle.

The end-to-end integration test that would have caught every smoking
gun in the 2026-05-04 audit. Drives a single task
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
  - i_am_done auto-runs submit_verification
  - QA pass clears active_claimant_id
  - branch creation atomicity rollback
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable, ProjectTable, TaskTable, WorkSessionTable
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    TaskNature,
    TaskStatus,
    TaskType,
    Team,
)
from roboco.models.work_session import WorkSessionStatus
from roboco.seeds.initial_data import AGENT_UUIDS
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps
from roboco.services.gateway.choreographer import findings as findings_lib
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
        # Mirrors git._record_pr_atomically — production sets pr_created
        # via mark_pr_created which is what the parallel-completion gate
        # in _maybe_advance_to_pm_review reads.
        self._task.pr_created = True
        await self._session.flush()
        return {"pr_number": _PR_NUMBER, "pr_url": _PR_URL, "is_root_pr": is_root_pr}

    async def diff(
        self,
        *,
        branch_name: str,
        base: Any = None,
        actor_agent_id: Any = None,
        preferred_parent: Any = None,
    ) -> str:
        del branch_name, base, actor_agent_id, preferred_parent
        return "stub diff"

    async def list_changed_files(
        self,
        *,
        branch_name: str,
        base: Any = None,
        actor_agent_id: Any = None,
        preferred_parent: Any = None,
    ) -> list[str]:
        del branch_name, base, actor_agent_id, preferred_parent
        return []

    async def diff_and_files(
        self,
        *,
        branch_name: str,
        base: Any = None,
        actor_agent_id: Any = None,
        preferred_parent: Any = None,
    ) -> tuple[str, list[str]]:
        return (
            await self.diff(
                branch_name=branch_name,
                base=base,
                actor_agent_id=actor_agent_id,
                preferred_parent=preferred_parent,
            ),
            await self.list_changed_files(
                branch_name=branch_name,
                base=base,
                actor_agent_id=actor_agent_id,
                preferred_parent=preferred_parent,
            ),
        )

    async def pr_target(self, pr_number: int, *, actor_agent_id: Any = None) -> str:
        del pr_number, actor_agent_id
        return "main"

    async def pr_merge(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        return {"merged": True, "sha": uuid4().hex[:40]}

    async def is_pr_merged_for_task(self, task_id: UUID) -> bool:
        del task_id
        return False


class _MultiTaskStubGit:
    """Deterministic GitService stub spanning MULTIPLE tasks (cell + root).

    ``_StubGit`` above binds to exactly one ``TaskTable`` row, which is fine
    for the file's single-task dev/QA/doc chain. Driving the cell->root and
    root->master PR/merge stages needs two independently-tracked PRs (one
    per assembled task), so this variant resolves the task to mutate from
    the ``branch_name`` / ``task_id`` every call already carries, keyed off
    a ``{branch_name: task}`` map built by the caller. Mirrors ``_StubGit``'s
    per-call mutation contract otherwise; also stubs the assembly-integrity
    (``unmerged_child_commits``) and behind-base freshen (``is_behind_base``)
    checks ``submit_up`` / ``submit_root`` run before opening a PR.
    """

    def __init__(self, session: Any, tasks_by_branch: dict[str, TaskTable]) -> None:
        self._session = session
        self._by_branch = tasks_by_branch
        self._next_pr_number = 100

    async def commit(
        self,
        *,
        branch_name: str,
        message: str,
        task_id: UUID,
        files: list[str] | None = None,
        actor_agent_id: Any = None,
    ) -> dict[str, Any]:
        del files, actor_agent_id
        task = self._by_branch[branch_name]
        sha = uuid4().hex[:40]
        commits = list(task.commits or [])
        commits.append({"sha": sha, "message": message, "task_id": str(task_id)})
        task.commits = commits
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
        del parent, actor_agent_id
        task = self._by_branch[branch_name]
        pr_number = self._next_pr_number
        self._next_pr_number += 1
        pr_url = f"https://github.com/example/life/pull/{pr_number}"
        task.pr_number = pr_number
        task.pr_url = pr_url
        task.pr_created = True
        await self._session.flush()
        return {"pr_number": pr_number, "pr_url": pr_url, "is_root_pr": is_root_pr}

    async def diff(
        self,
        *,
        branch_name: str,
        base: Any = None,
        actor_agent_id: Any = None,
        preferred_parent: Any = None,
    ) -> str:
        del branch_name, base, actor_agent_id, preferred_parent
        return "stub diff"

    async def list_changed_files(
        self,
        *,
        branch_name: str,
        base: Any = None,
        actor_agent_id: Any = None,
        preferred_parent: Any = None,
    ) -> list[str]:
        del branch_name, base, actor_agent_id, preferred_parent
        return []

    async def diff_and_files(
        self,
        *,
        branch_name: str,
        base: Any = None,
        actor_agent_id: Any = None,
        preferred_parent: Any = None,
    ) -> tuple[str, list[str]]:
        return (
            await self.diff(
                branch_name=branch_name,
                base=base,
                actor_agent_id=actor_agent_id,
                preferred_parent=preferred_parent,
            ),
            await self.list_changed_files(
                branch_name=branch_name,
                base=base,
                actor_agent_id=actor_agent_id,
                preferred_parent=preferred_parent,
            ),
        )

    async def pr_target(self, pr_number: int, *, actor_agent_id: Any = None) -> str:
        del pr_number, actor_agent_id
        return "main"

    async def pr_merge(
        self,
        pr_number: int,
        *,
        target: Any = None,
        project_id: Any = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del target, project_id, kwargs
        # Mirrors the real `GitService.pr_merge`: when the merged task carries
        # a work session (a PM's claim creates one — see `TaskService.
        # _finalize_claim`), mark it merged too, so a later
        # `_assert_pr_merged_for_complete` / `ceo_approve` check against that
        # SAME session sees consistent state instead of a stale "open" row a
        # bare pr_number/task mutation would leave behind.
        task = next(
            (t for t in self._by_branch.values() if t.pr_number == pr_number), None
        )
        if task is not None and task.work_session_id:
            ws = await self._session.get(WorkSessionTable, task.work_session_id)
            if ws is not None:
                ws.pr_status = "merged"
                ws.status = WorkSessionStatus.COMPLETED
                await self._session.flush()
        return {
            "merged": True,
            "sha": uuid4().hex[:40],
            "merge_commit_sha": uuid4().hex[:40],
        }

    async def is_pr_merged_for_task(self, task_id: UUID) -> bool:
        del task_id
        return False

    async def is_behind_base(
        self, task: Any, *, base_branch: str, actor_agent_id: Any = None
    ) -> tuple[int, int]:
        del task, base_branch, actor_agent_id
        return (0, 1)

    async def unmerged_child_commits(
        self, task: Any, *, actor_agent_id: Any = None
    ) -> list[dict[str, Any]]:
        del task, actor_agent_id
        return []

    async def get_pr_head_sha(self, project_slug: str, pr_number: int) -> str | None:
        del project_slug, pr_number
        return None

    async def post_pr_review(
        self, project_slug: str, pr_number: int, body: str, *, event: str
    ) -> None:
        del project_slug, pr_number, body, event


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

    Verifies in one shot: None-handling, heartbeat seed, branch
    atomicity, and active_claimant_id wired.
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
        agent_id=dev_agent.id,
        task_id=task.id,
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
    assert refreshed.last_heartbeat_at is not None, "heartbeat seed"
    assert refreshed.active_claimant_id == dev_agent.id, "claim lock"


@pytest.mark.asyncio
async def test_dev_full_chain_through_awaiting_qa(
    db_session: AsyncSession, lifecycle_setup: dict[str, Any]
) -> None:
    """claim → commit → open_pr → i_am_done lands in awaiting_qa.

    Drives the full developer-side closure path. Verifies:
      - open_pr records pr_number on the task (commits + PR pre-flight)
      - i_am_done auto-runs submit_verification → verifying → awaiting_qa
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
        agent_id=dev_agent.id,
        task_id=task.id,
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
    assert refreshed.pr_number == _PR_NUMBER, "PR recorded on task"

    # i_am_done obligates the developer's dev_notes section — the agent fills
    # it via note(scope='handoff') first; record_section_note is that write.
    await task_service.record_section_note(
        task.id,
        "developer",
        {"summary": "Implemented /healthz and added a test for the happy path."},
    )

    # 4. i_am_done — auto-runs in_progress → verifying → awaiting_qa.
    env = await c.i_am_done(dev_agent.id, task.id, "tests pass; route works")
    assert env.error is None, f"i_am_done failed: {env.message}"
    assert env.status == "awaiting_qa", (
        "i_am_done must auto-run submit_verification + submit_qa"
    )

    final = await task_service.get(task.id)
    assert final is not None
    assert str(final.status) == "awaiting_qa"
    assert final.self_verified is True, "self_verified set by auto-verify"


@pytest.mark.asyncio
async def test_full_chain_through_doc_handoff(
    db_session: AsyncSession, lifecycle_setup: dict[str, Any]
) -> None:
    """Extend the dev chain: QA pass → documenter → awaiting_pm_review.

    Verifies QA pass clears active_claimant_id,
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
        agent_id=dev_agent.id,
        task_id=task.id,
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
    # i_am_done obligates the developer's dev_notes section (note(scope='handoff')).
    await task_service.record_section_note(
        task.id,
        "developer",
        {"summary": "Implemented /healthz and added a test for the happy path."},
    )
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
    env = await c.pass_review(
        qa_agent.id,
        task.id,
        notes=qa_notes,
        ac_verdicts=[f"verified: {crit}" for crit in task.acceptance_criteria],
        criteria_verified=[
            {"criterion": crit, "evidence": f"verified against the PR diff: {crit}"}
            for crit in task.acceptance_criteria
        ],
    )
    assert env.error is None, f"pass_review failed: {env.message}"
    assert env.status == "awaiting_documentation"

    after_qa = await task_service.get(task.id)
    assert after_qa is not None
    assert after_qa.active_claimant_id is None, (
        "QA pass must clear active_claimant_id for next role"
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
        "i_documented must transition awaiting_documentation → awaiting_pm_review"
    )

    after_docs = await task_service.get(task.id)
    assert after_docs is not None
    assert after_docs.assigned_to == cell_pm_agent.id, (
        "docs_complete must reassign to the cell PM for the team"
    )


def _uid(value: Any) -> UUID:
    """Cast an ORM row's id column to a real ``UUID`` for static typing.

    Mirrors this file's sibling (``test_lifecycle_real_db.py``)'s own
    ``UUID(str(x.id))`` cast at reviewer/task id call sites — a concretely
    typed ``TaskTable``/``AgentTable`` row's ``.id`` resolves to the mapped
    column type rather than ``uuid.UUID`` under mypy.
    """
    return UUID(str(value))


async def _seed_pr_reviewer(db_session: AsyncSession) -> AgentTable:
    """Add + flush a backend in-path PR-review-gate reviewer.

    Flushed here (mirrors ``test_lifecycle_real_db.py``'s identical helper)
    so a later ``task.assigned_to = reviewer.id`` update can't race the
    reviewer INSERT in the same unit-of-work and trip the FK constraint.
    """
    reviewer = AgentTable(
        id=uuid4(),
        name="BE PR Reviewer",
        slug=f"be-pr-reviewer-{uuid4().hex[:8]}",
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


async def _seed_main_pm(db_session: AsyncSession) -> UUID:
    """Return the fixed ``main-pm`` agent id, seeding it if not already present.

    Keyed on the fixed foundation UUID (mirrors ``test_lifecycle_real_db.py``'s
    identical pattern): the cross-test shared DB already has other tests
    seeding this exact slug, and an unconditional insert with a fresh random
    id would collide on the slug's unique index.
    """
    main_pm_id = UUID(AGENT_UUIDS["main-pm"])
    if await db_session.get(AgentTable, main_pm_id) is None:
        db_session.add(
            AgentTable(
                id=main_pm_id,
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
        )
        await db_session.flush()
    return main_pm_id


async def _seed_root_task(
    db_session: AsyncSession,
    *,
    project_id: UUID,
    creator_id: UUID,
    main_pm_id: UUID,
) -> TaskTable:
    """Seed a Main-PM coordination root: no parent, its own branch/PR."""
    root = TaskTable(
        id=uuid4(),
        title="Ship the backend healthz cell",
        description="Coordination root assembling the backend cell's work",
        status=TaskStatus.IN_PROGRESS,
        priority=1,
        task_type=TaskType.PLANNING,
        nature=TaskNature.TECHNICAL,
        team=Team.MAIN_PM,
        project_id=project_id,
        created_by=creator_id,
        assigned_to=main_pm_id,
        parent_task_id=None,
        branch_name=f"feature/main_pm/root-{uuid4().hex[:8]}",
        acceptance_criteria=["Ship the /healthz endpoint end to end"],
    )
    db_session.add(root)
    await db_session.flush()
    return root


@dataclass
class _RootCellHarness:
    """A seeded root + cell hierarchy wired to a real Choreographer/TaskService.

    Shared setup for the happy-path and reject-path final-stage tests below —
    factored out to keep each test's own statement count within the xenon/
    ruff complexity budget.
    """

    cell: TaskTable
    root: TaskTable
    reviewer: AgentTable
    main_pm_id: UUID
    c: Choreographer
    task_service: TaskService
    git: Any

    async def submit_up_and_pass(
        self,
        pm_agent_id: UUID,
        task_id: UUID,
        *,
        submit_notes: str,
        pass_notes: str,
        resolved_findings: list[dict[str, Any]] | None = None,
    ) -> None:
        """Drive submit_up -> claim_gate_review -> pr_pass to awaiting_pm_review."""
        env = await self.c.submit_up(
            pm_agent_id,
            task_id,
            notes=submit_notes,
            resolved_findings=resolved_findings,
        )
        assert env.error is None, f"submit_up failed: {env.message}"
        assert env.status == "awaiting_pr_review"

        env = await self.c.claim_gate_review(_uid(self.reviewer.id), task_id)
        assert env.error is None, f"claim_gate_review failed: {env.message}"

        env = await self.c.pr_pass(_uid(self.reviewer.id), task_id, notes=pass_notes)
        assert env.error is None, f"pr_pass failed: {env.message}"
        assert env.status == "awaiting_pm_review"

    async def submit_up_and_pass_root(self) -> None:
        """Drive the root's own submit_root -> claim_gate_review -> pr_pass."""
        root_id = _uid(self.root.id)
        env = await self.c.submit_root(
            self.main_pm_id,
            root_id,
            notes="Root scope assembled: the backend cell's /healthz slice is"
            " merged; opening the root->master PR for review.",
        )
        assert env.error is None, f"submit_root failed: {env.message}"
        assert env.status == "awaiting_pr_review"

        root_after_submit = await self.task_service.get(root_id)
        assert root_after_submit is not None
        assert root_after_submit.pr_number is not None, "root->master PR recorded"

        env = await self.c.claim_gate_review(_uid(self.reviewer.id), root_id)
        assert env.error is None, f"claim_gate_review (root) failed: {env.message}"

        env = await self.c.pr_pass(
            _uid(self.reviewer.id),
            root_id,
            notes="Reviewed the root->master diff: the backend cell's work is"
            " complete, correct, and ready to ship. Approving.",
        )
        assert env.error is None, f"pr_pass (root) failed: {env.message}"
        assert env.status == "awaiting_pm_review"

    async def cell_merge_complete(self, notes: str) -> AgentTable:
        """Resolve the owning cell PM and merge-complete the cell task."""
        resolved_cell_pm = await self.task_service.cell_pm_for_team(Team.BACKEND)
        assert resolved_cell_pm is not None
        env = await self.c.complete(
            _uid(resolved_cell_pm.id), _uid(self.cell.id), notes=notes
        )
        assert env.error is None, f"complete (cell) failed: {env.message}"
        assert env.status == "completed"
        return resolved_cell_pm


async def _seed_root_cell_harness(
    db_session: AsyncSession, lifecycle_setup: dict[str, Any]
) -> _RootCellHarness:
    """Seed main_pm + reviewer + root, parent the fixture's cell task under
    it, and wire a real Choreographer over ``_MultiTaskStubGit``."""
    cell = lifecycle_setup["task"]
    cell_pm_agent = lifecycle_setup["cell_pm_agent"]
    project = lifecycle_setup["project"]

    main_pm_id = await _seed_main_pm(db_session)
    reviewer = await _seed_pr_reviewer(db_session)
    root = await _seed_root_task(
        db_session,
        project_id=project.id,
        creator_id=project.created_by,
        main_pm_id=main_pm_id,
    )

    # The cell task represents the backend cell's assembled work: parented
    # under the root, ready for the cell PM to bubble it up.
    cell.parent_task_id = root.id
    cell.status = TaskStatus.IN_PROGRESS
    cell.assigned_to = cell_pm_agent.id
    cell.commits = [
        {"sha": uuid4().hex[:40], "message": "feat: /healthz", "task_id": str(cell.id)}
    ]
    await db_session.flush()

    # A real, still-open WorkSessionTable row for the cell — without this,
    # complete()'s _assert_pr_merged_for_complete guard (task.py:7378) sees
    # a null work_session_id and early-returns True, never actually
    # exercising the merged-PR check the cell_pm complete -> pr_merge path
    # is meant to prove.
    cell_work_session = WorkSessionTable(
        id=uuid4(),
        project_id=project.id,
        task_id=cell.id,
        agent_id=cell_pm_agent.id,
        branch_name=str(cell.branch_name),
        base_branch=str(root.branch_name),
        target_branch=str(root.branch_name),
        status=WorkSessionStatus.ACTIVE,
        pr_status="open",
    )
    db_session.add(cell_work_session)
    await db_session.flush()
    cell.work_session_id = cell_work_session.id
    await db_session.flush()

    task_service = TaskService(db_session)
    git = _MultiTaskStubGit(
        db_session, {str(cell.branch_name): cell, str(root.branch_name): root}
    )
    deps = ChoreographerDeps(
        task=task_service,
        work_session=_mock_work_session(),
        git=git,
        a2a=AsyncMock(),
        journal=_mock_journal_with_reflect(),
        audit=AsyncMock(),
        evidence_repo=_mock_evidence_repo(),
    )
    return _RootCellHarness(
        cell=cell,
        root=root,
        reviewer=reviewer,
        main_pm_id=main_pm_id,
        c=Choreographer(deps),
        task_service=task_service,
        git=git,
    )


@pytest.mark.asyncio
async def test_cell_and_root_reach_completed_via_gate_and_ceo_approval(
    db_session: AsyncSession, lifecycle_setup: dict[str, Any]
) -> None:
    """Extends the dev/QA/doc chain through the stages the file's own TODO
    named as missing: cell_pm submit_up -> in-path PR-review gate -> cell_pm
    merge-complete -> main_pm submit_root -> gate -> main_pm complete ->
    CEO approval, reaching ``completed`` with the merged/PR state asserted.

    Seeds a real root (main_pm) + cell (cell_pm) task hierarchy and drives
    every stage through the real Choreographer + TaskService + DB. Git stays
    a deterministic stub (``_MultiTaskStubGit``, this file's established
    pattern generalized to two tasks). Both the cell and the root carry a
    REAL ``WorkSessionTable`` row seeded ``pr_status="open"`` — the cell's
    flips to ``"merged"`` via the real ``pr_merge`` pre-side-effect
    ``complete()`` runs, and the root's is proven non-circular: ``ceo_
    approve`` is asserted to refuse while still open, then the flip is
    driven through ``_MultiTaskStubGit.pr_merge`` (not a literal field
    write) before a second ``ceo_approve`` call succeeds — so the real
    merged-PR guards in both ``TaskService.complete`` and ``ceo_approve``
    actually execute, not skipped by an early-return on a null
    ``work_session_id``.
    """
    h = await _seed_root_cell_harness(db_session, lifecycle_setup)
    project = lifecycle_setup["project"]
    cell_pm_agent = lifecycle_setup["cell_pm_agent"]
    cell_id = _uid(h.cell.id)
    root_id = _uid(h.root.id)

    # 1-3. Cell PM bubbles the assembled cell scope up, the reviewer passes
    #      the cell->root PR, and the cell PM merges + completes the cell.
    await h.submit_up_and_pass(
        cell_pm_agent.id,
        cell_id,
        submit_notes="Cell scope assembled: /healthz implemented and tested;"
        " opening the cell->root PR for review.",
        pass_notes="Reviewed the cell->root diff: the /healthz endpoint is"
        " correctly implemented and covered by tests. Approving.",
    )
    cell_after_pass = await h.task_service.get(cell_id)
    assert cell_after_pass is not None
    assert cell_after_pass.pr_number is not None, "cell->root PR recorded"

    await h.cell_merge_complete(
        "Cell scope reviewed and approved; merging the cell->root PR."
    )
    cell_final = await h.task_service.get(cell_id)
    assert cell_final is not None
    assert str(cell_final.status) == "completed"
    assert any(entry.get("kind") == "merge" for entry in (cell_final.commits or [])), (
        "the merge commit must be recorded on the completed cell task"
    )
    # The cell's real WorkSessionTable row must now read "merged" — proving
    # complete()'s pr_merge pre-side-effect + _assert_pr_merged_for_complete
    # guard actually ran against a live session, not an early-return on a
    # null work_session_id.
    cell_work_session = await db_session.get(
        WorkSessionTable, cell_final.work_session_id
    )
    assert cell_work_session is not None
    assert cell_work_session.pr_status == "merged"
    # The root's only subtask (the cell) is now terminal.
    assert await h.task_service.all_subtasks_terminal(root_id)

    # 4-5. Main PM submits the root, the reviewer passes the root->master PR.
    await h.submit_up_and_pass_root()

    # 6. Main PM completes the root — escalates to the CEO for final sign-off.
    env = await h.c.main_pm_complete(
        h.main_pm_id,
        root_id,
        notes="Root scope reviewed and approved; escalating to the CEO for"
        " final sign-off before merging to master.",
    )
    assert env.error is None, f"main_pm_complete failed: {env.message}"
    assert env.status == "awaiting_ceo_approval"

    root_awaiting_ceo = await h.task_service.get(root_id)
    assert root_awaiting_ceo is not None
    assert str(root_awaiting_ceo.status) == "awaiting_ceo_approval"
    assert root_awaiting_ceo.assigned_to is None, (
        "main_pm_complete clears assigned_to so no agent is respawned while"
        " the CEO decides"
    )

    # 7. The CEO merges the root->master PR. Seed a real, still-OPEN
    #    WorkSessionTable row first and prove ceo_approve's merged-PR guard
    #    actively refuses while it is open (a non-circular check — the
    #    assertion would fail if the guard were deleted), then drive the
    #    flip to "merged" through _MultiTaskStubGit.pr_merge — the same
    #    stub method the real cell_pm complete path runs through — rather
    #    than a literal field write, before asserting the approval reaches
    #    completed.
    work_session = WorkSessionTable(
        id=uuid4(),
        project_id=project.id,
        task_id=root_id,
        agent_id=h.main_pm_id,
        branch_name=root_awaiting_ceo.branch_name,
        base_branch="master",
        target_branch="master",
        status=WorkSessionStatus.ACTIVE,
        pr_number=root_awaiting_ceo.pr_number,
        pr_url=root_awaiting_ceo.pr_url,
        pr_status="open",
    )
    db_session.add(work_session)
    await db_session.flush()
    root_awaiting_ceo.work_session_id = work_session.id
    await db_session.flush()

    refused = await h.task_service.ceo_approve(
        root_id, "Attempting approval before the PR is actually merged."
    )
    assert refused is None, (
        "ceo_approve must refuse while the work session's PR is still open"
    )

    await h.git.pr_merge(
        root_awaiting_ceo.pr_number, target="master", project_id=project.id
    )
    await db_session.refresh(work_session)
    assert work_session.pr_status == "merged", (
        "_MultiTaskStubGit.pr_merge must flip the real work session to merged"
    )

    approved = await h.task_service.ceo_approve(
        root_id,
        "Approved: the backend cell's /healthz slice is complete, reviewed,"
        " and merged to master. Shipping to production.",
    )
    assert approved is not None, "ceo_approve refused after the PR was actually merged"
    assert str(approved.status) == "completed"

    final_root = await h.task_service.get(root_id)
    assert final_root is not None
    assert str(final_root.status) == "completed"


@pytest.mark.asyncio
async def test_pr_fail_on_submit_up_then_resubmit_reaches_completed(
    db_session: AsyncSession, lifecycle_setup: dict[str, Any]
) -> None:
    """Reject path: submit_up -> pr_fail -> needs_revision -> cell PM
    re-claims (``i_will_plan``) -> resubmits -> pr_pass -> complete, reaching
    the terminal ``completed`` outcome.

    Covers the in-path PR-review gate's reject leg end-to-end against the
    real DB — the exact stage the three rounds of pr_gate hardening (the
    PR-waiver latch, the next-hint fix, and the revision-findings ledger)
    touched with no real-DB coverage before this.
    """
    h = await _seed_root_cell_harness(db_session, lifecycle_setup)
    cell_pm_agent = lifecycle_setup["cell_pm_agent"]
    cell_id = _uid(h.cell.id)

    # 1. Cell PM submits; the reviewer requests changes.
    env = await h.c.submit_up(
        cell_pm_agent.id,
        cell_id,
        notes="Cell scope assembled: /healthz implemented; opening the"
        " cell->root PR for review.",
    )
    assert env.error is None, f"submit_up failed: {env.message}"
    assert env.status == "awaiting_pr_review"

    env = await h.c.claim_gate_review(_uid(h.reviewer.id), cell_id)
    assert env.error is None, f"claim_gate_review failed: {env.message}"

    env = await h.c.pr_fail(
        _uid(h.reviewer.id),
        cell_id,
        issues=[
            "The /healthz handler does not return a timestamp field as the"
            " acceptance criteria require."
        ],
    )
    assert env.error is None, f"pr_fail failed: {env.message}"
    assert env.status == "needs_revision"

    revision_owner = await h.task_service.cell_pm_for_team(Team.BACKEND)
    assert revision_owner is not None
    revision_owner_id = _uid(revision_owner.id)
    failed = await h.task_service.get(cell_id)
    assert failed is not None
    assert failed.assigned_to == revision_owner.id, (
        "pr_fail hands the assembled task back to its owning cell PM"
    )
    assert "timestamp field" in (failed.pr_reviewer_notes or "")
    open_findings = await findings_lib.open_findings_for_task(db_session, cell_id)
    assert len(open_findings) == 1, "pr_fail must ledger exactly the one finding raised"
    finding_id = str(open_findings[0].id)[:8]

    # 2. The cell PM re-claims the rejected task and re-delegates the fix
    #    (simulated here by simply resubmitting — the DB-level contract under
    #    test is the needs_revision -> in_progress re-claim + resubmit path,
    #    not the fix content itself).
    env = await h.c.i_will_plan(
        revision_owner_id,
        cell_id,
        plan="Re-claim the rejected cell scope and resubmit after fixing the"
        " missing timestamp field.",
        rich_plan={
            "approach": (
                "Address the reviewer's finding: the /healthz handler must"
                " return a timestamp field. Re-verify both acceptance"
                " criteria locally, then resubmit the cell->root PR for"
                " another review pass."
            ),
            "sub_tasks": [
                {
                    "title": "Fix missing timestamp field",
                    "description": (
                        "Update the /healthz handler to include a timestamp"
                        " field in its response body, matching the second"
                        " acceptance criterion the reviewer flagged."
                    ),
                },
            ],
        },
    )
    assert env.error is None, f"i_will_plan (re-claim) failed: {env.message}"
    assert env.status == "in_progress"

    reclaimed = await h.task_service.get(cell_id)
    assert reclaimed is not None
    assert str(reclaimed.status) == "in_progress"
    assert reclaimed.assigned_to == revision_owner.id

    # 3. Resubmit, naming the resolved finding (FINDINGS_ADDRESSED requires
    #    every open ledger finding to be named before a re-submit is allowed);
    #    this time the reviewer passes it.
    await h.submit_up_and_pass(
        revision_owner_id,
        cell_id,
        submit_notes="Fixed the missing timestamp field per the reviewer's"
        " finding; resubmitting the cell->root PR.",
        pass_notes="Reviewed the updated diff: the timestamp field is now"
        " present and both acceptance criteria are covered. Approving.",
        resolved_findings=[
            {
                "finding_id": finding_id,
                "note": "Added the timestamp field to the /healthz response.",
            }
        ],
    )

    # 4. Cell PM merges and completes — the terminal outcome for this leg.
    await h.cell_merge_complete(
        "Cell scope reviewed and approved after revision; merging the cell->root PR."
    )
    final_cell = await h.task_service.get(cell_id)
    assert final_cell is not None
    assert str(final_cell.status) == "completed"
