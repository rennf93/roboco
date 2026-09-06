"""Pin the Markdown rendering of a per-task verification attestation.

``render_attestation_markdown`` renders directly off an already-assembled
``TaskAttestation`` — these tests build that dataclass by hand (no DB), the
same "task fixture" shape the parent task asks for, with mixed finding
states (open, addressed, verified, waived) so every status renders.
"""

from __future__ import annotations

from datetime import UTC, datetime

from roboco.services.attestation import (
    AttestedCriterion,
    AttestedFinding,
    CiVerdict,
    ConventionFinding,
    FindingsRound,
    ReviewerChainEntry,
    TaskAttestation,
    WorkSessionRef,
    render_attestation_markdown,
)

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
