"""Schemas for the Sentinel (Board Program) engine's CEO-facing surface.

Mirrors ``roboco.api.schemas.periscope`` exactly: the report itself is
read-only (the exploration task completes atomically at propose time), but
each drift ITEM still carries its own proposed/approved/rejected status the
CEO decides on afterward."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from roboco.foundation.policy.content import markers

if TYPE_CHECKING:
    from roboco.db.tables import TaskTable


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


def task_to_quality_report_response(task: TaskTable) -> QualityReportResponse | None:
    payload = markers.get_quality_report(task)
    if payload is None:
        return None
    items = [QualityReportItemResponse(**i) for i in payload.get("items", [])]
    return QualityReportResponse(
        task_id=str(task.id),
        title=task.title,
        completed_at=task.updated_at.isoformat() if task.updated_at else None,
        headline=payload.get("headline", ""),
        items=items,
        overall_assessment=payload.get("overall_assessment", ""),
    )
