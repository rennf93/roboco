"""Pitch API — Board proposals + the CEO approve -> auto-provision flow.

A pitch is an additive origination path: the Board proposes a product, the CEO
approves, and the system provisions repos + Projects (+ a Product when
multi-cell) and seeds a Main-PM delivery task. Nothing in the existing delivery
lifecycle changes; with provisioning unconfigured, approval is rejected with a
clear message and no side effects occur. Writes commit explicitly.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from roboco.api.deps import CurrentAgentContext, DbSession
from roboco.api.schemas.pitch import PitchCreateRequest, PitchDecision, PitchResponse
from roboco.api.utils.pitch import _parse_cells, _to_http_exc, _to_response
from roboco.models import AgentRole
from roboco.models.pitch import PitchCreate, PitchStatus
from roboco.security import guard_deco
from roboco.services.base import ConflictError, NotFoundError, ValidationError
from roboco.services.github_provisioning import ProvisioningError
from roboco.services.pitch import get_pitch_service

router = APIRouter()

_BOARD_ROLES = frozenset({AgentRole.PRODUCT_OWNER, AgentRole.HEAD_MARKETING})
_VIEW_ROLES = frozenset(
    {
        AgentRole.PRODUCT_OWNER,
        AgentRole.HEAD_MARKETING,
        AgentRole.MAIN_PM,
        AgentRole.CEO,
        AgentRole.AUDITOR,
    }
)


@router.post("", response_model=PitchResponse, status_code=status.HTTP_201_CREATED)
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.content_type_filter(["application/json"])
async def create_pitch(
    data: PitchCreateRequest, db: DbSession, agent: CurrentAgentContext
) -> PitchResponse:
    """Board (Product Owner / Head of Marketing) authors a pitch."""
    if agent.role not in _BOARD_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the Board (PO / Head of Marketing) can author pitches.",
        )
    create = PitchCreate(
        title=data.title,
        slug=data.slug,
        problem=data.problem,
        proposed_solution=data.proposed_solution,
        target_cells=_parse_cells(data.target_cells),
    )
    service = get_pitch_service(db)
    try:
        pitch = await service.create(create, created_by=agent.agent_id)
    except ConflictError as exc:
        raise _to_http_exc(exc) from exc
    await db.commit()
    return _to_response(pitch)


@router.get("", response_model=list[PitchResponse])
async def list_pitches(
    db: DbSession, agent: CurrentAgentContext, status_filter: str | None = None
) -> list[PitchResponse]:
    """List pitches (Board, Main PM, CEO, Auditor); optional status filter."""
    if agent.role not in _VIEW_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="not permitted to view pitches",
        )
    parsed: PitchStatus | None = None
    if status_filter:
        try:
            parsed = PitchStatus(status_filter)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"unknown pitch status '{status_filter}'",
            ) from exc
    pitches = await get_pitch_service(db).list_pitches(parsed)
    return [_to_response(p) for p in pitches]


@router.get("/{pitch_id}", response_model=PitchResponse)
async def get_pitch(
    pitch_id: UUID, db: DbSession, agent: CurrentAgentContext
) -> PitchResponse:
    """Fetch one pitch."""
    if agent.role not in _VIEW_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="not permitted to view pitches",
        )
    pitch = await get_pitch_service(db).get(pitch_id)
    if pitch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="pitch not found"
        )
    return _to_response(pitch)


@router.post("/{pitch_id}/approve", response_model=PitchResponse)
@guard_deco.rate_limit(requests=10, window=60)
@guard_deco.max_request_size(size_bytes=8192)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.block_clouds()
@guard_deco.usage_monitor(max_calls=30, window=3600, action="log")
async def approve_pitch(
    pitch_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
    data: PitchDecision | None = None,
) -> PitchResponse:
    """CEO approves a pitch -> provision repos/Projects (+Product) + seed a task."""
    if agent.role != AgentRole.CEO:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the CEO can approve pitches.",
        )
    notes = (data.notes if data else None) or ""
    service = get_pitch_service(db)
    try:
        pitch = await service.approve(pitch_id, notes, agent.agent_id)
    except (
        NotFoundError,
        ConflictError,
        ValidationError,
        ProvisioningError,
    ) as exc:
        raise _to_http_exc(exc) from exc
    await db.commit()
    return _to_response(pitch)


@router.post("/{pitch_id}/reject", response_model=PitchResponse)
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.max_request_size(size_bytes=8192)
@guard_deco.content_type_filter(["application/json"])
async def reject_pitch(
    pitch_id: UUID,
    data: PitchDecision,
    db: DbSession,
    agent: CurrentAgentContext,
) -> PitchResponse:
    """CEO rejects a pitch (reason required)."""
    if agent.role != AgentRole.CEO:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the CEO can reject pitches.",
        )
    if not data.notes or not data.notes.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="a rejection reason is required",
        )
    service = get_pitch_service(db)
    try:
        pitch = await service.reject(pitch_id, data.notes, agent.agent_id)
    except (NotFoundError, ConflictError) as exc:
        raise _to_http_exc(exc) from exc
    await db.commit()
    return _to_response(pitch)
