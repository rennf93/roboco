"""Product domain models — a per-cell Project mapping for board->cells routing."""

from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import ConfigDict, Field, field_validator

from roboco.foundation.identity import CELL_TEAMS, Team
from roboco.models.base import RobocoBase, TimestampMixin


class ProductCellMapping(RobocoBase):
    """One cell -> Project assignment within a Product."""

    # Keep ``team`` as a real ``Team`` enum. ``RobocoBase`` sets
    # ``use_enum_values=True``, which would coerce the field to the plain
    # string ``"backend"`` and break ``team in CELL_TEAMS`` and enum identity
    # for callers. This single override inherits the rest of the base config.
    model_config = ConfigDict(use_enum_values=False)

    team: Team
    project_id: UUID

    @field_validator("team")
    @classmethod
    def _must_be_a_cell(cls, v: Team) -> Team:
        if v not in CELL_TEAMS:
            raise ValueError(
                f"team must be a cell (one of {sorted(t.value for t in CELL_TEAMS)}); "
                f"got {v.value!r}"
            )
        return v


class Product(TimestampMixin):
    """A product groups the per-cell Project mapping for a repo topology."""

    id: UUID = Field(default_factory=uuid4, description="Unique product identifier")
    name: str = Field(..., min_length=1, max_length=100)
    slug: str = Field(
        ...,
        min_length=1,
        max_length=50,
        pattern=r"^[a-z0-9-]+$",
        description="URL-safe identifier (e.g. 'roboco')",
    )
    description: str | None = Field(default=None)
    cells: list[ProductCellMapping] = Field(default_factory=list)
    created_by: UUID = Field(..., description="Who registered the product")


class ProductCreate(RobocoBase):
    """Service-layer create DTO."""

    name: str = Field(..., min_length=1, max_length=100)
    slug: str = Field(..., min_length=1, max_length=50, pattern=r"^[a-z0-9-]+$")
    description: str | None = None
    cells: list[ProductCellMapping] = Field(default_factory=list)


class ProductUpdate(RobocoBase):
    """Service-layer update DTO (all fields optional)."""

    name: str | None = None
    description: str | None = None
    cells: list[ProductCellMapping] | None = None
