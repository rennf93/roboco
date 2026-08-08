"""Coroner (Board Program) engine API — the CEO reads filed postmortems and
approves/dismisses each one's process change.

A postmortem completes atomically at ``propose_postmortem`` time — the
EXPLORATION TASK has no per-item decision to wait on — but its single
process change still carries its own proposed/approved/rejected status the
CEO decides on afterward (unless kind="playbook", already routed into the
playbook queue). Unlike Periscope/Sentinel there is no item id: a postmortem
is one process change, not a list, so the action routes key on the task id
alone. CEO-only, mirroring every other Board Program surface.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from roboco.api.deps import CurrentAgentContext, DbSession
from roboco.api.schemas.coroner import (
    PostmortemResponse,
    ProcessChangeActionResponse,
    ProcessChangeRejectRequest,
)
from roboco.api.utils.coroner import _require_ceo, _to_response
from roboco.security import guard_deco
from roboco.services.coroner_service import get_coroner_service
from roboco.services.task import get_task_service

router = APIRouter()


@router.get("/postmortems", response_model=list[PostmortemResponse])
async def list_postmortems(
    db: DbSession, agent: CurrentAgentContext
) -> list[PostmortemResponse]:
    """Every completed Coroner postmortem, newest first."""
    _require_ceo(agent)
    tasks = await get_task_service(db).list_completed_coroner_postmortems()
    return [_to_response(t) for t in tasks]


@router.post(
    "/postmortems/{task_id}/process-change/approve",
    response_model=ProcessChangeActionResponse,
)
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.block_clouds()
async def approve_process_change(
    task_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
) -> ProcessChangeActionResponse:
    """Materialize the postmortem's process change as a Main-PM-owned root
    task (idempotent)."""
    _require_ceo(agent)
    result = await get_coroner_service(db).approve_process_change(
        task_id, created_by=agent.agent_id
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such Coroner postmortem",
        )
    await db.commit()
    return ProcessChangeActionResponse(
        status=result.status,
        materialized_task_id=result.materialized_task_id,
        detail=result.detail,
    )


@router.post(
    "/postmortems/{task_id}/process-change/reject",
    response_model=ProcessChangeActionResponse,
)
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
async def reject_process_change(
    task_id: UUID,
    data: ProcessChangeRejectRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> ProcessChangeActionResponse:
    """Dismiss the postmortem's process change with a reason (idempotent)."""
    _require_ceo(agent)
    result = await get_coroner_service(db).reject_process_change(task_id, data.reason)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such Coroner postmortem",
        )
    await db.commit()
    return ProcessChangeActionResponse(
        status=result.status,
        materialized_task_id=result.materialized_task_id,
        detail=result.detail,
    )
