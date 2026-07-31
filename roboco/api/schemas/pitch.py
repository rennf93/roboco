"""Pitch API schemas — Board proposals and CEO decisions."""

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from roboco.db.tables import PitchTable


class PitchCreateRequest(BaseModel):
    """Board authors a pitch."""

    title: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=50, pattern=r"^[a-z0-9-]+$")
    problem: str = Field(min_length=1)
    proposed_solution: str = Field(min_length=1)
    target_cells: list[str] = Field(min_length=1)


class PitchDecision(BaseModel):
    """CEO approve/reject payload."""

    notes: str | None = None


class PitchResponse(BaseModel):
    """A pitch as returned to the Board / CEO."""

    id: str
    title: str
    slug: str
    problem: str
    proposed_solution: str
    target_cells: list[str]
    status: str
    created_by: str
    decided_by: str | None = None
    decision_notes: str | None = None
    provisioned_product_id: str | None = None
    provisioned_project_ids: list[str]
    seed_task_id: str | None = None
    created_at: str | None = None


def pitch_to_response(pitch: "PitchTable") -> PitchResponse:
    """Convert a PitchTable to PitchResponse."""
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
