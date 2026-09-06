"""Pin the Markdown rendering of a per-task verification attestation, plus
the DB-backed assembly logic in ``assemble_task_attestation``.

``render_attestation_markdown`` renders directly off an already-assembled
``TaskAttestation`` — the render tests build that dataclass by hand (no DB),
the same "task fixture" shape the parent task asks for, with mixed finding
states (open, addressed, verified, waived) so every status renders. The
assembly tests below exercise ``assemble_task_attestation`` itself against a
real DB session, covering the qa_notes ``[AC]`` stamp match by criterion id
AND by text, and the reviewer-chain duplicate-row skip.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from roboco.db.tables import AuditLogTable, TaskTable
from roboco.models import Team
from roboco.models.base import TaskNature, TaskStatus, TaskType
from roboco.services.attestation import (
    AttestedCriterion,
    AttestedFinding,
    CiVerdict,
    ConventionFinding,
    FindingsRound,
    ReviewerChainEntry,
    TaskAttestation,
    WorkSessionRef,
    assemble_task_attestation,
    render_attestation_markdown,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_NOW = datetime(2026, 9, 5, 12, 0, 0, tzinfo=UTC)
_EMPTY_SECTION_COUNT = 4  # AC, conventions, reviewer, sessions


def _finding(status: str, *, resolution_note: str | None = None) -> AttestedFinding:
    return AttestedFinding(
        id=f"finding-{status}",
        round=1,
        origin="qa",
        severity="major",
        status=status,
        file="roboco/services/attestation.py",
        line=42,
        criterion="AC1",
        expected="the field is present",
        actual="the field was missing",
        fix="add the field",
        resolution_note=resolution_note,
        created_at=_NOW,
    )


def _build_attestation() -> TaskAttestation:
    """A task fixture with mixed finding states: open, addressed, verified,
    waived — the exact ledger status vocabulary the parent task names."""
    return TaskAttestation(
        task_id="11111111-1111-1111-1111-111111111111",
        title="Add the attestation route",
        status="awaiting_qa",
        team="backend",
        project_slug="roboco-api",
        branch_name="feature/backend/d971bd3c",
        pr_number=1234,
        pr_url="https://github.com/example/roboco/pull/1234",
        revision_count=1,
        commits=({"sha": "abc123", "message": "add route"},),
        work_sessions=(
            WorkSessionRef(
                agent_slug="be-dev-1",
                branch_name="feature/backend/d971bd3c",
                base_branch="feature/backend/7fbb2241--3e269117",
                target_branch="feature/backend/7fbb2241--3e269117",
                status="active",
                commits=("abc123",),
                pr_number=1234,
                pr_url="https://github.com/example/roboco/pull/1234",
                pr_status="open",
                started_at=_NOW,
                ended_at=None,
            ),
        ),
        acceptance_criteria=(
            AttestedCriterion(
                id="ac-1", text="Route returns JSON", verified=True, evidence="tested"
            ),
            AttestedCriterion(id=None, text="Route returns Markdown", verified=False),
        ),
        findings_by_round=(
            FindingsRound(
                round=1,
                findings=(
                    _finding("open"),
                    _finding("addressed", resolution_note="fixed in abc123"),
                    _finding("verified"),
                    _finding("waived", resolution_note="minor, waived by auditor"),
                ),
            ),
        ),
        ci=CiVerdict(state="success", head_sha="abc123", failing_checks=()),
        conventions_findings=(
            ConventionFinding(
                file="roboco/api/routes/tasks.py",
                line=10,
                rule="thin_routes",
                level="warn",
                kind="helper",
                message="helper defined inline",
            ),
        ),
        reviewer_chain=(
            ReviewerChainEntry(
                to_status="in_progress",
                agent_slug="be-dev-1",
                agent_role="developer",
                timestamp=_NOW,
            ),
        ),
        generated_at=_NOW,
    )


def test_render_includes_header_identity_and_refs() -> None:
    md = render_attestation_markdown(_build_attestation())
    assert "# Verification attestation — Add the attestation route" in md
    assert "`11111111-1111-1111-1111-111111111111`" in md
    assert "**Status:** awaiting_qa" in md
    assert "(#1234)" in md
    assert "https://github.com/example/roboco/pull/1234" in md


def test_render_acceptance_criteria_checklist() -> None:
    md = render_attestation_markdown(_build_attestation())
    assert "- [x] Route returns JSON" in md
    assert "Evidence: tested" in md
    assert "- [ ] Route returns Markdown" in md


def test_render_findings_covers_every_ledger_status() -> None:
    md = render_attestation_markdown(_build_attestation())
    assert "### Round 1" in md
    for status in ("open", "addressed", "verified", "waived"):
        assert f"({status})" in md
    assert "Resolution: fixed in abc123" in md
    assert "Resolution: minor, waived by auditor" in md
    assert "fix: add the field" in md


def test_render_ci_conventions_reviewer_chain_and_sessions() -> None:
    md = render_attestation_markdown(_build_attestation())
    assert "**State:** success (head `abc123`)" in md
    assert "thin_routes" in md
    assert "→ in_progress" in md
    assert "feature/backend/d971bd3c" in md
    assert "1 commit(s)" in md


def test_render_empty_sections_degrade_to_placeholders() -> None:
    attestation = TaskAttestation(
        task_id="22222222-2222-2222-2222-222222222222",
        title="Untouched task",
        status="pending",
        team="backend",
        project_slug=None,
        branch_name=None,
        pr_number=None,
        pr_url=None,
        revision_count=0,
        commits=(),
        work_sessions=(),
        acceptance_criteria=(),
        findings_by_round=(),
        ci=CiVerdict(state="not_available"),
        conventions_findings=(),
        reviewer_chain=(),
        generated_at=_NOW,
    )
    md = render_attestation_markdown(attestation)
    assert "`n/a`" in md  # branch
    assert "**PR:** n/a" in md
    assert "_No findings raised._" in md
    assert md.count("_None recorded._") == _EMPTY_SECTION_COUNT


def _seed_task(**overrides: object) -> TaskTable:
    defaults: dict[str, object] = {
        "id": uuid4(),
        "title": "Attested task",
        "description": "d",
        "acceptance_criteria": ["Criterion by text", "Criterion by id"],
        "acceptance_criteria_ids": ["", "ac-2"],
        "status": TaskStatus.AWAITING_QA,
        "priority": 2,
        "task_type": TaskType.CODE,
        "nature": TaskNature.TECHNICAL,
        "created_by": uuid4(),
        "team": Team.BACKEND,
        "revision_count": 0,
    }
    defaults.update(overrides)
    return TaskTable(**defaults)


@pytest.mark.asyncio
async def test_assemble_matches_ac_stamps_by_text_and_by_id(
    db_session: AsyncSession,
) -> None:
    """``criteria_verified``'s ``criterion`` may be the AC's stable id or its
    exact text — a stamp keyed either way must mark that criterion verified,
    not just the text-keyed one."""
    task = _seed_task(
        qa_notes=(
            "[AC] Criterion by text — verified: seen in the diff\n"
            "[AC] ac-2 — verified: seen via id lookup"
        ),
    )
    db_session.add(task)
    await db_session.flush()

    attestation = await assemble_task_attestation(db_session, task)

    by_text = {c.text: c for c in attestation.acceptance_criteria}
    assert by_text["Criterion by text"].verified is True
    assert by_text["Criterion by text"].evidence == "seen in the diff"
    assert by_text["Criterion by id"].verified is True
    assert by_text["Criterion by id"].evidence == "seen via id lookup"


@pytest.mark.asyncio
async def test_assemble_reviewer_chain_skips_rejector_duplicate_rows(
    db_session: AsyncSession,
) -> None:
    """``TaskService._audit_events_for`` emits a rejector-attributed duplicate
    row (``task.qa_fail`` etc.) alongside the generic ``task.<to_status>``
    row for rework attribution — the chain must show each real transition
    exactly once, not double it."""
    task = _seed_task(qa_notes=None)
    db_session.add(task)
    await db_session.flush()

    db_session.add_all(
        [
            AuditLogTable(
                id=uuid4(),
                event_type="task.needs_revision",
                target_type="task",
                target_id=task.id,
                severity="info",
                details={"to_status": "needs_revision"},
                timestamp=datetime(2026, 9, 5, 10, 0, 0, tzinfo=UTC),
            ),
            AuditLogTable(
                id=uuid4(),
                event_type="task.qa_fail",
                target_type="task",
                target_id=task.id,
                severity="info",
                details={"to_status": "needs_revision"},
                timestamp=datetime(2026, 9, 5, 10, 0, 1, tzinfo=UTC),
            ),
        ]
    )
    await db_session.flush()

    attestation = await assemble_task_attestation(db_session, task)

    to_statuses = [rc.to_status for rc in attestation.reviewer_chain]
    assert to_statuses == ["needs_revision"]


@pytest.mark.asyncio
async def test_assemble_no_project_resolves_ci_not_available(
    db_session: AsyncSession,
) -> None:
    """A task with no project_id skips project/git-service resolution
    entirely and degrades the CI verdict to not_available — no live call is
    attempted."""
    task = _seed_task(project_id=None)
    db_session.add(task)
    await db_session.flush()

    attestation = await assemble_task_attestation(db_session, task)

    assert attestation.project_slug is None
    assert attestation.ci.state == "not_available"
