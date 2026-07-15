"""API request/response schemas for the Product entity."""

from datetime import datetime
from typing import TYPE_CHECKING
from typing import cast as typing_cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from roboco.foundation.identity import Team

if TYPE_CHECKING:
    from roboco.db.tables import ProductTable


class CellMapping(BaseModel):
    team: Team
    project_id: UUID

    model_config = ConfigDict(from_attributes=True)


class ProductResponse(BaseModel):
    id: UUID
    name: str
    slug: str
    description: str | None = None
    cells: list[CellMapping] = Field(default_factory=list)
    created_by: UUID
    created_at: datetime
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class ProductCellSummary(BaseModel):
    """One cell->project mapping for the summary list (carries the project name
    so the panel can show which repo each cell points at without a second fetch).
    """

    team: Team
    project_id: UUID
    project_name: str

    model_config = ConfigDict(from_attributes=True)


class ProductProgressSummary(BaseModel):
    """Task progress across a product's cell projects.

    done = completed; blocked = blocked; active = every non-terminal,
    non-cancelled, non-blocked status. Cancelled tasks are excluded (abandoned
    work is not progress).
    """

    done: int = 0
    active: int = 0
    blocked: int = 0

    model_config = ConfigDict(from_attributes=True)


class ProductSummaryResponse(BaseModel):
    id: UUID
    name: str
    slug: str
    cell_count: int = 0
    cells: list[ProductCellSummary] = Field(default_factory=list)
    progress: ProductProgressSummary = Field(default_factory=ProductProgressSummary)

    model_config = ConfigDict(from_attributes=True)


class ProductCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    slug: str = Field(..., min_length=1, max_length=50, pattern=r"^[a-z0-9-]+$")
    description: str | None = None
    cells: list[CellMapping] = Field(default_factory=list)


class ProductUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    cells: list[CellMapping] | None = None


def product_to_response(product: "ProductTable") -> ProductResponse:
    return ProductResponse(
        id=typing_cast("UUID", product.id),
        name=str(product.name),
        slug=str(product.slug),
        description=product.description,
        cells=[
            CellMapping(team=c.team, project_id=typing_cast("UUID", c.project_id))
            for c in product.cells
        ],
        created_by=typing_cast("UUID", product.created_by),
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


def product_to_summary(
    product: "ProductTable",
    progress: ProductProgressSummary | None = None,
) -> ProductSummaryResponse:
    return ProductSummaryResponse(
        id=typing_cast("UUID", product.id),
        name=str(product.name),
        slug=str(product.slug),
        cell_count=len(product.cells),
        cells=[
            ProductCellSummary(
                team=c.team,
                project_id=typing_cast("UUID", c.project_id),
                project_name=str(c.project.name) if c.project else "",
            )
            for c in product.cells
        ],
        progress=progress or ProductProgressSummary(),
    )
