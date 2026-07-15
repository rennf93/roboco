"""Schemas for the gated release-manager CEO surface."""

from __future__ import annotations

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
