"""Operator maintenance pause API: CEO-only read/set/clear per scope.

Lets the CEO drain new agent spawns / autonomous origination (dev/delivery
dispatch, Board Programs, the originating engines) without stopping the
stack. See ``roboco.services.maintenance_pause`` for the mechanism and
``roboco.runtime.orchestrator``'s gate call sites for where each scope is
actually consulted. Mirrors ``roboco/api/routes/board_programs.py``'s
CEO-gating shape.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from roboco.api.deps import CurrentAgentContext, DbSession, require_ceo_role
from roboco.foundation.policy.maintenance_pause import (
    DEFAULT_PAUSE_HOURS,
    MAX_PAUSE_HOURS,
    PauseScope,
    PauseState,
)
from roboco.security import guard_deco
from roboco.services.maintenance_pause import (
    MaintenancePauseError,
    get_maintenance_pause_service,
    lookup_degraded_at,
)

router = APIRouter()


def _require_ceo(agent: CurrentAgentContext) -> None:
    require_ceo_role(agent.role, action="view or change the maintenance pause")


class MaintenancePauseResponse(BaseModel):
    """One scope's live pause status, the shape the panel's maintenance-pause
    card reads for all three scopes."""

    scope: str
    paused: bool
    paused_by: str | None = None
    paused_at: str | None = None
    reason: str | None = None
    expires_at: str | None = None
    # Set when this scope's runtime gate (``is_paused``, consulted by the
    # orchestrator/engines) is CURRENTLY reading fail-closed on a lookup
    # error -- distinct from ``paused``, which reflects the persisted state
    # this same request just read successfully. A scope can show
    # ``paused=False`` and a non-null ``read_degraded_since`` at once: the
    # stored setting genuinely says not-paused, but the runtime gate is
    # treating it as paused until its own next lookup succeeds.
    read_degraded_since: str | None = None


class MaintenancePauseRequest(BaseModel):
    """Body for ``POST /maintenance-pause/{scope}``."""

    reason: str | None = Field(default=None, max_length=500)
    hours: float = Field(default=DEFAULT_PAUSE_HOURS, gt=0, le=MAX_PAUSE_HOURS)


def _to_response(
    state: PauseState, *, read_degraded_since: datetime | None = None
) -> MaintenancePauseResponse:
    return MaintenancePauseResponse(
        scope=state.scope.value,
        paused=state.paused,
        paused_by=state.paused_by,
        paused_at=state.paused_at.isoformat() if state.paused_at else None,
        reason=state.reason,
        expires_at=state.expires_at.isoformat() if state.expires_at else None,
        read_degraded_since=(
            read_degraded_since.isoformat() if read_degraded_since else None
        ),
    )


@router.get("", response_model=list[MaintenancePauseResponse])
async def list_maintenance_pauses(
    db: DbSession, agent: CurrentAgentContext
) -> list[MaintenancePauseResponse]:
    """Every scope's live pause status, plus whether its runtime gate is
    CURRENTLY reading fail-closed (see ``read_degraded_since`` above) --
    independent of this call's own read, which can succeed even while the
    orchestrator's is_paused() calls for the same scope are failing."""
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
