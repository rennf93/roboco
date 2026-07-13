"""Tier 3 real-DB tests — the revision-findings ledger + i_am_done gate.

Mirrors ``test_lifecycle_real_db.py``: only git is stubbed; the spec,
choreographer, VerbRunner, TaskService, and the ledger repository are all
real. Covers the four producers (fail_review / pr_fail / request_changes /
ceo_reject) persisting structured findings, the dev_notes data-loss-bug fix,
and the i_am_done FINDINGS_ADDRESSED resolution gate end to end.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable, AuditLogTable, ProjectTable, TaskTable
from roboco.foundation.policy.content import Finding, Severity
from roboco.foundation.policy.lifecycle import Status
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    TaskNature,
    TaskStatus,
    TaskType,
    Team,
)
from roboco.seeds.initial_data import AGENT_UUIDS
from roboco.services.base import ValidationError
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps
from roboco.services.repositories.review_findings import (
    STATUS_ADDRESSED,
    STATUS_OPEN,
    STATUS_VERIFIED,
    ReviewFindingsRepository,
)
from roboco.services.task import TaskService
from sqlalchemy import select

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

_BRANCH = "feature/backend/healthz"
_PR_NUMBER = 8
_PR_URL = "https://github.com/example/findings/pull/8"
_EXPECTED_FINDING_COUNT = 2
_EXPECTED_ROUND_COUNT = 2

_GOOD_PLAN = (
    "Implement the task on its feature branch: edit the target module, add or "
    "update unit tests covering the change, run the suite locally, then commit "
    "on the branch and open a PR. Keep the diff focused on the acceptance "
    "criteria and verify it before submitting for QA."
)
_STEPS = [
    {
        "title": "Implement the change",
        "description": (
            "edit the target file, add tests, run them, and stage the change "
            "for commit on the task branch"
        ),
    }
]
_GOOD_TC = ["Follow the existing module's patterns; keep the change minimal."]
_GOOD_RISKS = [
    {
        "risk": "Scope creep balloons the diff and slows review.",
        "mitigation": "Touch only the files the acceptance criteria require.",
    }
]


class _StubGit:
    """Deterministic GitService stub — mirrors test_lifecycle_real_db.py."""

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
        return {"sha": sha, "message": message, "files_changed": 1}

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
        self, *, branch_name: str, base: Any = None, actor_agent_id: Any = None
    ) -> list[str]:
        del branch_name, base, actor_agent_id
        return ["roboco/api/routes/health.py"]

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

    async def is_pr_merged_for_task(self, task_id: UUID) -> bool:
        del task_id
        return False

    async def get_pr_head_sha(self, slug: str, pr_number: int) -> str:
        del slug, pr_number
        return uuid4().hex[:40]

    async def post_pr_review(
        self, slug: str, pr_number: int, body: str, *, event: str
    ) -> None:
        del slug, pr_number, body, event


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
    journal = AsyncMock()
    journal.has_reflect_for_task.return_value = True
    journal.has_learning_for_task.return_value = True
    journal.has_decision_for_task.return_value = True
    journal.has_struggle_for_task.return_value = False
    journal.has_note_for_task.return_value = True
    journal.latest_decision_at.return_value = datetime.now(UTC)
    return journal


def _mock_work_session() -> Any:
    ws = AsyncMock()
    ws.files_changed.return_value = ["roboco/api/routes/health.py"]
    ws.has_unpushed_commits.return_value = False
    return ws


async def _seed_agents_and_project(db_session: AsyncSession) -> dict[str, Any]:
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
        name="Findings Ledger Test Project",
        slug=f"findings-{uuid4().hex[:8]}",
        git_url="https://github.com/example/findings.git",
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
    reviewer_agent = AgentTable(
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
    main_pm_agent = AgentTable(
        id=uuid4(),
        name="Main PM",
        slug=f"main-pm-{uuid4().hex[:8]}",
        role=AgentRole.MAIN_PM,
        team=Team.MAIN_PM,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="main_pm",
        capabilities=["coord"],
        permissions={},
        metrics={},
    )
    db_session.add_all(
        [dev_agent, qa_agent, cell_pm_agent, reviewer_agent, main_pm_agent]
    )
    await db_session.flush()

    return {
        "system_agent": system_agent,
        "project": project,
        "dev_agent": dev_agent,
        "qa_agent": qa_agent,
        "cell_pm_agent": cell_pm_agent,
        "reviewer_agent": reviewer_agent,
        "main_pm_agent": main_pm_agent,
    }


def _build_task(
    *, project_id: UUID, creator_id: UUID, assignee_id: UUID | None, status: TaskStatus
) -> TaskTable:
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
    )


@pytest_asyncio.fixture
async def setup(db_session: AsyncSession) -> AsyncIterator[dict[str, Any]]:
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


def _choreographer(db_session: AsyncSession, task: TaskTable) -> Choreographer:
    deps = ChoreographerDeps(
        task=TaskService(db_session),
        work_session=_mock_work_session(),
        git=_StubGit(db_session, task),
        a2a=AsyncMock(),
        journal=_mock_journal_with_reflect(),
        audit=AsyncMock(),
        evidence_repo=_mock_evidence_repo(),
    )
    return Choreographer(deps)


# ---------------------------------------------------------------------------
# fail_review persists structured findings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fail_review_persists_findings_round1_and_qa_notes(
    db_session: AsyncSession, setup: dict[str, Any]
) -> None:
    task = setup["task"]
    qa_agent = setup["qa_agent"]
    dev_agent = setup["dev_agent"]

    task.status = TaskStatus.AWAITING_QA
    task.pr_number = _PR_NUMBER
    task.pr_url = _PR_URL
    task.commits = [
        {"sha": uuid4().hex[:40], "message": "feat: x", "task_id": str(task.id)}
    ]
    task.self_verified = True
    task.orchestration_markers = {"original_developer": str(dev_agent.id)}
    await db_session.flush()

    task_service = TaskService(db_session)
    c = _choreographer(db_session, task)
    await c.claim_review(qa_agent.id, task.id)

    env = await c.fail_review(
        qa_agent.id,
        task.id,
        findings=[
            {
                "file": "roboco/api/routes/health.py",
                "line": 12,
                "severity": "major",
                "expected": "returns 200",
                "actual": "returns 500 on the timestamp branch",
            }
        ],
    )
    assert env.error is None, env.as_dict()
    assert env.status == Status.NEEDS_REVISION.value

    findings = await ReviewFindingsRepository(db_session).list_for_task(task.id)
    assert len(findings) == 1
    assert findings[0].origin == "qa"
    assert findings[0].round == 1
    assert findings[0].status == STATUS_OPEN
    # GatewayAgentView (self.task.agent_for) carries no slug field, so
    # author_slug falls back to the role string — mirrors the same
    # established fallback _post_gate_review uses for reviewer_slug.
    assert findings[0].author_slug == "qa"

    final = await task_service.get(task.id)
    assert final is not None
    assert "returns 500 on the timestamp branch" in (final.qa_notes or "")
    assert f"[F-{str(findings[0].id)[:8]}]" in (final.qa_notes or "")


@pytest.mark.asyncio
async def test_fail_review_issues_shim_still_works(
    db_session: AsyncSession, setup: dict[str, Any]
) -> None:
    task = setup["task"]
    qa_agent = setup["qa_agent"]
    dev_agent = setup["dev_agent"]
    task.status = TaskStatus.AWAITING_QA
    task.pr_number = _PR_NUMBER
    task.commits = [
        {"sha": uuid4().hex[:40], "message": "feat: x", "task_id": str(task.id)}
    ]
    task.self_verified = True
    task.orchestration_markers = {"original_developer": str(dev_agent.id)}
    await db_session.flush()

    c = _choreographer(db_session, task)
    await c.claim_review(qa_agent.id, task.id)

    env = await c.fail_review(
        qa_agent.id,
        task.id,
        issues=["Missing test coverage for the timestamp branch of /healthz"],
    )
    assert env.error is None, env.as_dict()

    findings = await ReviewFindingsRepository(db_session).list_for_task(task.id)
    assert len(findings) == 1
    assert findings[0].file is None
    assert findings[0].severity == "major"
    assert (
        findings[0].actual
        == "Missing test coverage for the timestamp branch of /healthz"
    )


@pytest.mark.asyncio
async def test_fail_review_rejects_over_hard_cap(
    db_session: AsyncSession, setup: dict[str, Any]
) -> None:
    task = setup["task"]
    qa_agent = setup["qa_agent"]
    task.status = TaskStatus.AWAITING_QA
    task.pr_number = _PR_NUMBER
    task.commits = [
        {"sha": uuid4().hex[:40], "message": "feat: x", "task_id": str(task.id)}
    ]
    task.self_verified = True
    await db_session.flush()

    c = _choreographer(db_session, task)
    await c.claim_review(qa_agent.id, task.id)

    findings = [
        {
            "expected": f"expected {i}",
            "actual": f"actual finding number {i}",
            "severity": "minor",
        }
        for i in range(11)
    ]
    env = await c.fail_review(qa_agent.id, task.id, findings=findings)
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "11" in body["message"]

    assert await ReviewFindingsRepository(db_session).list_for_task(task.id) == []


@pytest.mark.asyncio
async def test_fail_review_nudge_warns_over_five(
    db_session: AsyncSession, setup: dict[str, Any]
) -> None:
    task = setup["task"]
    qa_agent = setup["qa_agent"]
    dev_agent = setup["dev_agent"]
    task.status = TaskStatus.AWAITING_QA
    task.pr_number = _PR_NUMBER
    task.commits = [
        {"sha": uuid4().hex[:40], "message": "feat: x", "task_id": str(task.id)}
    ]
    task.self_verified = True
    task.orchestration_markers = {"original_developer": str(dev_agent.id)}
    await db_session.flush()

    c = _choreographer(db_session, task)
    await c.claim_review(qa_agent.id, task.id)

    findings = [
        {
            "expected": f"expected {i}",
            "actual": f"actual finding number {i}",
            "severity": "minor",
        }
        for i in range(6)
    ]
    env = await c.fail_review(qa_agent.id, task.id, findings=findings)
    assert env.error is None, env.as_dict()
    assert env.warning is not None
    assert "6" in env.warning


# ---------------------------------------------------------------------------
# pr_fail persists structured findings into pr_reviewer_notes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pr_fail_persists_findings_and_pr_reviewer_notes(
    db_session: AsyncSession, setup: dict[str, Any]
) -> None:
    task = setup["task"]
    reviewer = setup["reviewer_agent"]
    task.status = TaskStatus.AWAITING_PR_REVIEW
    task.assigned_to = reviewer.id
    task.claimed_by = reviewer.id
    task.pr_number = _PR_NUMBER
    await db_session.flush()

    task_service = TaskService(db_session)
    c = _choreographer(db_session, task)

    env = await c.pr_fail(
        UUID(str(reviewer.id)),
        task.id,
        findings=[
            {
                "file": "roboco/api/routes/health.py",
                "severity": "blocker",
                "expected": "task_id is a UUID",
                "actual": "FE sends task_id as a string",
            }
        ],
    )
    assert env.error is None, env.as_dict()
    assert env.status == Status.NEEDS_REVISION.value

    findings = await ReviewFindingsRepository(db_session).list_for_task(task.id)
    assert len(findings) == 1
    assert findings[0].origin == "pr_gate"
    # GatewayAgentView carries no slug field — falls back to the role string.
    assert findings[0].author_slug == "pr_reviewer"

    final = await task_service.get(task.id)
    assert final is not None
    assert "FE sends task_id as a string" in (final.pr_reviewer_notes or "")
    assert (final.notes_structured or {}).get("pr_review", {}).get(
        "verdict"
    ) == "failed"
    assert (
        len((final.notes_structured or {}).get("pr_review", {}).get("findings", []))
        == 1
    )


# ---------------------------------------------------------------------------
# request_changes persists structured findings into the new pm_notes slot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_changes_persists_findings_and_pm_notes(
    db_session: AsyncSession, setup: dict[str, Any]
) -> None:
    task = setup["task"]
    cell_pm_agent = setup["cell_pm_agent"]
    task.status = TaskStatus.AWAITING_PM_REVIEW
    task.assigned_to = cell_pm_agent.id
    task.pr_number = _PR_NUMBER
    await db_session.flush()

    task_service = TaskService(db_session)
    c = _choreographer(db_session, task)

    env = await c.request_changes(
        cell_pm_agent.id,
        task.id,
        findings=[
            {
                "file": "frontend/CLAUDE.md",
                "severity": "major",
                "expected": "scope limited to the endpoint change",
                "actual": "CLAUDE.md modified out of scope",
            }
        ],
    )
    assert env.error is None, env.as_dict()
    assert env.status == Status.NEEDS_REVISION.value

    findings = await ReviewFindingsRepository(db_session).list_for_task(task.id)
    assert len(findings) == 1
    assert findings[0].origin == "pm"

    final = await task_service.get(task.id)
    assert final is not None
    assert "CLAUDE.md modified out of scope" in (final.pm_notes or "")
    assert (final.notes_structured or {}).get("pm_review") is not None


@pytest.mark.asyncio
async def test_request_changes_dev_notes_survives_a_later_handoff_note(
    db_session: AsyncSession, setup: dict[str, Any]
) -> None:
    """Regression: request_changes must NOT raw-append onto dev_notes — the
    data-loss bug the ledger fix retires. A later developer handoff note
    (apply_structured_note's real overwrite semantics) must show only its OWN
    content; the PM's reject lives in pm_notes + the ledger instead."""
    task = setup["task"]
    cell_pm_agent = setup["cell_pm_agent"]
    task.status = TaskStatus.AWAITING_PM_REVIEW
    task.assigned_to = cell_pm_agent.id
    task.pr_number = _PR_NUMBER
    task.dev_notes = "original developer summary"
    await db_session.flush()

    task_service = TaskService(db_session)
    c = _choreographer(db_session, task)

    env = await c.request_changes(
        cell_pm_agent.id,
        task.id,
        issues=["revert the out-of-scope doc change"],
    )
    assert env.error is None, env.as_dict()

    mid = await task_service.get(task.id)
    assert mid is not None
    # dev_notes untouched by request_changes.
    assert mid.dev_notes == "original developer summary"

    # A later developer handoff note overwrites dev_notes (its normal,
    # correct behavior) — this must not have destroyed anything, because
    # request_changes never wrote to dev_notes in the first place.
    await task_service.record_section_note(
        task.id, "developer", {"summary": "Reverted the doc change; back on scope."}
    )
    final = await task_service.get(task.id)
    assert final is not None
    assert "Reverted the doc change" in (final.dev_notes or "")

    # The PM's reject is still fully intact in its own channels.
    assert "revert the out-of-scope doc change" in (final.pm_notes or "")
    ledger = await ReviewFindingsRepository(db_session).list_for_task(task.id)
    assert len(ledger) == 1


# ---------------------------------------------------------------------------
# ceo_reject persists one origin=ceo finding (TaskService-level, no gateway)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ceo_reject_persists_one_finding(
    db_session: AsyncSession, setup: dict[str, Any]
) -> None:
    task = setup["task"]
    task.status = TaskStatus.AWAITING_CEO_APPROVAL
    task.pr_number = _PR_NUMBER
    await db_session.flush()

    task_service = TaskService(db_session)
    result = await task_service.ceo_reject(
        task.id, "The AC2 timestamp format does not match the API contract."
    )
    assert result is not None
    assert result.status == TaskStatus.NEEDS_REVISION

    findings = await ReviewFindingsRepository(db_session).list_for_task(task.id)
    assert len(findings) == 1
    assert findings[0].origin == "ceo"
    assert findings[0].author_slug == "ceo"
    assert "AC2 timestamp format" in findings[0].actual


