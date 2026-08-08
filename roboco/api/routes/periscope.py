"""Periscope (Board Program) engine API — the CEO reads filed market briefs
and approves/dismisses individual findings. CEO-only throughout. The brief
itself is a report (read-only — the exploration task completes atomically at
propose time), but each finding carries its own per-item approve/reject,
mirroring ``roboco.api.routes.roadmap``'s shape.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from roboco.api.deps import CurrentAgentContext, DbSession
from roboco.api.schemas.periscope import (
    MarketBriefFindingActionResponse,
    MarketBriefFindingRejectRequest,
    MarketBriefResponse,
)
from roboco.api.utils.periscope import require_ceo as _require_ceo
from roboco.api.utils.periscope import to_response as _to_response
from roboco.security import guard_deco
from roboco.services.periscope_service import get_periscope_service
from roboco.services.task import get_task_service

router = APIRouter()


@router.get("/briefs", response_model=list[MarketBriefResponse])
async def list_market_briefs(
    db: DbSession, agent: CurrentAgentContext
) -> list[MarketBriefResponse]:
    """Recent filed market briefs, newest-first. A completed Periscope
    exploration without a marker (shouldn't happen — the verb always sets
    one before completing) is omitted rather than rendered blank."""
    _require_ceo(agent)
    tasks = await get_task_service(db).list_periscope_briefs()
    return [r for t in tasks if (r := _to_response(t)) is not None]


@router.post(
    "/briefs/{task_id}/findings/{finding_id}/approve",
    response_model=MarketBriefFindingActionResponse,
)
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.block_clouds()
async def approve_market_brief_finding(
    task_id: UUID,
    finding_id: str,
    db: DbSession,
    agent: CurrentAgentContext,
) -> MarketBriefFindingActionResponse:
    """Materialize one finding as a Main-PM-owned root task (idempotent)."""
    _require_ceo(agent)
    result = await get_periscope_service(db).approve_finding(
        task_id, finding_id, created_by=agent.agent_id
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such open Periscope finding",
        )
    await db.commit()
    return MarketBriefFindingActionResponse(
        status=result.status,
        finding_id=result.finding_id,
        materialized_task_id=result.materialized_task_id,
        detail=result.detail,
    )


@router.post(
    "/briefs/{task_id}/findings/{finding_id}/reject",
    response_model=MarketBriefFindingActionResponse,
)
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
async def reject_market_brief_finding(
    task_id: UUID,
    finding_id: str,
    data: MarketBriefFindingRejectRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> MarketBriefFindingActionResponse:
    """Dismiss one finding with a reason (idempotent)."""
    _require_ceo(agent)
    result = await get_periscope_service(db).reject_finding(
        task_id, finding_id, data.reason
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such open Periscope finding",
        )
    await db.commit()
    return MarketBriefFindingActionResponse(
        status=result.status,
        finding_id=result.finding_id,
        materialized_task_id=result.materialized_task_id,
        detail=result.detail,
    )
