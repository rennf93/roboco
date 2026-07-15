"""ProductService — CRUD + the per-cell project_for routing resolver."""

from typing import ClassVar
from typing import cast as typing_cast
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from roboco.db.tables import ProductProjectTable, ProductTable, TaskTable
from roboco.foundation.identity import Team
from roboco.models.base import TaskStatus
from roboco.models.product import ProductCellMapping, ProductCreate, ProductUpdate
from roboco.services.base import BaseService, ConflictError, NotFoundError

# Statuses that are NOT active progress: completed (done), cancelled
# (abandoned), blocked (its own bucket). Everything else counts as active.
_INACTIVE_STATUSES = (
    TaskStatus.COMPLETED,
    TaskStatus.CANCELLED,
    TaskStatus.BLOCKED,
)


def _project_to_products_map(
    products: list[ProductTable],
) -> dict[UUID, list[UUID]]:
    """Distinct (project_id, product_id) pairs — dedup the monorepo case."""
    proj_to_products: dict[UUID, list[UUID]] = {}
    for product in products:
        seen: set[UUID] = set()
        for cell in product.cells:
            pid = typing_cast("UUID", cell.project_id)
            if pid in seen:
                continue
            seen.add(pid)
            proj_to_products.setdefault(pid, []).append(typing_cast("UUID", product.id))
    return proj_to_products


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
        # Eager-load cells + each cell's project so product_to_summary can read
        # project.name without an N+1 lazy join per cell.
        query = (
            select(ProductTable)
            .options(
                selectinload(ProductTable.cells).joinedload(ProductProjectTable.project)
            )
            .order_by(ProductTable.name)
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def progress_for_products(
        self, products: list[ProductTable]
    ) -> dict[UUID, dict[str, int]]:
        """Per-product task progress (done/active/blocked) across cell projects.

        One grouped query over tasks for every distinct project_id any product
        references, summed per product. A product's cells may point several teams
        at the same project (the monorepo case), so each project is counted once
        per product. Returns {product_id: {done, active, blocked}}.
        """
        # distinct (product_id, project_id) pairs — dedup the monorepo case.
        proj_to_products = _project_to_products_map(products)
        if not proj_to_products:
            return {}

        result = await self.session.execute(
            select(
                TaskTable.project_id,
                func.coalesce(
                    func.sum(
                        case((TaskTable.status == TaskStatus.COMPLETED, 1), else_=0)
                    ),
                    0,
                ).label("done"),
                func.coalesce(
                    func.sum(
                        case((TaskTable.status == TaskStatus.BLOCKED, 1), else_=0)
                    ),
                    0,
                ).label("blocked"),
                func.coalesce(
                    func.sum(
                        case((TaskTable.status.in_(_INACTIVE_STATUSES), 0), else_=1)
                    ),
                    0,
                ).label("active"),
            )
            .where(TaskTable.project_id.in_(list(proj_to_products.keys())))
            .group_by(TaskTable.project_id)
        )
        per_project: dict[UUID, dict[str, int]] = {}
        for row in result.fetchall():
            per_project[typing_cast("UUID", row.project_id)] = {
                "done": int(row.done or 0),
                "active": int(row.active or 0),
                "blocked": int(row.blocked or 0),
            }

        out: dict[UUID, dict[str, int]] = {}
        for project_id, product_ids in proj_to_products.items():
            counts = per_project.get(project_id)
            if not counts:
                continue
            for pid in product_ids:
                agg = out.setdefault(pid, {"done": 0, "active": 0, "blocked": 0})
                agg["done"] += counts["done"]
                agg["active"] += counts["active"]
                agg["blocked"] += counts["blocked"]
        return out

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

    async def distinct_project_ids(self, product_id: UUID) -> list[UUID]:
        """Distinct repos a product spans — one Main-PM integration branch each.

        The product's cell->project map may point several teams at the same
        Project (the monorepo case) or at different ones (multi-repo). The
        Main-PM root cuts one ``feature/main_pm/{root}`` integration branch per
        DISTINCT project, so cells in that repo branch off it instead of master.
        Ordered by the first team that references each project for determinism.
        """
        result = await self.session.execute(
            select(ProductProjectTable.project_id)
            .where(ProductProjectTable.product_id == product_id)
            .order_by(ProductProjectTable.team)
        )
        seen: dict[UUID, None] = {}
        for project_id in result.scalars().all():
            seen.setdefault(project_id, None)
        return list(seen)

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
        product.cells.clear()  # delete-orphan marks the old mapping rows for delete
        # Flush the DELETEs before appending the new rows. Within a single flush
        # SQLAlchemy orders INSERTs before DELETEs for the same table, so without
        # this the new (product_id, team) rows collide with the not-yet-deleted
        # old ones on uq_product_projects_product_team (a 409 on any re-mapping of
        # a team that already has a project). Flushing here issues the DELETEs
        # first; the appended rows insert cleanly on the next flush.
        await self.session.flush()
        for mapping in cells:
            product.cells.append(
                ProductProjectTable(team=mapping.team, project_id=mapping.project_id)
            )


def get_product_service(session: AsyncSession) -> ProductService:
    """Get a ProductService instance."""
    return ProductService(session)
