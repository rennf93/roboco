"""Operator maintenance pause API: CEO-only read/set/clear per scope.

Lets the CEO drain new agent spawns / autonomous origination (dev/delivery
dispatch, Board Programs, the originating engines) without stopping the
stack. See ``roboco.services.maintenance_pause`` for the mechanism and
``roboco.runtime.orchestrator``'s gate call sites for where each scope is
actually consulted. Mirrors ``roboco/api/routes/board_programs.py``'s
CEO-gating shape.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from roboco.api.deps import CurrentAgentContext, DbSession
from roboco.api.schemas.maintenance_pause import (
    MaintenancePauseRequest,
    MaintenancePauseResponse,
)
from roboco.api.utils.maintenance_pause import require_ceo as _require_ceo
from roboco.api.utils.maintenance_pause import to_response as _to_response
from roboco.foundation.policy.maintenance_pause import PauseScope
from roboco.security import guard_deco
from roboco.services.maintenance_pause import (
    MaintenancePauseError,
    get_maintenance_pause_service,
    lookup_degraded_at,
)

router = APIRouter()


@router.get("", response_model=list[MaintenancePauseResponse])
async def list_maintenance_pauses(
    db: DbSession, agent: CurrentAgentContext
) -> list[MaintenancePauseResponse]:
    """Every scope's live pause status, plus whether its runtime gate is
    CURRENTLY reading fail-closed (see
    ``MaintenancePauseResponse.read_degraded_since``) -- independent of this
    call's own read, which can succeed even while the orchestrator's
    is_paused() calls for the same scope are failing."""
    _require_ceo(agent)
    states = await get_maintenance_pause_service(db).all()
    return [
        _to_response(states[scope], read_degraded_since=lookup_degraded_at(scope))
        for scope in PauseScope
    ]


@router.post("/{scope}", response_model=MaintenancePauseResponse)
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
async def pause_scope(
    scope: PauseScope,
    data: MaintenancePauseRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> MaintenancePauseResponse:
    """Pause ``scope`` for ``hours`` (default 4), stamping who/when/why."""
    _require_ceo(agent)
    svc = get_maintenance_pause_service(db)
    try:
        state = await svc.pause(
            scope, by=agent.slug or "ceo", reason=data.reason, hours=data.hours
        )
    except MaintenancePauseError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    # Write route commits explicitly (get_db auto-commit is unreliable).
    await db.commit()
    return _to_response(state, read_degraded_since=lookup_degraded_at(scope))


@router.delete("/{scope}", response_model=MaintenancePauseResponse)
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.block_clouds()
async def resume_scope(
    scope: PauseScope, db: DbSession, agent: CurrentAgentContext
) -> MaintenancePauseResponse:
    """Resume ``scope``: explicit and idempotent (a no-op when already clear)."""
    _require_ceo(agent)
    state = await get_maintenance_pause_service(db).resume(scope)
    await db.commit()
    return _to_response(state, read_degraded_since=lookup_degraded_at(scope))
