"""
Pitch Route Helpers

Route-glue helpers backing roboco/api/routes/pitch.py.
"""

from fastapi import HTTPException, status

from roboco.api.schemas.pitch import PitchResponse
from roboco.db.tables import PitchTable
from roboco.foundation.identity import CELL_TEAMS, Team
from roboco.services.base import ConflictError, NotFoundError, ValidationError
from roboco.services.github_provisioning import (
    ProvisioningDisabledError,
    ProvisioningError,
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
