"""Schemas for the Sentinel (Board Program) engine's CEO-facing read surface.
Mirrors ``roboco.api.schemas.periscope`` — a report has no per-item
approve/reject, so this is list-only."""

from __future__ import annotations

from pydantic import BaseModel


class QualityReportItemResponse(BaseModel):
    """One drift item within a Sentinel quality report."""

    id: str
    area: str
    observation: str
    evidence: str
    suggested_action: str


class QualityReportResponse(BaseModel):
    """One completed Sentinel exploration's filed report."""

    task_id: str
    title: str
    completed_at: str | None
    headline: str
    items: list[QualityReportItemResponse]
    overall_assessment: str
