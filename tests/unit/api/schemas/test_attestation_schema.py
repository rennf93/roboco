"""Pin the JSON shape of the per-task verification attestation.

``attestation_to_response`` mirrors the assembler's frozen dataclasses
field-for-field into the Pydantic response — this test builds the same
"task fixture" shape as the Markdown-render tests (mixed finding states:
open, addressed, verified, waived) and asserts the response shape the
frontend cell's download action depends on.
"""

from __future__ import annotations

from datetime import UTC, datetime

from roboco.api.schemas.attestation import (
    TaskAttestationResponse,
    attestation_to_response,
)
from roboco.services.attestation import (
    AttestedCriterion,
    AttestedFinding,
    CiVerdict,
    ConventionFinding,
    FindingsRound,
    ReviewerChainEntry,
    TaskAttestation,
    WorkSessionRef,
)

_NOW = datetime(2026, 9, 5, 12, 0, 0, tzinfo=UTC)
_PR_NUMBER = 1234
_AC_COUNT = 2
_FINDING_COUNT = 4


def _build_attestation() -> TaskAttestation:
    return TaskAttestation(
        task_id="11111111-1111-1111-1111-111111111111",
        title="Add the attestation route",
        status="awaiting_qa",
        team="backend",
        project_slug="roboco-api",
        branch_name="feature/backend/d971bd3c",
        pr_number=_PR_NUMBER,
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
                pr_number=_PR_NUMBER,
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
                findings=tuple(
                    AttestedFinding(
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
                        resolution_note=None,
                        created_at=_NOW,
                    )
                    for status in ("open", "addressed", "verified", "waived")
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


def test_attestation_to_response_returns_pydantic_model() -> None:
    response = attestation_to_response(_build_attestation())
    assert isinstance(response, TaskAttestationResponse)


def test_attestation_to_response_pins_identity_and_refs() -> None:
    response = attestation_to_response(_build_attestation())
    assert response.task_id == "11111111-1111-1111-1111-111111111111"
    assert response.title == "Add the attestation route"
    assert response.status == "awaiting_qa"
    assert response.team == "backend"
    assert response.project_slug == "roboco-api"
    assert response.branch_name == "feature/backend/d971bd3c"
    assert response.pr_number == _PR_NUMBER
    assert response.revision_count == 1
    assert response.commits == [{"sha": "abc123", "message": "add route"}]


def test_attestation_to_response_pins_acceptance_criteria() -> None:
    response = attestation_to_response(_build_attestation())
    assert len(response.acceptance_criteria) == _AC_COUNT
    verified, unverified = response.acceptance_criteria
    assert verified.id == "ac-1"
    assert verified.verified is True
    assert verified.evidence == "tested"
    assert unverified.verified is False
    assert unverified.evidence is None


def test_attestation_to_response_pins_every_finding_status() -> None:
    response = attestation_to_response(_build_attestation())
    assert len(response.findings_by_round) == 1
    round_1 = response.findings_by_round[0]
    assert round_1.round == 1
    statuses = {f.status for f in round_1.findings}
    assert statuses == {"open", "addressed", "verified", "waived"}


def test_attestation_to_response_pins_ci_conventions_and_reviewer_chain() -> None:
    response = attestation_to_response(_build_attestation())
    assert response.ci.state == "success"
    assert response.ci.head_sha == "abc123"
    assert len(response.conventions_findings) == 1
    assert response.conventions_findings[0].rule == "thin_routes"
    assert len(response.reviewer_chain) == 1
    assert response.reviewer_chain[0].to_status == "in_progress"
    assert len(response.work_sessions) == 1
    assert response.work_sessions[0].pr_number == _PR_NUMBER


def test_attestation_to_response_serializes_to_json() -> None:
    response = attestation_to_response(_build_attestation())
    # Round-trips through Pydantic's JSON encoder without error — pins that
    # every nested field (datetimes included) is actually JSON-serializable,
    # the same path the FastAPI route relies on for the default format=json.
    payload = response.model_dump(mode="json")
    assert payload["task_id"] == "11111111-1111-1111-1111-111111111111"
    assert len(payload["findings_by_round"][0]["findings"]) == _FINDING_COUNT
