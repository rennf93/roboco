"""Board roadmap engine API — the CEO approves/rejects items within a held
roadmap cycle. CEO-only throughout. Approving an item materializes it as a
BACKLOG task; nothing here starts it — normal PM activation takes it from
there.
"""

from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from roboco.api.deps import CurrentAgentContext, DbSession, require_ceo_role
from roboco.api.schemas.roadmap import (
    RoadmapCycleResponse,
    RoadmapItemActionResponse,
    RoadmapItemResponse,
    RoadmapRejectRequest,
)
from roboco.foundation.policy.content import markers
from roboco.security import guard_deco
from roboco.services.roadmap_service import get_roadmap_service

if TYPE_CHECKING:
    from roboco.db.tables import TaskTable

router = APIRouter()


def _require_ceo(agent: CurrentAgentContext) -> None:
    require_ceo_role(agent.role, action="view or act on the roadmap queue")


def _status_value(task: "TaskTable") -> str:
    raw = task.status
    return raw.value if hasattr(raw, "value") else str(raw)


def _to_response(task: "TaskTable") -> RoadmapCycleResponse:
    payload = markers.get_roadmap_cycle(task) or {}
    items = [RoadmapItemResponse(**item) for item in payload.get("items", [])]
    return RoadmapCycleResponse(
        task_id=str(task.id),
        title=task.title,
        status=_status_value(task),
        goal=str(payload.get("goal") or ""),
        items=items,
    )


@router.get("/cycles", response_model=list[RoadmapCycleResponse])
async def list_roadmap_cycles(
    db: DbSession, agent: CurrentAgentContext
) -> list[RoadmapCycleResponse]:
    """Every open roadmap cycle already authored by the Product Owner.

    A cycle the PO hasn't authored yet (no items drafted) is omitted — there
    is nothing for the CEO to review until ``propose_roadmap`` lands.
    """
    _require_ceo(agent)
    tasks = await get_roadmap_service(db).list_open_cycles()
    return [_to_response(t) for t in tasks if markers.get_roadmap_cycle(t)]


@router.post(
    "/cycles/{task_id}/items/{item_id}/approve",
    response_model=RoadmapItemActionResponse,
)
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.block_clouds()
async def approve_roadmap_item(
    task_id: UUID,
    item_id: str,
    db: DbSession,
    agent: CurrentAgentContext,
) -> RoadmapItemActionResponse:
    """Materialize one proposed item as a BACKLOG task (idempotent)."""
    _require_ceo(agent)
    result = await get_roadmap_service(db).approve_item(
        task_id, item_id, created_by=agent.agent_id
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such open roadmap item",
        )
    await db.commit()
    return RoadmapItemActionResponse(
        status=result.status,
        item_id=result.item_id,
        materialized_task_id=result.materialized_task_id,
        detail=result.detail,
    )


@router.post(
    "/cycles/{task_id}/items/{item_id}/reject",
    response_model=RoadmapItemActionResponse,
)
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
async def reject_roadmap_item(
    task_id: UUID,
    item_id: str,
    data: RoadmapRejectRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> RoadmapItemActionResponse:
    """Reject one proposed item with a reason (idempotent)."""
    _require_ceo(agent)
    result = await get_roadmap_service(db).reject_item(task_id, item_id, data.reason)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such open roadmap item",
        )
    await db.commit()
    return RoadmapItemActionResponse(
        status=result.status,
        item_id=result.item_id,
        materialized_task_id=result.materialized_task_id,
        detail=result.detail,
    )
