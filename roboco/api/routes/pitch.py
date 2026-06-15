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
from roboco.db.tables import PitchTable
from roboco.foundation.identity import CELL_TEAMS, Team
from roboco.models import AgentRole
from roboco.models.pitch import PitchCreate, PitchStatus
from roboco.services.base import ConflictError, NotFoundError, ValidationError
from roboco.services.github_provisioning import (
    ProvisioningDisabledError,
    ProvisioningError,
)
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


_SERVICE_ERROR_HTTP: tuple[tuple[type[Exception], int], ...] = (
    (NotFoundError, status.HTTP_404_NOT_FOUND),
    (ProvisioningDisabledError, status.HTTP_400_BAD_REQUEST),
    (ProvisioningError, status.HTTP_502_BAD_GATEWAY),
    (ConflictError, status.HTTP_409_CONFLICT),
    (ValidationError, status.HTTP_400_BAD_REQUEST),
)


def _to_http_exc(exc: Exception) -> HTTPException:
    """Translate a known service/provisioning error into an HTTPException.

    ProvisioningDisabledError is listed before ProvisioningError (its parent)
    so the more specific 400 wins.
    """
    detail = getattr(exc, "message", None) or str(exc)
    for exc_type, code in _SERVICE_ERROR_HTTP:
        if isinstance(exc, exc_type):
            return HTTPException(status_code=code, detail=detail)
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail
    )


def _to_response(pitch: PitchTable) -> PitchResponse:
    return PitchResponse(
        id=str(pitch.id),
        title=pitch.title,
        slug=pitch.slug,
        problem=pitch.problem,
        proposed_solution=pitch.proposed_solution,
        target_cells=list(pitch.target_cells or []),
        status=pitch.status,
        created_by=str(pitch.created_by),
        decided_by=str(pitch.decided_by) if pitch.decided_by else None,
        decision_notes=pitch.decision_notes,
        provisioned_product_id=(
            str(pitch.provisioned_product_id) if pitch.provisioned_product_id else None
        ),
        provisioned_project_ids=list(pitch.provisioned_project_ids or []),
        seed_task_id=str(pitch.seed_task_id) if pitch.seed_task_id else None,
        created_at=pitch.created_at.isoformat() if pitch.created_at else None,
    )


def _parse_cells(raw: list[str]) -> list[Team]:
    cells: list[Team] = []
    for c in raw:
        try:
            team = Team(c)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"unknown cell '{c}'",
            ) from exc
        if team not in CELL_TEAMS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"'{c}' is not a cell team",
            )
        cells.append(team)
    return cells


@router.post("", response_model=PitchResponse, status_code=status.HTTP_201_CREATED)
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
