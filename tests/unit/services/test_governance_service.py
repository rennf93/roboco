"""Unit tests for GovernanceService — gate-chain extraction, findings summary
reuse, conventions verdict, and 404-on-missing-task.

Follows the ``test_review_findings_repository.py`` real-DB pattern: real
Postgres via the session-scoped test DB, ``Base.metadata.create_all`` builds
the schema from live ORM metadata.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from roboco.db.tables import (
    AgentTable,
    AuditLogTable,
    ProjectConventionFindingTable,
    ProjectTable,
    TaskTable,
)
from roboco.foundation.policy.content import Finding, Severity
from roboco.models.base import AgentRole, AgentStatus, TaskStatus, TaskType, Team
from roboco.services.governance import GovernanceService
from roboco.services.repositories.review_findings import ReviewFindingsRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_agent(session: AsyncSession) -> UUID:
    agent = AgentTable(
        id=uuid4(),
        name="Gov Test Agent",
        slug=f"gov-test-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="gov test",
        capabilities=[],
        permissions={},
        metrics={},
    )
    session.add(agent)
    await session.flush()
    return UUID(str(agent.id))


async def _seed_task(
    session: AsyncSession,
    created_by: UUID,
    *,
    status: TaskStatus = TaskStatus.AWAITING_QA,
    self_verified: bool = True,
    qa_verified: bool | None = None,
    revision_count: int = 0,
) -> UUID:
    task = TaskTable(
        id=uuid4(),
        title="governance seed task",
        description="seed",
        acceptance_criteria=["seeded"],
        status=status,
        priority=2,
        task_type=TaskType.CODE,
        team=Team.BACKEND,
        created_by=created_by,
        self_verified=self_verified,
        qa_verified=qa_verified,
        revision_count=revision_count,
    )
    session.add(task)
    await session.flush()
    return UUID(str(task.id))


async def _add_audit_event(
    session: AsyncSession,
    task_id: UUID,
    event_type: str,
    *,
    timestamp: datetime | None = None,
) -> None:
    ev = AuditLogTable(
        id=uuid4(),
        event_type=event_type,
        target_type="task",
        target_id=task_id,
        severity="info",
        details={},
        timestamp=timestamp or datetime.now(UTC),
    )
    session.add(ev)
    await session.flush()


async def _add_convention_finding(
    session: AsyncSession,
    task_id: UUID,
    project_id: UUID,
    *,
    level: str = "block",
) -> None:
    finding = ProjectConventionFindingTable(
        id=uuid4(),
        project_id=project_id,
        task_id=task_id,
        file="roboco/services/foo.py",
        line=42,
        rule="no_models_in_routes",
        level=level,
        kind="placement",
        message="model defined in route",
        detected_at=datetime.now(UTC),
    )
    session.add(finding)
    await session.flush()


def _finding(**overrides: object) -> Finding:
    base: dict[str, object] = {
        "file": "roboco/services/task.py",
        "line": 10,
        "severity": Severity.MAJOR,
        "criterion": "seeded",
        "expected": "expected",
        "actual": "actual",
        "fix": "fix it",
    }
    base.update(overrides)
    return Finding.model_validate(base)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_report_returns_none_for_missing_task(
    db_session: AsyncSession,
) -> None:
    """A non-existent task_id returns None (the route maps this to 404)."""
    service = GovernanceService(db_session)
    report = await service.get_report(uuid4())
    assert report is None


@pytest.mark.asyncio
async def test_gate_chain_extracts_passed_stages_from_audit_events(
    db_session: AsyncSession,
) -> None:
    """Audit events for task.verifying and task.awaiting_qa mark
    self_verification as passed, and the task's qa_verified marks QA as
    passed."""
    agent_id = await _seed_agent(db_session)
    task_id = await _seed_task(
        db_session,
        agent_id,
        status=TaskStatus.AWAITING_QA,
        self_verified=True,
    )
    base = datetime.now(UTC) - timedelta(hours=2)
    await _add_audit_event(db_session, task_id, "task.verifying", timestamp=base)
    await _add_audit_event(
        db_session,
        task_id,
        "task.awaiting_qa",
        timestamp=base + timedelta(minutes=30),
    )

    service = GovernanceService(db_session)
    report = await service.get_report(task_id)
    assert report is not None

    gate_map = {g.gate: g for g in report.gate_chain}
    assert gate_map["self_verification"].status == "passed"
    assert gate_map["qa"].status == "not_reached"
    # Task progressed past development (AWAITING_QA) with no block findings —
    # conventions gate was checked and passed.
    assert gate_map["conventions"].status == "passed"


@pytest.mark.asyncio
async def test_gate_chain_marks_qa_failed_on_qa_fail_event(
    db_session: AsyncSession,
) -> None:
    """A task.qa_fail audit event marks the QA gate as failed."""
    agent_id = await _seed_agent(db_session)
    task_id = await _seed_task(
        db_session,
        agent_id,
        status=TaskStatus.NEEDS_REVISION,
        revision_count=1,
    )
    await _add_audit_event(db_session, task_id, "task.qa_fail")

    service = GovernanceService(db_session)
    report = await service.get_report(task_id)
    assert report is not None

    gate_map = {g.gate: g for g in report.gate_chain}
    assert gate_map["qa"].status == "failed"
    assert gate_map["qa"].detail == "bounced"
    assert report.revision_count == 1


@pytest.mark.asyncio
async def test_gate_chain_marks_pr_gate_and_pm_review_from_audit_events(
    db_session: AsyncSession,
) -> None:
    """task.awaiting_pm_review marks pr_gate as passed, and
    task.awaiting_ceo_approval marks pm_review as passed."""
    agent_id = await _seed_agent(db_session)
    task_id = await _seed_task(
        db_session,
        agent_id,
        status=TaskStatus.AWAITING_CEO_APPROVAL,
    )
    await _add_audit_event(db_session, task_id, "task.awaiting_pm_review")
    await _add_audit_event(db_session, task_id, "task.awaiting_ceo_approval")

    service = GovernanceService(db_session)
    report = await service.get_report(task_id)
    assert report is not None

    gate_map = {g.gate: g for g in report.gate_chain}
    assert gate_map["pr_gate"].status == "passed"
    assert gate_map["pm_review"].status == "passed"
    assert gate_map["ceo_approval"].status == "not_reached"


@pytest.mark.asyncio
async def test_gate_chain_marks_ceo_approval_passed_on_completed(
    db_session: AsyncSession,
) -> None:
    """task.completed marks ceo_approval as passed."""
    agent_id = await _seed_agent(db_session)
    task_id = await _seed_task(
        db_session,
        agent_id,
        status=TaskStatus.COMPLETED,
        qa_verified=True,
    )
    await _add_audit_event(db_session, task_id, "task.completed")

    service = GovernanceService(db_session)
    report = await service.get_report(task_id)
    assert report is not None

    gate_map = {g.gate: g for g in report.gate_chain}
    assert gate_map["ceo_approval"].status == "passed"


@pytest.mark.asyncio
async def test_conventions_verdict_counts_block_and_warn(
    db_session: AsyncSession,
) -> None:
    """Block and warn convention findings are counted correctly, and a
    block finding marks the conventions gate as failed."""
    agent_id = await _seed_agent(db_session)
    task_id = await _seed_task(
        db_session,
        agent_id,
        status=TaskStatus.NEEDS_REVISION,
    )
    # Need a project for the convention finding FK.
    project_id = uuid4()
    project = ProjectTable(
        id=project_id,
        name="gov test project",
        slug=f"gov-test-{uuid4().hex[:8]}",
        git_url="https://github.com/test/repo",
        default_branch="main",
        assigned_cell=Team.BACKEND,
        created_by=agent_id,
    )
    db_session.add(project)
    await db_session.flush()

    await _add_convention_finding(db_session, task_id, project_id, level="block")
    await _add_convention_finding(db_session, task_id, project_id, level="warn")
    await _add_convention_finding(db_session, task_id, project_id, level="warn")

    service = GovernanceService(db_session)
    report = await service.get_report(task_id)
    assert report is not None

    assert report.conventions_block_count == 1
    assert report.conventions_warn_count == 2
    gate_map = {g.gate: g for g in report.gate_chain}
    assert gate_map["conventions"].status == "failed"
    assert gate_map["conventions"].detail == "1 block finding(s)"


@pytest.mark.asyncio
async def test_findings_summary_reuses_review_findings_repository(
    db_session: AsyncSession,
) -> None:
    """The findings summary in the governance report matches what
    ReviewFindingsRepository.status_counts_for_task returns, routed through
    the findings_summary schema helper."""
    agent_id = await _seed_agent(db_session)
    task_id = await _seed_task(db_session, agent_id)

    repo = ReviewFindingsRepository(db_session)
    await repo.insert_many(
        task_id=task_id,
        origin="qa",
        round=1,
        author_slug="be-qa",
        findings=[
            _finding(file="a.py", line=1),
            _finding(file="b.py", line=2, severity=Severity.MINOR),
        ],
    )

    service = GovernanceService(db_session)
    report = await service.get_report(task_id)
    assert report is not None

    # Two open QA findings.
    assert len(report.findings_summary) == 1
    row = report.findings_summary[0]
    assert row.origin == "qa"
    assert row.open == 2


@pytest.mark.asyncio
async def test_report_includes_task_status_and_revision_count(
    db_session: AsyncSession,
) -> None:
    """The report surfaces the task's current status and revision_count."""
    agent_id = await _seed_agent(db_session)
    task_id = await _seed_task(
        db_session,
        agent_id,
        status=TaskStatus.AWAITING_PM_REVIEW,
        revision_count=3,
    )

    service = GovernanceService(db_session)
    report = await service.get_report(task_id)
    assert report is not None
    assert report.task_status == "awaiting_pm_review"
    assert report.revision_count == 3


@pytest.mark.asyncio
async def test_gate_chain_has_six_ordered_stages(
    db_session: AsyncSession,
) -> None:
    """The gate chain always has exactly six stages in canonical order,
    regardless of how far the task has progressed."""
    agent_id = await _seed_agent(db_session)
    task_id = await _seed_task(
        db_session,
        agent_id,
        status=TaskStatus.PENDING,
        self_verified=False,
    )

    service = GovernanceService(db_session)
    report = await service.get_report(task_id)
    assert report is not None

    gate_names = [g.gate for g in report.gate_chain]
    assert gate_names == [
        "conventions",
        "self_verification",
        "qa",
        "pr_gate",
        "pm_review",
        "ceo_approval",
    ]
    # All not_reached for a pending task that hasn't started any gate.
    assert all(g.status == "not_reached" for g in report.gate_chain)