# ---------------------------------------------------------------------------
# i_am_done — the FINDINGS_ADDRESSED resolution gate, end to end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_am_done_full_chain_blocks_then_resolves(
    db_session: AsyncSession, setup: dict[str, Any]
) -> None:
    """pending -> in_progress -> awaiting_qa (round 1, no findings, passes
    untouched) -> QA fails with 2 findings -> dev recovers -> i_am_done
    blocked (both ids named) -> i_am_done(resolved_findings=[...]) -> awaiting_qa."""
    task = setup["task"]
    dev_agent = setup["dev_agent"]
    qa_agent = setup["qa_agent"]
    task_service = TaskService(db_session)
    stub_git = _StubGit(db_session, task)
    c = Choreographer(
        ChoreographerDeps(
            task=task_service,
            work_session=_mock_work_session(),
            git=stub_git,
            a2a=AsyncMock(),
            journal=_mock_journal_with_reflect(),
            audit=AsyncMock(),
            evidence_repo=_mock_evidence_repo(),
        )
    )

    # --- Round 1: no findings exist yet — i_am_done must pass untouched. ---
    env = await c.i_will_work_on(
        dev_agent.id,
        task.id,
        plan=_GOOD_PLAN,
        steps=_STEPS,
        technical_considerations=_GOOD_TC,
        risks=_GOOD_RISKS,
    )
    assert env.error is None, env.as_dict()

    await stub_git.commit(
        branch_name=_BRANCH, message="feat(api): add /healthz", task_id=task.id
    )
    await task_service.add_progress(task.id, dev_agent.id, "implemented /healthz")
    env = await c.open_pr(dev_agent.id, task.id)
    assert env.error is None, env.as_dict()
    await task_service.record_section_note(
        task.id,
        "developer",
        {"summary": "Implemented /healthz with a happy-path test."},
    )

    env = await c.i_am_done(dev_agent.id, task.id, "tests pass")
    assert env.error is None, (
        f"round 1 i_am_done should pass untouched: {env.as_dict()}"
    )
    assert env.status == Status.AWAITING_QA.value

    # --- QA fails with two structured findings. ---
    env = await c.claim_review(qa_agent.id, task.id)
    assert env.error is None, env.as_dict()
    env = await c.fail_review(
        qa_agent.id,
        task.id,
        findings=[
            {
                "expected": "200 on the timestamp branch",
                "actual": "500 on the timestamp branch",
                "severity": "blocker",
            },
            {
                "expected": "test covers both branches",
                "actual": "only the happy path is tested",
                "severity": "major",
            },
        ],
    )
    assert env.error is None, env.as_dict()
    assert env.status == Status.NEEDS_REVISION.value

    open_findings = await ReviewFindingsRepository(db_session).list_for_task(
        task.id, status=STATUS_OPEN
    )
    assert len(open_findings) == _EXPECTED_FINDING_COUNT
    open_ids = [str(f.id)[:8] for f in open_findings]

    # --- Dev recovers into in_progress (needs_revision -> in_progress).
    # `_dev_reentry` only short-circuits in_progress/claimed same-agent
    # re-entry; needs_revision falls through to `_fresh_dev_claim`, which
    # re-enforces the rich-plan gate for a developer — so the full plan is
    # supplied again here, same as the very first claim. ---
    env = await c.i_will_work_on(
        dev_agent.id,
        task.id,
        plan=_GOOD_PLAN,
        steps=_STEPS,
        technical_considerations=_GOOD_TC,
        risks=_GOOD_RISKS,
    )
    assert env.error is None, env.as_dict()
    assert env.status == Status.IN_PROGRESS.value

    # --- i_am_done WITHOUT resolving the findings is blocked, naming both ids. ---
    env = await c.i_am_done(dev_agent.id, task.id, "fixed the bug")
    body = env.as_dict()
    assert body["error"] == "tracing_gap", body
    for fid in open_ids:
        assert f"finding:{fid}" in body["missing"], body["missing"]
    assert all(fid in body["remediate"] for fid in open_ids)

    # --- i_am_done WITH resolved_findings for both now succeeds. ---
    env = await c.i_am_done(
        dev_agent.id,
        task.id,
        "fixed the bug and added the missing test",
        resolved_findings=[
            {"finding_id": open_ids[0], "commit": "abc123", "note": "fixed the 500"},
            {"finding_id": open_ids[1], "commit": "abc123", "note": "added the test"},
        ],
    )
    assert env.error is None, f"resolved i_am_done should succeed: {env.as_dict()}"
    assert env.status == Status.AWAITING_QA.value

    remaining_open = await ReviewFindingsRepository(db_session).list_for_task(
        task.id, status=STATUS_OPEN
    )
    assert remaining_open == []
    addressed = await ReviewFindingsRepository(db_session).list_for_task(
        task.id, status=STATUS_ADDRESSED
    )
    assert len(addressed) == _EXPECTED_FINDING_COUNT


