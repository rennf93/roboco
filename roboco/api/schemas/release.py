"""Schemas for the gated release-manager CEO surface.

Runtime annotations (no ``from __future__ import annotations``): the
certificate response's ``datetime`` fields must be resolvable when Pydantic
builds the model, mirroring ``schemas/x.py``.
"""

from datetime import datetime

from pydantic import BaseModel, Field


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
    `qa_passed` is True when every criterion is verified (tasks with no
    acceptance criteria count as passed).
    """

    task_id: str
    title: str
    status: str
    criteria_total: int
    criteria_verified: int
    qa_passed: bool


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
    conventions_clean: bool
    ceo_approved_at: datetime | None
    changelog_excerpt: str
    task_states: list[ReleaseCertificateTaskState]
    findings_summary: ReleaseCertificateFindingsSummary
