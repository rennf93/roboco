"""
Base Repository Pattern

Generic repository for common CRUD operations.
Services can use these to avoid duplicating basic database operations.
"""

from typing import Any, cast
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from roboco.services.base import NotFoundError

logger = structlog.get_logger()


class BaseRepository[ModelT]:
    """
    Generic repository for database operations.

    Provides standard CRUD operations that can be reused across services.
    Subclass to add entity-specific operations.

    Usage:
        class TaskRepository(BaseRepository[TaskTable]):
            model = TaskTable

            async def find_by_status(self, status: TaskStatus) -> list[TaskTable]:
                return await self.find_by(TaskTable.status == status)

        repo = TaskRepository(session)
        task = await repo.get(task_id)
        tasks = await repo.list(limit=10, offset=0)
    """

    # Override in subclass
    model: type[ModelT]
    model_name: str = "Resource"

    def __init__(self, session: AsyncSession) -> None:
        """
        Initialize the repository with a database session.

        Args:
            session: SQLAlchemy async session
        """
        self.session = session
        self.log = logger.bind(repository=self.model_name)

    # =========================================================================
    # BASIC CRUD
    # =========================================================================

    async def get(self, entity_id: UUID) -> ModelT | None:
        """
        Get an entity by ID.

        Args:
            entity_id: UUID of the entity

        Returns:
            The entity if found, None otherwise
        """
        model = cast("Any", self.model)
        result = await self.session.execute(
            select(self.model).where(model.id == entity_id)
        )
        return result.scalar_one_or_none()

    async def get_or_raise(self, entity_id: UUID) -> ModelT:
        """
        Get an entity by ID or raise NotFoundError.

        Args:
            entity_id: UUID of the entity

        Returns:
            The entity

        Raises:
            NotFoundError: If entity not found
        """
        entity = await self.get(entity_id)
        if entity is None:
            raise NotFoundError(self.model_name, str(entity_id))
        return entity

    async def get_all(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        order_by: Any | None = None,
    ) -> list[ModelT]:
        """
        Get all entities with pagination.

        Args:
            limit: Maximum number of results
            offset: Number of results to skip
            order_by: Column to order by (default: created_at desc)

        Returns:
            List of entities
        """
        query = select(self.model)

        model = cast("Any", self.model)
        if order_by is not None:
            query = query.order_by(order_by)
        elif hasattr(self.model, "created_at"):
            query = query.order_by(model.created_at.desc())

        query = query.limit(limit).offset(offset)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def find_by(
        self,
        *conditions: Any,
        limit: int = 100,
        offset: int = 0,
        order_by: Any | None = None,
    ) -> list[ModelT]:
        """
        Find entities matching conditions.

        Args:
            conditions: SQLAlchemy filter conditions
            limit: Maximum number of results
            offset: Number of results to skip
            order_by: Column to order by

        Returns:
            List of matching entities
        """
        query = select(self.model).where(*conditions)

        model = cast("Any", self.model)
        if order_by is not None:
            query = query.order_by(order_by)
        elif hasattr(self.model, "created_at"):
            query = query.order_by(model.created_at.desc())

        query = query.limit(limit).offset(offset)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def find_one(self, *conditions: Any) -> ModelT | None:
        """
        Find a single entity matching conditions.

        Args:
            conditions: SQLAlchemy filter conditions

        Returns:
            The entity if found, None otherwise
        """
        query = select(self.model).where(*conditions).limit(1)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def exists(self, entity_id: UUID) -> bool:
        """
        Check if an entity exists.

        Args:
            entity_id: UUID of the entity

        Returns:
            True if exists, False otherwise
        """
        model = cast("Any", self.model)
        result = await self.session.execute(
            select(func.count()).where(model.id == entity_id)
        )
        count = result.scalar_one()
        return count > 0

    async def count(self, *conditions: Any) -> int:
        """
        Count entities matching conditions.

        Args:
            conditions: SQLAlchemy filter conditions

        Returns:
            Count of matching entities
        """
        query = select(func.count()).select_from(self.model)
        if conditions:
            query = query.where(*conditions)
        result = await self.session.execute(query)
        return result.scalar_one() or 0

    # =========================================================================
    # MUTATIONS
    # =========================================================================

    async def add(self, entity: ModelT) -> ModelT:
        """
        Add a new entity to the session.

        Args:
            entity: The entity to add

        Returns:
            The added entity (with ID populated after flush)
        """
        self.session.add(entity)
        await self.session.flush()
        return entity

    async def delete(self, entity: ModelT) -> None:
        """
        Delete an entity.

        Args:
            entity: The entity to delete
        """
        await self.session.delete(entity)
        await self.session.flush()

    async def delete_by_id(self, entity_id: UUID) -> bool:
        """
        Delete an entity by ID.

        Args:
            entity_id: UUID of the entity

        Returns:
            True if deleted, False if not found
        """
        entity = await self.get(entity_id)
        if entity is None:
            return False
        await self.delete(entity)
        return True

    # =========================================================================
    # QUERY BUILDING
    # =========================================================================

    def query(self) -> Select[tuple[ModelT]]:
        """
        Start a query for this model.

        Returns:
            SQLAlchemy Select object
        """
        return select(self.model)

    async def execute_query(self, query: Select[tuple[ModelT]]) -> list[ModelT]:
        """
        Execute a query and return results.

        Args:
            query: SQLAlchemy Select object

        Returns:
            List of matching entities
        """
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def execute_scalar(self, query: Select[tuple[Any]]) -> Any:
        """
        Execute a query and return a scalar result.

        Args:
            query: SQLAlchemy Select object

        Returns:
            Scalar result
        """
        result = await self.session.execute(query)
        return result.scalar_one_or_none()