# ---------------------------------------------------------------------------
# Mixed findings + issues — both must land, not silently drop one (fail_review)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fail_review_merges_findings_and_issues_instead_of_dropping_one(
    db_session: AsyncSession, setup: dict[str, Any]
) -> None:
    """Live repro: ``findings if findings else issues_to_findings(issues)``
    silently dropped ``issues`` whenever ``findings`` was ALSO supplied. Both
    sources must land in the ledger."""
    task = setup["task"]
    qa_agent = setup["qa_agent"]
    dev_agent = setup["dev_agent"]
    task.status = TaskStatus.AWAITING_QA
    task.pr_number = _PR_NUMBER
    task.commits = [
        {"sha": uuid4().hex[:40], "message": "feat: x", "task_id": str(task.id)}
    ]
    task.self_verified = True
    task.orchestration_markers = {"original_developer": str(dev_agent.id)}
    await db_session.flush()

    c = _choreographer(db_session, task)
    await c.claim_review(qa_agent.id, task.id)

    env = await c.fail_review(
        qa_agent.id,
        task.id,
        findings=[
            {
                "file": "roboco/api/routes/health.py",
                "severity": "major",
                "expected": "returns 200",
                "actual": "returns 500 on the timestamp branch",
            }
        ],
        issues=["also missing test coverage for the timestamp branch"],
    )
    assert env.error is None, env.as_dict()

    findings = await ReviewFindingsRepository(db_session).list_for_task(task.id)
    assert len(findings) == _EXPECTED_FINDING_COUNT
    actuals = {f.actual for f in findings}
    assert "returns 500 on the timestamp branch" in actuals
    assert "also missing test coverage for the timestamp branch" in actuals


