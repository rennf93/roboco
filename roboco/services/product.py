"""ProductService — CRUD + the per-cell project_for routing resolver."""

from typing import ClassVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.db.tables import ProductProjectTable, ProductTable
from roboco.foundation.identity import Team
from roboco.models.product import ProductCellMapping, ProductCreate, ProductUpdate
from roboco.services.base import BaseService, ConflictError, NotFoundError


class ProductService(BaseService):
    service_name: ClassVar[str] = "product"

    async def create(self, data: ProductCreate, created_by: UUID) -> ProductTable:
        if await self.get_by_slug(data.slug):
            raise ConflictError(
                f"Product with slug '{data.slug}' already exists",
                resource_type="product",
            )
        product = ProductTable(
            name=data.name,
            slug=data.slug,
            description=data.description,
            created_by=created_by,
        )
        self.session.add(product)
        await self.session.flush()
        await self._replace_cells(product, data.cells)
        await self.session.flush()
        self.log.info("Product created", product_id=str(product.id), slug=data.slug)
        return product

    async def get(self, product_id: UUID) -> ProductTable | None:
        result = await self.session.execute(
            select(ProductTable).where(ProductTable.id == product_id)
        )
        return result.scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> ProductTable | None:
        result = await self.session.execute(
            select(ProductTable).where(ProductTable.slug == slug)
        )
        return result.scalar_one_or_none()

    async def get_or_raise(self, product_id: UUID) -> ProductTable:
        product = await self.get(product_id)
        if not product:
            raise NotFoundError("Product", str(product_id))
        return product

    async def update(
        self, product_id: UUID, data: ProductUpdate
    ) -> ProductTable | None:
        product = await self.get(product_id)
        if not product:
            return None
        if data.name is not None:
            product.name = data.name
        if data.description is not None:
            product.description = data.description
        if data.cells is not None:
            await self._replace_cells(product, data.cells)
        await self.session.flush()
        return product

    async def delete(self, product_id: UUID) -> bool:
        product = await self.get(product_id)
        if not product:
            return False
        # tasks.product_id is ON DELETE RESTRICT — a referenced product cannot
        # be deleted; the DB raises IntegrityError, the route maps it to 409.
        await self.session.delete(product)
        await self.session.flush()
        return True

    async def list_all(self, limit: int = 100, offset: int = 0) -> list[ProductTable]:
        query = (
            select(ProductTable).order_by(ProductTable.name).limit(limit).offset(offset)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def project_for(self, product_id: UUID, team: Team | str) -> UUID | None:
        """Resolve the Project a given cell maps to within a product.

        Returns None when the product has no mapping for that cell (the caller
        falls back to the parent task's project). This is the per-cell routing
        keystone called from the gateway delegate path.
        """
        team_value = team.value if isinstance(team, Team) else str(team)
        result = await self.session.execute(
            select(ProductProjectTable.project_id).where(
                ProductProjectTable.product_id == product_id,
                ProductProjectTable.team == team_value,
            )
        )
        return result.scalar_one_or_none()

    async def _replace_cells(
        self, product: ProductTable, cells: list[ProductCellMapping]
    ) -> None:
        """Replace a product's full cell->project map (idempotent set semantics).

        Mutates through the `cells` relationship (cascade=all,delete-orphan) so
        the in-memory collection stays consistent with the DB — product_to_response
        iterates product.cells, and direct session.add of child rows would leave
        that collection stale. A freshly-constructed product was never loaded via
        a query, so its `cells` collection is unloaded; refresh it inside the
        async greenlet before mutating (touching it directly would trigger a
        synchronous lazy load and raise MissingGreenlet).
        """
        await self.session.refresh(product, ["cells"])
        product.cells.clear()  # delete-orphan removes the old mapping rows
        for mapping in cells:
            product.cells.append(
                ProductProjectTable(team=mapping.team, project_id=mapping.project_id)
            )


def get_product_service(session: AsyncSession) -> ProductService:
    """Get a ProductService instance."""
    return ProductService(session)
