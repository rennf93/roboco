"""Schemas for the gated release-manager CEO surface.

Runtime annotations (no ``from __future__ import annotations``): the
certificate response's ``datetime`` fields must be resolvable when Pydantic
builds the model, mirroring ``schemas/x.py``.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from roboco.api.schemas.attestation import AttestationCiVerdictResponse
from roboco.api.schemas.project import ConventionFinding
from roboco.api.schemas.tasks import TaskFindingResponse, finding_to_response

if TYPE_CHECKING:
    from roboco.services.release_verification import MemberCiVerdict, MemberReceipt


class ReleaseGapModel(BaseModel):
    """One readiness shortfall the CEO must weigh before approving."""

    category: str
    detail: str


class ReleaseReportModel(BaseModel):
    """The deterministic readiness report rendered for the CEO."""

    proposed_version: str
    bump_kind: str
    change_summary: list[str]
    drafted_changelog: str
    version_bump_plan: list[str]
    gaps: list[ReleaseGapModel]
    migration_notes: list[str]
    gate_state: str


class ReleaseMemberTaskModel(BaseModel):
    """One delivery task that belongs to this release, for the panel's
    per-member-task verification-receipt rollup.

    Wire contract (pr_gate F-b7ba8602, confirmed against the consuming
    frontend, PR #1069 / branch feature/frontend/0e923eb8--54e30535--07f3373f):
    ``member_task_ids`` carries ``{task_id, pr_number}`` OBJECTS, matching the
    panel's ``ReleaseMemberTaskId`` in ``panel/src/lib/api/release.ts``
    (``task_id: string; pr_number: number | null``), which maps
    ``m.task_id`` per member into the release verification rollup. The
    ``member_task_ids`` field name is kept (not renamed to e.g.
    ``member_tasks``) because the FE is already wired to it; the object
    shape leaves room for per-member display fields (e.g. pr links)
    without another contract break.
    """

    task_id: str
    pr_number: int | None = None


class ReleaseProposalResponse(BaseModel):
    """The held proposal the CEO approves or rejects."""

    task_id: str
    title: str
    status: str
    required_changes: str | None = None
    execute_status: str | None = None
    execute_detail: str | None = None
    execute_in_flight: bool = False
    report: ReleaseReportModel
    member_task_ids: list[ReleaseMemberTaskModel] = Field(default_factory=list)


class ReleaseRejectRequest(BaseModel):
    """The CEO's required changes when rejecting a proposal."""

    required_changes: str = Field(min_length=10)


class ReleaseExecuteResponse(BaseModel):
    """The outcome of an approved release execution."""

    status: str
    version: str
    files_changed: list[str]
    commit_sha: str | None = None
    release_url: str | None = None
    detail: str


class ReleaseCertificateTaskState(BaseModel):
    """One task in the release's per-AC QA pass state.

    `criteria_total` is the task's acceptance-criterion count;
    `criteria_verified` is how many of them QA stamped
    ('[AC] <criterion> — verified: <evidence>' lines in qa_notes);
    `qa_passed` is True when every criterion is verified, False when at least
    one isn't, and None when the task carries zero acceptance criteria — it
    never went through QA at all, which is distinct from QA having passed it.
    """

    task_id: str
    title: str
    status: str
    criteria_total: int
    criteria_verified: int
    qa_passed: bool | None


class ReleaseCertificateSeverityCounts(BaseModel):
    """Finding counts in one ledger bucket, broken down by severity."""

    blocker: int = 0
    major: int = 0
    minor: int = 0
    nit: int = 0


class ReleaseCertificateFindingsSummary(BaseModel):
    """Findings-ledger summary across the release task set, by status bucket.

    `closed` covers findings whose status is `addressed` or `verified`
    (fixed and, post-review, confirmed); `open` is unaddressed work;
    `waived` is explicitly waived minor/nit findings.
    """

    open: ReleaseCertificateSeverityCounts
    closed: ReleaseCertificateSeverityCounts
    waived: ReleaseCertificateSeverityCounts


class ReleaseCertificateResponse(BaseModel):
    """The exportable governance artifact for a published release.

    Cross-cell contract: the frontend cell's panel "Download certificate"
    action consumes this shape verbatim — treat additive changes only.
    """

    version: str
    generated_at: datetime
    ci_verdict: str
    # True unless a release-window task carries an unresolved block-level
    # architectural-convention finding (the real conventions validator's own
    # persisted output — see ReleaseCertificateService._conventions_clean).
    conventions_clean: bool
    # The CEO's approval-dispatch timestamp (release.py's approve route),
    # None when the release predates that marker being recorded — distinct
    # from the proposal's completion time, which the ~40min background
    # executor stamps only once publish finishes.
    ceo_approved_at: datetime | None
    changelog_excerpt: str
    task_states: list[ReleaseCertificateTaskState]
    findings_summary: ReleaseCertificateFindingsSummary


class ReleaseReviewerChainEntryModel(BaseModel):
    """One review-layer spawn session against a member task (the same entry
    ``GET /dashboard/metrics/task/{task_id}`` reports as ``reviewer_chain``)."""

    round: int
    role: str
    model: str | None
    agent_slug: str
    started_at: str


class ReleaseMemberReceiptModel(BaseModel):
    """One member task's verification receipt for the release rollup: the
    ingredients the panel's task-detail Verification tab reads from four
    per-task endpoints, in their existing wire shapes."""

    task_id: str
    title: str
    acceptance_criteria: list[str]
    qa_notes: str | None
    findings: list[TaskFindingResponse]
    conventions_findings: list[ConventionFinding]
    reviewer_chain: list[ReleaseReviewerChainEntryModel]


class ReleaseVerificationResponse(BaseModel):
    """Every member task's receipt for the open proposal, in one response."""

    proposal_task_id: str
    members: list[ReleaseMemberReceiptModel]


class ReleaseMemberCiVerdictModel(BaseModel):
    """One member task's PR CI verdict."""

    task_id: str
    ci: AttestationCiVerdictResponse


class ReleaseCiVerdictsResponse(BaseModel):
    """Every member task's PR CI verdict for the open proposal."""

    proposal_task_id: str
    verdicts: list[ReleaseMemberCiVerdictModel]


def member_receipt_to_model(receipt: "MemberReceipt") -> ReleaseMemberReceiptModel:
    """The service's frozen receipt as its response model."""
    return ReleaseMemberReceiptModel(
        task_id=str(receipt.task_id),
        title=receipt.title,
        acceptance_criteria=list(receipt.acceptance_criteria),
        qa_notes=receipt.qa_notes,
        findings=[finding_to_response(row) for row in receipt.findings],
        conventions_findings=[
            ConventionFinding(**finding) for finding in receipt.conventions_findings
        ],
        reviewer_chain=[
            ReleaseReviewerChainEntryModel(**entry.to_dict())
            for entry in receipt.reviewer_chain
        ],
    )


def member_ci_verdict_to_model(
    verdict: "MemberCiVerdict",
) -> ReleaseMemberCiVerdictModel:
    """The service's member CI verdict as its response model."""
    return ReleaseMemberCiVerdictModel(
        task_id=str(verdict.task_id),
        ci=AttestationCiVerdictResponse(
            state=verdict.ci.state,
            head_sha=verdict.ci.head_sha,
            failing_checks=list(verdict.ci.failing_checks),
        ),
    )