@pytest.mark.asyncio
async def test_fail_review_combined_findings_and_issues_hit_hard_cap(
    db_session: AsyncSession, setup: dict[str, Any]
) -> None:
    """The 10-item hard cap applies to the MERGED findings+issues total, not
    just whichever source the old ``if findings else`` picked."""
    task = setup["task"]
    qa_agent = setup["qa_agent"]
    task.status = TaskStatus.AWAITING_QA
    task.pr_number = _PR_NUMBER
    task.commits = [
        {"sha": uuid4().hex[:40], "message": "feat: x", "task_id": str(task.id)}
    ]
    task.self_verified = True
    await db_session.flush()

    c = _choreographer(db_session, task)
    await c.claim_review(qa_agent.id, task.id)

    findings = [
        {"expected": f"expected {i}", "actual": f"actual {i}", "severity": "minor"}
        for i in range(6)
    ]
    issues = [f"issue number {i}" for i in range(5)]
    env = await c.fail_review(qa_agent.id, task.id, findings=findings, issues=issues)
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "11" in body["message"]
    assert await ReviewFindingsRepository(db_session).list_for_task(task.id) == []


# ---------------------------------------------------------------------------
# claim_gate_review evidence: open findings ride alongside the full ledger
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_gate_review_evidence_surfaces_open_findings_separately(
    db_session: AsyncSession, setup: dict[str, Any]
) -> None:
    """Live repro: ``_build_gate_review_evidence`` returned only
    ``prior_findings`` (the full ledger, capped at 10) — 3 OPEN rows got
    crowded out by the cap. ``revision_findings`` (open-only) must ride
    alongside, mirroring QA's ``claim_review`` evidence."""
    task = setup["task"]
    reviewer = setup["reviewer_agent"]
    task.status = TaskStatus.AWAITING_PR_REVIEW
    task.assigned_to = reviewer.id
    task.claimed_by = reviewer.id
    task.pr_number = _PR_NUMBER
    await db_session.flush()

    repo = ReviewFindingsRepository(db_session)
    await repo.insert_many(
        task_id=task.id,
        origin="pr_gate",
        round=1,
        author_slug="be-pr-reviewer",
        findings=[
            Finding(
                severity=Severity.MAJOR,
                expected="task_id is a UUID",
                actual="FE sends task_id as a string",
            )
        ],
    )

    c = _choreographer(db_session, task)
    env = await c.claim_gate_review(reviewer.id, task.id)
    assert env.error is None, env.as_dict()
    evidence = env.evidence
    assert evidence is not None
    assert len(evidence["revision_findings"]) == 1
    assert evidence["revision_findings"][0]["status"] == STATUS_OPEN
    assert len(evidence["prior_findings"]) == 1


