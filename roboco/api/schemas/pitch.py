"""Pitch API schemas — Board proposals and CEO decisions."""

from pydantic import BaseModel, Field


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
