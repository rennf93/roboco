"""Scales (Board Program) engine API — the CEO approves/rejects items within
a held portfolio-rebalance cycle. CEO-only throughout. Approving an item
EXECUTES it against the live target task (reprioritize its ``priority`` or
cancel it) — nothing here creates a task. Mirrors
``roboco.api.routes.pest_control``.
"""

from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from roboco.api.deps import CurrentAgentContext, DbSession, require_ceo_role
from roboco.api.schemas.scales import (
    RebalanceCycleResponse,
    RebalanceItemActionResponse,
    RebalanceItemResponse,
    RebalanceRejectRequest,
)
from roboco.foundation.policy.content import markers
from roboco.security import guard_deco
from roboco.services.scales_service import get_scales_service

if TYPE_CHECKING:
    from roboco.db.tables import TaskTable

router = APIRouter()


def _require_ceo(agent: CurrentAgentContext) -> None:
    require_ceo_role(agent.role, action="view or act on the Scales queue")


def _status_value(task: "TaskTable") -> str:
    raw = task.status
    return raw.value if hasattr(raw, "value") else str(raw)


def _to_response(task: "TaskTable") -> RebalanceCycleResponse:
    payload = markers.get_rebalance_plan(task) or {}
    items = [RebalanceItemResponse(**item) for item in payload.get("items", [])]
    return RebalanceCycleResponse(
        task_id=str(task.id),
        title=task.title,
        status=_status_value(task),
        items=items,
    )


@router.get("/cycles", response_model=list[RebalanceCycleResponse])
async def list_scales_cycles(
    db: DbSession, agent: CurrentAgentContext
) -> list[RebalanceCycleResponse]:
    """Every open Scales cycle already authored by the Product Owner.

    A cycle the PO hasn't authored yet (no items drafted) is omitted — there
    is nothing for the CEO to review until ``propose_rebalance`` lands.
    """
    _require_ceo(agent)
    tasks = await get_scales_service(db).list_open_cycles()
    return [_to_response(t) for t in tasks if markers.get_rebalance_plan(t)]


@router.post(
    "/cycles/{task_id}/items/{item_id}/approve",
    response_model=RebalanceItemActionResponse,
)
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.block_clouds()
async def approve_rebalance_item(
    task_id: UUID,
    item_id: str,
    db: DbSession,
    agent: CurrentAgentContext,
) -> RebalanceItemActionResponse:
    """Execute one proposed item against its live target task (idempotent)."""
    _require_ceo(agent)
    result = await get_scales_service(db).approve_item(
        task_id, item_id, created_by=agent.agent_id
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such open Scales item",
        )
    await db.commit()
    return RebalanceItemActionResponse(
        status=result.status,
        item_id=result.item_id,
        executed_detail=result.executed_detail,
        detail=result.detail,
    )


@router.post(
    "/cycles/{task_id}/items/{item_id}/reject",
    response_model=RebalanceItemActionResponse,
)
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
async def reject_rebalance_item(
    task_id: UUID,
    item_id: str,
    data: RebalanceRejectRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> RebalanceItemActionResponse:
    """Reject one proposed item with a reason (idempotent)."""
    _require_ceo(agent)
    result = await get_scales_service(db).reject_item(task_id, item_id, data.reason)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such open Scales item",
        )
    await db.commit()
    return RebalanceItemActionResponse(
        status=result.status,
        item_id=result.item_id,
        executed_detail=result.executed_detail,
        detail=result.detail,
    )
