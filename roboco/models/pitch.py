"""Pitch domain models — Board proposals the CEO approves to auto-provision.

A pitch is the origination point of the autonomous strategy engine: the Board
proposes a product (problem + solution + which cells should build it); the CEO
approves; the system provisions a repo per cell, registers Projects (and a
Product when multi-cell), and seeds a delivery task to Main PM. It is layered on
top of the existing Product / coordination-task machinery and does not change
the delivery lifecycle.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field, field_validator

from roboco.foundation.identity import CELL_TEAMS, Team
from roboco.models.base import RobocoBase, TimestampMixin


class PitchStatus(StrEnum):
    """Lifecycle of a pitch (independent of the task delivery lifecycle)."""

    PROPOSED = "proposed"
    PROVISIONED = "provisioned"
    REJECTED = "rejected"
    FAILED = "failed"


def _validate_cells(cells: list[Team]) -> list[Team]:
    if not cells:
        raise ValueError("a pitch must target at least one cell")
    for c in cells:
        if c not in CELL_TEAMS:
            raise ValueError(
                f"target_cells must be cells (one of "
                f"{sorted(t.value for t in CELL_TEAMS)}); got {c!r}"
            )
    return cells


class Pitch(TimestampMixin):
    """A Board-authored product proposal."""

    id: UUID = Field(default_factory=uuid4)
    title: str = Field(..., min_length=1, max_length=200)
    slug: str = Field(..., min_length=1, max_length=50, pattern=r"^[a-z0-9-]+$")
    problem: str = Field(..., min_length=1)
    proposed_solution: str = Field(..., min_length=1)
    target_cells: list[Team] = Field(default_factory=list)
    status: PitchStatus = PitchStatus.PROPOSED
    created_by: UUID
    decided_by: UUID | None = None
    decision_notes: str | None = None
    provisioned_product_id: UUID | None = None
    provisioned_project_ids: list[UUID] = Field(default_factory=list)
    seed_task_id: UUID | None = None


class PitchCreate(RobocoBase):
    """Service-layer create DTO."""

    title: str = Field(..., min_length=1, max_length=200)
    slug: str = Field(..., min_length=1, max_length=50, pattern=r"^[a-z0-9-]+$")
    problem: str = Field(..., min_length=1)
    proposed_solution: str = Field(..., min_length=1)
    target_cells: list[Team] = Field(..., min_length=1)

    @field_validator("target_cells")
    @classmethod
    def _cells_must_be_cells(cls, v: list[Team]) -> list[Team]:
        return _validate_cells(v)
