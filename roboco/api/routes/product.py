"""Product management routes — CRUD + per-cell project mapping."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from roboco.api.deps import CurrentAgentContext, DbSession, require_pm_or_above
from roboco.api.schemas.product import (
    ProductCreateRequest,
    ProductResponse,
    ProductSummaryResponse,
    ProductUpdateRequest,
    product_to_response,
    product_to_summary,
)
from roboco.models.product import ProductCellMapping, ProductCreate, ProductUpdate
from roboco.services.product import get_product_service

router = APIRouter()


def _to_mappings(cells: list) -> list[ProductCellMapping]:
    return [ProductCellMapping(team=c.team, project_id=c.project_id) for c in cells]


@router.get("", response_model=list[ProductSummaryResponse])
async def list_products(
    db: DbSession,
    _agent: CurrentAgentContext,
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[ProductSummaryResponse]:
    service = get_product_service(db)
    products = await service.list_all(limit=limit, offset=offset)
    return [product_to_summary(p) for p in products]


@router.post("", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(
    data: ProductCreateRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> ProductResponse:
    require_pm_or_above(agent.role, "create products")
    service = get_product_service(db)
    create_data = ProductCreate(
        name=data.name,
        slug=data.slug,
        description=data.description,
        cells=_to_mappings(data.cells),
    )
    try:
        product = await service.create(create_data, created_by=agent.agent_id)
        await db.commit()
        return product_to_response(product)
    except Exception as e:
        await db.rollback()
        if "already exists" in str(e):
            raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e)) from e
        raise


@router.get("/{product_id}", response_model=ProductResponse)
async def get_product(
    product_id: str,
    db: DbSession,
    _agent: CurrentAgentContext,
) -> ProductResponse:
    service = get_product_service(db)
    try:
        uuid = UUID(product_id)
        product = await service.get(uuid)
    except ValueError:
        product = await service.get_by_slug(product_id)
    if not product:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Product not found: {product_id}"
        )
    return product_to_response(product)


@router.patch("/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: UUID,
    data: ProductUpdateRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> ProductResponse:
    require_pm_or_above(agent.role, "update products")
    service = get_product_service(db)
    update_data = ProductUpdate(
        name=data.name,
        description=data.description,
        cells=_to_mappings(data.cells) if data.cells is not None else None,
    )
    product = await service.update(product_id, update_data)
    if not product:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")
    await db.commit()
    return product_to_response(product)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
) -> None:
    require_pm_or_above(agent.role, "delete products")
    service = get_product_service(db)
    # Resolve existence first so a genuine 404 isn't masked by the FK guard.
    if await service.get(product_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")
    try:
        await service.delete(product_id)
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Cannot delete product: it is still referenced by tasks.",
        ) from e
