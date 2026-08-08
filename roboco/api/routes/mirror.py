"""Mirror (Board Program) engine API — the CEO approves/rejects items
within a held messaging-fixes cycle. CEO-only throughout. Approving an item
materializes it as a BACKLOG docs task; nothing here starts it — normal PM
activation takes it from there. Mirrors ``roboco.api.routes.spackle``.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from roboco.api.deps import CurrentAgentContext, DbSession
from roboco.api.schemas.mirror import (
    MessagingFixItemActionResponse,
    MessagingFixRejectRequest,
    MirrorCycleResponse,
)
from roboco.api.utils.mirror import require_ceo as _require_ceo
from roboco.api.utils.mirror import to_response as _to_response
from roboco.foundation.policy.content import markers
from roboco.security import guard_deco
from roboco.services.mirror_service import get_mirror_service

router = APIRouter()


@router.get("/cycles", response_model=list[MirrorCycleResponse])
async def list_mirror_cycles(
    db: DbSession, agent: CurrentAgentContext
) -> list[MirrorCycleResponse]:
    """Every open mirror cycle already authored by the Head of Marketing.

    A cycle the HoM hasn't authored yet (no items drafted) is omitted — there
    is nothing for the CEO to review until ``propose_messaging_fixes`` lands.
    """
    _require_ceo(agent)
    tasks = await get_mirror_service(db).list_open_cycles()
    return [_to_response(t) for t in tasks if markers.get_messaging_fixes(t)]


@router.post(
    "/cycles/{task_id}/items/{item_id}/approve",
    response_model=MessagingFixItemActionResponse,
)
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.block_clouds()
async def approve_messaging_fix_item(
    task_id: UUID,
    item_id: str,
    db: DbSession,
    agent: CurrentAgentContext,
) -> MessagingFixItemActionResponse:
    """Materialize one proposed item as a BACKLOG docs task (idempotent)."""
    _require_ceo(agent)
    result = await get_mirror_service(db).approve_item(
        task_id, item_id, created_by=agent.agent_id
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such open mirror item",
        )
    await db.commit()
    return MessagingFixItemActionResponse(
        status=result.status,
        item_id=result.item_id,
        materialized_task_id=result.materialized_task_id,
        detail=result.detail,
    )


@router.post(
    "/cycles/{task_id}/items/{item_id}/reject",
    response_model=MessagingFixItemActionResponse,
)
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
async def reject_messaging_fix_item(
    task_id: UUID,
    item_id: str,
    data: MessagingFixRejectRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> MessagingFixItemActionResponse:
    """Reject one proposed item with a reason (idempotent)."""
    _require_ceo(agent)
    result = await get_mirror_service(db).reject_item(task_id, item_id, data.reason)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such open mirror item",
        )
    await db.commit()
    return MessagingFixItemActionResponse(
        status=result.status,
        item_id=result.item_id,
        materialized_task_id=result.materialized_task_id,
        detail=result.detail,
    )
