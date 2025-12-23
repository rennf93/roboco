"""
Query Helpers

Common query patterns used across services.
Provides reusable filter and query building utilities.
"""

from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from roboco.db.tables import AgentTable


def pagination(
    query: Select[Any],
    limit: int = 100,
    offset: int = 0,
) -> Select[Any]:
    """
    Apply pagination to a query.

    Args:
        query: SQLAlchemy Select query
        limit: Maximum number of results
        offset: Number of results to skip

    Returns:
        Query with pagination applied
    """
    return query.limit(limit).offset(offset)


def status_filter(
    query: Select[Any],
    model: Any,
    status: Enum | str | None,
) -> Select[Any]:
    """
    Apply status filter to a query.

    Args:
        query: SQLAlchemy Select query
        model: Model class with status column
        status: Status value to filter by

    Returns:
        Query with status filter applied
    """
    if status is None:
        return query

    if hasattr(status, "value"):
        status = status.value

    return query.where(model.status == status)


def team_filter(
    query: Select[Any],
    model: Any,
    team: Enum | str | None,
) -> Select[Any]:
    """
    Apply team filter to a query.

    Args:
        query: SQLAlchemy Select query
        model: Model class with team column
        team: Team value to filter by

    Returns:
        Query with team filter applied
    """
    if team is None:
        return query

    if hasattr(team, "value"):
        team = team.value

    return query.where(model.team == team)


def agent_id_filter(
    query: Select[Any],
    model: Any,
    agent_id: UUID | None,
    field_name: str = "agent_id",
) -> Select[Any]:
    """
    Apply agent ID filter to a query.

    Args:
        query: SQLAlchemy Select query
        model: Model class
        agent_id: Agent UUID to filter by
        field_name: Name of the agent ID field

    Returns:
        Query with agent filter applied
    """
    if agent_id is None:
        return query

    field = getattr(model, field_name)
    return query.where(field == agent_id)


def timestamp_filter(
    query: Select[Any],
    model: Any,
    since: datetime | None = None,
    until: datetime | None = None,
    field_name: str = "created_at",
) -> Select[Any]:
    """
    Apply timestamp range filter to a query.

    Args:
        query: SQLAlchemy Select query
        model: Model class
        since: Start of time range (inclusive)
        until: End of time range (inclusive)
        field_name: Name of the timestamp field

    Returns:
        Query with timestamp filter applied
    """
    field = getattr(model, field_name)

    if since is not None:
        query = query.where(field >= since)
    if until is not None:
        query = query.where(field <= until)

    return query


def days_ago(days: int) -> datetime:
    """
    Get a datetime for N days ago.

    Args:
        days: Number of days ago

    Returns:
        Datetime N days in the past
    """
    return datetime.now(UTC) - timedelta(days=days)


async def resolve_agent_uuid(
    db: AsyncSession,
    agent_id_or_slug: str,
) -> UUID | None:
    """
    Resolve an agent ID from either a UUID string or slug.

    This consolidates the duplicated pattern found in deps.py and journal.py.

    Args:
        db: Database session
        agent_id_or_slug: Either a UUID string or agent slug (e.g., "be-dev-1")

    Returns:
        UUID of the agent, or None if not found/invalid
    """
    # First, try to parse as UUID
    try:
        return UUID(agent_id_or_slug)
    except ValueError:
        pass

    # Not a UUID, try to look up by slug
    result = await db.execute(
        select(AgentTable.id).where(AgentTable.slug == agent_id_or_slug)
    )
    agent_uuid = result.scalar_one_or_none()

    if agent_uuid is None:
        return None

    return UUID(str(agent_uuid))


async def resolve_agent_identity(
    db: AsyncSession,
    agent_id_or_slug: str,
) -> tuple[UUID, str] | None:
    """
    Resolve an agent identity to both UUID and slug.

    Args:
        db: Database session
        agent_id_or_slug: Either a UUID string or agent slug (e.g., "be-dev-1")

    Returns:
        Tuple of (UUID, slug), or None if not found
    """
    # First, try to parse as UUID
    try:
        agent_uuid = UUID(agent_id_or_slug)
        # It's a UUID, look up the slug
        result = await db.execute(
            select(AgentTable.slug).where(AgentTable.id == agent_uuid)
        )
        slug = result.scalar_one_or_none()
        if slug is None:
            return None
        return (agent_uuid, slug)
    except ValueError:
        pass

    # Not a UUID, try to look up by slug
    result = await db.execute(
        select(AgentTable.id).where(AgentTable.slug == agent_id_or_slug)
    )
    agent_uuid = result.scalar_one_or_none()

    if agent_uuid is None:
        return None

    return (UUID(str(agent_uuid)), agent_id_or_slug)


async def get_agent_slug(
    db: AsyncSession,
    agent_id: UUID,
) -> str | None:
    """
    Get an agent's slug from their UUID.

    Args:
        db: Database session
        agent_id: The agent's UUID

    Returns:
        The agent's slug (e.g., "be-dev-1"), or None if not found
    """
    result = await db.execute(
        select(AgentTable.slug).where(AgentTable.id == agent_id)
    )
    return result.scalar_one_or_none()


async def get_agent_by_slug(
    db: AsyncSession,
    slug: str,
) -> AgentTable | None:
    """
    Get an agent by their slug.

    Args:
        db: Database session
        slug: Agent slug (e.g., "be-dev-1")

    Returns:
        Agent table row, or None if not found
    """
    result = await db.execute(select(AgentTable).where(AgentTable.slug == slug))
    return result.scalar_one_or_none()