# ---------------------------------------------------------------------------
# submit_up / submit_root — FINDINGS_ADDRESSED gate + resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_up_blocked_by_open_finding_then_resolved(
    db_session: AsyncSession, setup: dict[str, Any]
) -> None:
    """Live gap: submit_up had no equivalent of i_am_done's FINDINGS_ADDRESSED
    gate — a PM could re-submit an assembled cell root with open pr_gate/pm/
    ceo-origin findings still unaddressed."""
    cell_pm = setup["cell_pm_agent"]
    task: Any = TaskTable(
        id=uuid4(),
        title="Assemble the healthz cell work",
        description="Bubble the cell's healthz work up to the Main PM",
        status=TaskStatus.IN_PROGRESS,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        team=Team.BACKEND,
        project_id=setup["project"].id,
        created_by=setup["system_agent"].id,
        assigned_to=cell_pm.id,
        claimed_by=cell_pm.id,
        branch_name="feature/backend/healthz-cell",
        acceptance_criteria=["Returns 200"],
    )
    db_session.add(task)
    await db_session.flush()

    repo = ReviewFindingsRepository(db_session)
    rows = await repo.insert_many(
        task_id=task.id,
        origin="pr_gate",
        round=1,
        author_slug="be-pr-reviewer",
        findings=[
            Finding(
                severity=Severity.MAJOR,
                expected="all checks green",
                actual="lint failing on health.py",
            )
        ],
    )
    finding_id8 = str(rows[0].id)[:8]

    c = _choreographer(db_session, task)

    blocked = await c.submit_up(cell_pm.id, task.id, "bubbling up the cell's work")
    body = blocked.as_dict()
    assert body["error"] == "tracing_gap", body
    assert f"finding:{finding_id8}" in body["missing"]

    ok = await c.submit_up(
        cell_pm.id,
        task.id,
        "bubbling up the cell's work now that lint is fixed",
        resolved_findings=[
            {"finding_id": finding_id8, "commit": "abc123", "note": "fixed lint"}
        ],
    )
    assert ok.error is None, ok.as_dict()
    assert ok.status == Status.AWAITING_PR_REVIEW.value

    addressed = await repo.list_for_task(task.id, status=STATUS_ADDRESSED)
    assert len(addressed) == 1


