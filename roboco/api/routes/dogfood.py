"""Dogfood (Board Program) engine API — the CEO approves/rejects items
within a held friction-fix cycle. CEO-only throughout. Approving an item
materializes it as a BACKLOG task; nothing here starts it — normal PM
activation takes it from there. Mirrors ``roboco.api.routes.spackle``.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from roboco.api.deps import CurrentAgentContext, DbSession
from roboco.api.schemas.dogfood import (
    DogfoodCycleResponse,
    FrictionFixItemActionResponse,
    FrictionFixRejectRequest,
)
from roboco.api.utils.dogfood import _require_ceo, _to_response
from roboco.foundation.policy.content import markers
from roboco.security import guard_deco
from roboco.services.dogfood_service import get_dogfood_service

router = APIRouter()


@router.get("/cycles", response_model=list[DogfoodCycleResponse])
async def list_dogfood_cycles(
    db: DbSession, agent: CurrentAgentContext
) -> list[DogfoodCycleResponse]:
    """Every open dogfood cycle already authored by the Product Owner.

    A cycle the PO hasn't authored yet (no items drafted) is omitted — there
    is nothing for the CEO to review until ``propose_friction_fixes`` lands.
    """
    _require_ceo(agent)
    tasks = await get_dogfood_service(db).list_open_cycles()
    return [_to_response(t) for t in tasks if markers.get_friction_fixes(t)]


@router.post(
    "/cycles/{task_id}/items/{item_id}/approve",
    response_model=FrictionFixItemActionResponse,
)
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.block_clouds()
async def approve_friction_fix_item(
    task_id: UUID,
    item_id: str,
    db: DbSession,
    agent: CurrentAgentContext,
) -> FrictionFixItemActionResponse:
    """Materialize one proposed item as a BACKLOG task (idempotent)."""
    _require_ceo(agent)
    result = await get_dogfood_service(db).approve_item(
        task_id, item_id, created_by=agent.agent_id
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such open dogfood item",
        )
    await db.commit()
    return FrictionFixItemActionResponse(
        status=result.status,
        item_id=result.item_id,
        materialized_task_id=result.materialized_task_id,
        detail=result.detail,
    )


@router.post(
    "/cycles/{task_id}/items/{item_id}/reject",
    response_model=FrictionFixItemActionResponse,
)
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
async def reject_friction_fix_item(
    task_id: UUID,
    item_id: str,
    data: FrictionFixRejectRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> FrictionFixItemActionResponse:
    """Reject one proposed item with a reason (idempotent)."""
    _require_ceo(agent)
    result = await get_dogfood_service(db).reject_item(task_id, item_id, data.reason)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such open dogfood item",
        )
    await db.commit()
    return FrictionFixItemActionResponse(
        status=result.status,
        item_id=result.item_id,
        materialized_task_id=result.materialized_task_id,
        detail=result.detail,
    )
