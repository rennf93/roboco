"""Schemas for the Sentinel (Board Program) engine's CEO-facing surface.

Mirrors ``roboco.api.schemas.periscope`` exactly: the report itself is
read-only (the exploration task completes atomically at propose time), but
each drift ITEM still carries its own proposed/approved/rejected status the
CEO decides on afterward."""

from __future__ import annotations

from pydantic import BaseModel, Field


class QualityReportItemResponse(BaseModel):
    """One drift item within a Sentinel quality report."""

    id: str
    area: str
    observation: str
    evidence: str
    suggested_action: str
    # Defaults cover an item authored before this feature shipped, whose
    # stored marker carries none of these three keys.
    status: str = "proposed"
    reject_reason: str | None = None
    materialized_task_id: str | None = None


class QualityReportResponse(BaseModel):
    """One completed Sentinel exploration's filed report."""

    task_id: str
    title: str
    completed_at: str | None
    headline: str
    items: list[QualityReportItemResponse]
    overall_assessment: str


class QualityReportItemRejectRequest(BaseModel):
    """The CEO's reason for dismissing one quality-report item."""

    reason: str = Field(..., min_length=4)


class QualityReportItemActionResponse(BaseModel):
    """The outcome of an approve/reject call on one quality-report item."""

    status: str
    item_id: str
    materialized_task_id: str | None = None
    detail: str
