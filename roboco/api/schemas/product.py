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


class ProductSummaryResponse(BaseModel):
    id: UUID
    name: str
    slug: str
    cell_count: int = 0

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


def product_to_summary(product: "ProductTable") -> ProductSummaryResponse:
    return ProductSummaryResponse(
        id=typing_cast("UUID", product.id),
        name=str(product.name),
        slug=str(product.slug),
        cell_count=len(product.cells),
    )