@pytest.mark.asyncio
async def test_non_owner_resolved_findings_never_mutate_the_ledger(
    db_session: AsyncSession, setup: dict[str, Any]
) -> None:
    """A stale non-owner PM's resolved_findings must not be applied: the
    ownership rejection commits the session, so a pre-guard apply would
    persist — zeroing the FINDINGS_ADDRESSED gate for the real owner."""
    cell_pm = setup["cell_pm_agent"]
    main_pm = setup["main_pm_agent"]
    task: Any = TaskTable(
        id=uuid4(),
        title="Assemble the healthz cell work",
        description="Bubble the cell's healthz work up to the Main PM",
        status=TaskStatus.IN_PROGRESS,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        team=Team.BACKEND,
        project_id=setup["project"].id,
        created_by=setup["system_agent"].id,
        assigned_to=cell_pm.id,
        claimed_by=cell_pm.id,
        branch_name="feature/backend/healthz-cell",
        acceptance_criteria=["Returns 200"],
    )
    db_session.add(task)
    await db_session.flush()

    repo = ReviewFindingsRepository(db_session)
    rows = await repo.insert_many(
        task_id=task.id,
        origin="pr_gate",
        round=1,
        author_slug="be-pr-reviewer",
        findings=[
            Finding(
                severity=Severity.MAJOR,
                expected="all checks green",
                actual="lint failing on health.py",
            )
        ],
    )
    finding_id8 = str(rows[0].id)[:8]

    c = _choreographer(db_session, task)
    rejected = await c.submit_up(
        main_pm.id,
        task.id,
        "sneaky resubmit from a PM that does not own this task",
        resolved_findings=[
            {"finding_id": finding_id8, "commit": "abc123", "note": "not mine"}
        ],
    )
    assert rejected.error == "not_authorized", rejected.as_dict()
    still_open = await repo.list_for_task(task.id, status=STATUS_OPEN)
    assert len(still_open) == 1, "non-owner resolutions must not touch the ledger"


