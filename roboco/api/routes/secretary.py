"""Secretary API — the CEO's chief-of-staff surface.

The Secretary agent submits directives here on the CEO's command; gated ones are
queued and the CEO confirms/rejects them (CEO-only routes). The Secretary also
reads company state. Writes commit explicitly.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from roboco.api.deps import CurrentAgentContext, DbSession
from roboco.api.schemas.secretary import (
    CompanyStateResponse,
    DirectiveDecision,
    DirectiveResponse,
    DirectiveSubmit,
)
from roboco.models import AgentRole
from roboco.models.secretary import DirectiveKind, DirectiveStatus
from roboco.services.base import ConflictError, NotFoundError, ValidationError
from roboco.services.secretary import get_secretary_service

router = APIRouter()

_SECRETARY_OR_CEO = frozenset({AgentRole.SECRETARY, AgentRole.CEO})


def _require(agent: CurrentAgentContext, allowed: frozenset[AgentRole]) -> None:
    if agent.role not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"role '{agent.role}' not permitted on the Secretary surface",
        )


@router.get("/state", response_model=CompanyStateResponse)
async def read_state(db: DbSession, agent: CurrentAgentContext) -> CompanyStateResponse:
    """Compact company-state snapshot (Secretary or CEO)."""
    _require(agent, _SECRETARY_OR_CEO)
    state = await get_secretary_service(db).read_company_state()
    return CompanyStateResponse(**state)


@router.get("/tasks/{task_id}")
async def read_task(
    task_id: UUID, db: DbSession, agent: CurrentAgentContext
) -> dict[str, object]:
    """Read one task's detail (Secretary or CEO)."""
    _require(agent, _SECRETARY_OR_CEO)
    try:
        return await get_secretary_service(db).read_task(task_id)
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=exc.message
        ) from exc


@router.post(
    "/directives", response_model=DirectiveResponse, status_code=status.HTTP_201_CREATED
)
async def submit_directive(
    data: DirectiveSubmit, db: DbSession, agent: CurrentAgentContext
) -> DirectiveResponse:
    """Submit a directive (Secretary or CEO). Gated kinds queue; others run."""
    _require(agent, _SECRETARY_OR_CEO)
    try:
        kind = DirectiveKind(data.kind)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"unknown directive kind '{data.kind}'",
        ) from exc
    service = get_secretary_service(db)
    try:
        row = await service.submit_directive(kind, data.payload, agent.agent_id)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=exc.message
        ) from exc
    await db.commit()
    return DirectiveResponse(**service.to_dict(row))


@router.get("/directives", response_model=list[DirectiveResponse])
async def list_directives(
    db: DbSession, agent: CurrentAgentContext, status_filter: str | None = None
) -> list[DirectiveResponse]:
    """List directives (CEO only); optional status filter."""
    _require(agent, frozenset({AgentRole.CEO}))
    parsed: DirectiveStatus | None = None
    if status_filter:
        try:
            parsed = DirectiveStatus(status_filter)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"unknown status '{status_filter}'",
            ) from exc
    service = get_secretary_service(db)
    rows = await service.list_directives(parsed)
    return [DirectiveResponse(**service.to_dict(r)) for r in rows]


@router.post("/directives/{directive_id}/confirm", response_model=DirectiveResponse)
async def confirm_directive(
    directive_id: UUID, db: DbSession, agent: CurrentAgentContext
) -> DirectiveResponse:
    """CEO confirms a pending directive — it executes with CEO authority."""
    _require(agent, frozenset({AgentRole.CEO}))
    service = get_secretary_service(db)
    try:
        row = await service.confirm_directive(directive_id, agent.agent_id)
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=exc.message
        ) from exc
    except ConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=exc.message
        ) from exc
    await db.commit()
    return DirectiveResponse(**service.to_dict(row))


@router.post("/directives/{directive_id}/reject", response_model=DirectiveResponse)
async def reject_directive(
    directive_id: UUID,
    data: DirectiveDecision,
    db: DbSession,
    agent: CurrentAgentContext,
) -> DirectiveResponse:
    """CEO rejects a pending directive."""
    _require(agent, frozenset({AgentRole.CEO}))
    service = get_secretary_service(db)
    try:
        row = await service.reject_directive(directive_id, agent.agent_id, data.reason)
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=exc.message
        ) from exc
    except ConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=exc.message
        ) from exc
    await db.commit()
    return DirectiveResponse(**service.to_dict(row))
