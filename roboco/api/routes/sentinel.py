"""Sentinel (Board Program) engine API — the CEO reads filed quality reports
and approves/dismisses individual drift items. CEO-only throughout. The
report itself is read-only (the exploration task completes atomically at
propose time), but each item carries its own per-item approve/reject,
mirroring ``roboco.api.routes.periscope``'s shape.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from roboco.api.deps import CurrentAgentContext, DbSession
from roboco.api.schemas.sentinel import (
    QualityReportItemActionResponse,
    QualityReportItemRejectRequest,
    QualityReportResponse,
)
from roboco.api.utils.sentinel import _require_ceo, _to_response
from roboco.security import guard_deco
from roboco.services.sentinel_service import get_sentinel_service
from roboco.services.task import get_task_service

router = APIRouter()


@router.get("/reports", response_model=list[QualityReportResponse])
async def list_quality_reports(
    db: DbSession, agent: CurrentAgentContext
) -> list[QualityReportResponse]:
    """Recent filed quality reports, newest-first. A completed Sentinel
    exploration without a marker (shouldn't happen — the verb always sets
    one before completing) is omitted rather than rendered blank."""
    _require_ceo(agent)
    tasks = await get_task_service(db).list_sentinel_reports()
    return [r for t in tasks if (r := _to_response(t)) is not None]


@router.post(
    "/reports/{task_id}/items/{item_id}/approve",
    response_model=QualityReportItemActionResponse,
)
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.block_clouds()
async def approve_quality_report_item(
    task_id: UUID,
    item_id: str,
    db: DbSession,
    agent: CurrentAgentContext,
) -> QualityReportItemActionResponse:
    """Materialize one drift item as a Main-PM-owned root task (idempotent)."""
    _require_ceo(agent)
    result = await get_sentinel_service(db).approve_item(
        task_id, item_id, created_by=agent.agent_id
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such open Sentinel item",
        )
    await db.commit()
    return QualityReportItemActionResponse(
        status=result.status,
        item_id=result.item_id,
        materialized_task_id=result.materialized_task_id,
        detail=result.detail,
    )


@router.post(
    "/reports/{task_id}/items/{item_id}/reject",
    response_model=QualityReportItemActionResponse,
)
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
async def reject_quality_report_item(
    task_id: UUID,
    item_id: str,
    data: QualityReportItemRejectRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> QualityReportItemActionResponse:
    """Dismiss one drift item with a reason (idempotent)."""
    _require_ceo(agent)
    result = await get_sentinel_service(db).reject_item(task_id, item_id, data.reason)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such open Sentinel item",
        )
    await db.commit()
    return QualityReportItemActionResponse(
        status=result.status,
        item_id=result.item_id,
        materialized_task_id=result.materialized_task_id,
        detail=result.detail,
    )