@pytest.mark.asyncio
async def test_submit_root_blocked_by_open_finding_then_resolved(
    db_session: AsyncSession, setup: dict[str, Any]
) -> None:
    """submit_root's root analogue of the submit_up gap above."""
    main_pm = setup["main_pm_agent"]
    task: Any = TaskTable(
        id=uuid4(),
        title="Assemble the healthz root work",
        description="Open the root->master PR for the healthz rollout",
        status=TaskStatus.IN_PROGRESS,
        priority=2,
        task_type=TaskType.PLANNING,
        nature=TaskNature.TECHNICAL,
        team=Team.MAIN_PM,
        project_id=setup["project"].id,
        created_by=setup["system_agent"].id,
        assigned_to=main_pm.id,
        claimed_by=main_pm.id,
        branch_name="feature/main_pm/healthz-root",
        acceptance_criteria=["Returns 200"],
    )
    db_session.add(task)
    await db_session.flush()

    repo = ReviewFindingsRepository(db_session)
    rows = await repo.insert_many(
        task_id=task.id,
        origin="pm",
        round=1,
        author_slug="main-pm",
        findings=[
            Finding(
                severity=Severity.MAJOR,
                expected="scope limited to the endpoint change",
                actual="CLAUDE.md modified out of scope",
            )
        ],
    )
    finding_id8 = str(rows[0].id)[:8]

    c = _choreographer(db_session, task)

    blocked = await c.submit_root(main_pm.id, task.id, "opening the root->master PR")
    body = blocked.as_dict()
    assert body["error"] == "tracing_gap", body
    assert f"finding:{finding_id8}" in body["missing"]

    ok = await c.submit_root(
        main_pm.id,
        task.id,
        "opening the root->master PR now that scope is reverted",
        resolved_findings=[
            {"finding_id": finding_id8, "commit": "def456", "note": "reverted"}
        ],
    )
    assert ok.error is None, ok.as_dict()
    assert ok.status == Status.AWAITING_PR_REVIEW.value

    addressed = await repo.list_for_task(task.id, status=STATUS_ADDRESSED)
    assert len(addressed) == 1


# ---------------------------------------------------------------------------
# complete() (PM merge) stamps pm-origin addressed findings verified
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cell_pm_complete_stamps_pm_origin_addressed_findings_verified(
    db_session: AsyncSession, setup: dict[str, Any]
) -> None:
    """Live gap: pr_pass's stamp_addressed_verified(origin='pr_gate') was the
    ONLY caller of the addressed->verified lifecycle other than i_am_done — a
    pm-origin finding (request_changes) never got confirmed anywhere. The PM's
    own merge (complete) IS that confirmation."""
    cell_pm = setup["cell_pm_agent"]
    task: Any = TaskTable(
        id=uuid4(),
        title="Cell task ready to merge",
        description="Merge the cell->root PR",
        status=TaskStatus.AWAITING_PM_REVIEW,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        team=Team.BACKEND,
        project_id=setup["project"].id,
        created_by=setup["system_agent"].id,
        assigned_to=cell_pm.id,
        pr_number=_PR_NUMBER,
        branch_name=_BRANCH,
        acceptance_criteria=["Returns 200"],
    )
    db_session.add(task)
    await db_session.flush()

    repo = ReviewFindingsRepository(db_session)
    rows = await repo.insert_many(
        task_id=task.id,
        origin="pm",
        round=1,
        author_slug="be-pm",
        findings=[
            Finding(
                severity=Severity.MAJOR,
                expected="scope limited to the endpoint change",
                actual="CLAUDE.md modified out of scope",
            )
        ],
    )
    await repo.mark_addressed(task.id, str(rows[0].id), commit="abc", note="reverted")

    c = _choreographer(db_session, task)
    env = await c.cell_pm_complete(
        cell_pm.id, task.id, "reviewed and approved; scope reverted"
    )
    assert env.error is None, env.as_dict()

    verified = await repo.list_for_task(task.id, status=STATUS_VERIFIED)
    assert len(verified) == 1
    assert verified[0].id == rows[0].id


# ---------------------------------------------------------------------------
# ceo_approve stamps ceo-origin addressed findings verified
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ceo_approve_stamps_ceo_origin_addressed_findings_verified(
    db_session: AsyncSession, setup: dict[str, Any]
) -> None:
    task = setup["task"]
    task.status = TaskStatus.AWAITING_CEO_APPROVAL
    await db_session.flush()

    repo = ReviewFindingsRepository(db_session)
    rows = await repo.insert_many(
        task_id=task.id,
        origin="ceo",
        round=1,
        author_slug="ceo",
        findings=[
            Finding(
                severity=Severity.BLOCKER,
                expected="CEO sign-off on this task",
                actual="the timestamp format did not match the API contract",
            )
        ],
    )
    await repo.mark_addressed(task.id, str(rows[0].id), commit="abc", note="fixed")

    task_service = TaskService(db_session)
    result = await task_service.ceo_approve(task.id, "looks good")
    assert result is not None
    assert result.status == TaskStatus.COMPLETED

    verified = await repo.list_for_task(task.id, status=STATUS_VERIFIED)
    assert len(verified) == 1
    assert verified[0].id == rows[0].id


