"""Schemas for the per-task verification attestation.

Mirrors ``roboco.services.attestation``'s dataclasses field-for-field — the
service assembles the data, the (future) route converts each dataclass into
its response-schema twin here, so the assembler's return shape is the real
contract rather than something the route re-derives.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class AttestationCriterionResponse(BaseModel):
    """One acceptance criterion plus its verification stamp, if any."""

    id: str | None = None
    text: str
    verified: bool
    evidence: str | None = None


class AttestationFindingResponse(BaseModel):
    """One row of the revision-findings ledger (``task_review_findings``)."""

    id: str
    round: int
    origin: str
    severity: str
    status: str
    file: str | None = None
    line: int | None = None
    criterion: str | None = None
    expected: str
    actual: str
    fix: str | None = None
    resolution_note: str | None = None
    created_at: datetime


class AttestationFindingsRoundResponse(BaseModel):
    """Every ledger row raised in one revision round."""

    round: int
    findings: list[AttestationFindingResponse] = Field(default_factory=list)


class AttestationCiVerdictResponse(BaseModel):
    """The CI verdict for the PR's head commit, or ``not_available`` when no
    PR/CI signal could be resolved."""

    state: str
    head_sha: str | None = None
    failing_checks: list[str] = Field(default_factory=list)


class AttestationConventionFindingResponse(BaseModel):
    """One recorded architectural-conventions finding for this task's diff."""

    file: str
    line: int
    rule: str
    level: str
    kind: str | None = None
    message: str


class AttestationReviewerChainEntryResponse(BaseModel):
    """One status-transition hop in the task's custody chain."""

    to_status: str
    agent_slug: str | None = None
    agent_role: str | None = None
    timestamp: datetime


class AttestationWorkSessionResponse(BaseModel):
    """One work session bound to this task — the commit/PR refs an outside
    auditor can verify against the real git history."""

    agent_slug: str | None = None
    branch_name: str
    base_branch: str
    target_branch: str
    status: str
    commits: list[str] = Field(default_factory=list)
    pr_number: int | None = None
    pr_url: str | None = None
    pr_status: str | None = None
    started_at: datetime
    ended_at: datetime | None = None


class TaskAttestationResponse(BaseModel):
    """The full per-task verification attestation — everything an outside
    auditor needs to confirm a task's acceptance criteria, review history,
    and CI/conventions state without the live panel."""

    task_id: str
    title: str
    status: str
    team: str
    project_slug: str | None = None
    branch_name: str | None = None
    pr_number: int | None = None
    pr_url: str | None = None
    revision_count: int
    commits: list[dict[str, Any]] = Field(default_factory=list)
    work_sessions: list[AttestationWorkSessionResponse] = Field(default_factory=list)
    acceptance_criteria: list[AttestationCriterionResponse] = Field(
        default_factory=list
    )
    findings_by_round: list[AttestationFindingsRoundResponse] = Field(
        default_factory=list
    )
    ci: AttestationCiVerdictResponse
    conventions_findings: list[AttestationConventionFindingResponse] = Field(
        default_factory=list
    )
    reviewer_chain: list[AttestationReviewerChainEntryResponse] = Field(
        default_factory=list
    )
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TaskAttestationQuery(BaseModel):
    """Query params for the (future) attestation route: the task id plus an
    optional output ``format`` — ``json`` (the default, this response
    schema) or ``markdown`` (leaf 2's rendered report)."""

    task_id: str
    format: Literal["json", "markdown"] = "json"