# ---------------------------------------------------------------------------
# ceo_reject — reason validation (never a 500) + coordination-root rounds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_reason", ["", "wip", "n/a", "-", "   "])
async def test_ceo_reject_rejects_trivial_reason_cleanly(
    db_session: AsyncSession, setup: dict[str, Any], bad_reason: str
) -> None:
    """Live repro: an empty/'wip'/'n/a'/'-'/whitespace reason built a Finding
    directly and let pydantic's uncaught ValidationError surface as a 500.
    The reason must be validated BEFORE the Finding is constructed."""
    task = setup["task"]
    task.status = TaskStatus.AWAITING_CEO_APPROVAL
    await db_session.flush()

    task_service = TaskService(db_session)
    with pytest.raises(ValidationError):
        await task_service.ceo_reject(task.id, bad_reason)

    assert await ReviewFindingsRepository(db_session).list_for_task(task.id) == []
    refreshed = await task_service.get(task.id)
    assert refreshed is not None
    assert refreshed.status == TaskStatus.AWAITING_CEO_APPROVAL


async def _seed_main_pm_and_ceo_fixed_ids(
    db_session: AsyncSession,
) -> tuple[UUID, UUID]:
    """Ensure the fixed-UUID main-pm/ceo agents exist — FK targets for the
    coordination-root reject path's reassignment + CEO-attributed audit row."""
    main_pm_id = UUID(AGENT_UUIDS["main-pm"])
    if await db_session.get(AgentTable, main_pm_id) is None:
        db_session.add(
            AgentTable(
                id=main_pm_id,
                name="Main PM",
                slug="main-pm",
                role=AgentRole.MAIN_PM,
                team=Team.MAIN_PM,
                status=AgentStatus.ACTIVE,
                model_config={},
                system_prompt="pm",
                capabilities=[],
                permissions={},
                metrics={},
            )
        )
    ceo_id = UUID(AGENT_UUIDS["ceo"])
    if await db_session.get(AgentTable, ceo_id) is None:
        db_session.add(
            AgentTable(
                id=ceo_id,
                name="CEO",
                slug="ceo",
                role=AgentRole.CEO,
                team=Team.MAIN_PM,
                status=AgentStatus.ACTIVE,
                model_config={},
                system_prompt="ceo",
                capabilities=[],
                permissions={},
                metrics={},
            )
        )
    await db_session.flush()
    return main_pm_id, ceo_id


@pytest.mark.asyncio
async def test_ceo_reject_coordination_root_bumps_round_and_audit_each_time(
    db_session: AsyncSession, setup: dict[str, Any]
) -> None:
    """Live repro: two consecutive coordination-root rejects both wrote ledger
    round=1 — the PENDING-routing path never bumped revision_count (only a
    to_status==NEEDS_REVISION transition did) and never emitted the NAMED
    task.ceo_reject audit event the rework scorecard attributes rejections by."""
    await _seed_main_pm_and_ceo_fixed_ids(db_session)
    task: Any = TaskTable(
        id=uuid4(),
        title="Ship the healthz feature across both repos",
        description="Coordinate the healthz rollout across the app + engine repos",
        status=TaskStatus.AWAITING_CEO_APPROVAL,
        priority=2,
        task_type=TaskType.PLANNING,
        nature=TaskNature.TECHNICAL,
        team=Team.MAIN_PM,
        project_id=None,
        product_id=None,
        batch_id=uuid4(),
        parent_task_id=None,
        created_by=setup["system_agent"].id,
        acceptance_criteria=["Both repos ship the endpoint"],
    )
    db_session.add(task)
    await db_session.flush()

    task_service = TaskService(db_session)

    first = await task_service.ceo_reject(task.id, "The rollout plan misses repo B.")
    assert first is not None
    assert first.status == TaskStatus.PENDING
    assert first.revision_count == 1

    findings = await ReviewFindingsRepository(db_session).list_for_task(task.id)
    assert [f.round for f in findings] == [1]

    # Re-escalate for a second round (mirrors the Main PM re-submitting and
    # the CEO rejecting again).
    task.status = TaskStatus.AWAITING_CEO_APPROVAL
    await db_session.flush()
    second = await task_service.ceo_reject(task.id, "Still missing repo B's rollout.")
    assert second is not None
    assert second.revision_count == _EXPECTED_ROUND_COUNT

    findings = await ReviewFindingsRepository(db_session).list_for_task(task.id)
    assert sorted(f.round for f in findings) == [1, _EXPECTED_ROUND_COUNT]

    ceo_reject_rows = (
        (
            await db_session.execute(
                select(AuditLogTable).where(
                    AuditLogTable.target_id == task.id,
                    AuditLogTable.event_type == "task.ceo_reject",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(ceo_reject_rows) == _EXPECTED_ROUND_COUNT
