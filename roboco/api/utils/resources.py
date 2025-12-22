"""
Resource Helpers

Common patterns for resource retrieval and validation in API routes.
Reduces boilerplate for get-or-404 and ownership checks.
"""

from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.api.utils.errors import forbidden, not_found


async def get_or_404[T](
    db: AsyncSession,
    model: type[T],
    resource_id: UUID,
    resource_name: str | None = None,
) -> T:
    """
    Get a resource by ID or raise 404.

    Reduces boilerplate for the common pattern:
        result = await db.execute(select(Model).where(Model.id == id))
        resource = result.scalar_one_or_none()
        if not resource:
            raise HTTPException(404, "Not found")
        return resource

    Usage:
        task = await get_or_404(db, TaskTable, task_id, "Task")

    Args:
        db: Database session
        model: SQLAlchemy model class
        resource_id: UUID of the resource
        resource_name: Human-readable name for error messages

    Returns:
        The found resource

    Raises:
        HTTPException: 404 if not found
    """
    model_any = cast("Any", model)
    result = await db.execute(select(model).where(model_any.id == resource_id))
    resource = result.scalar_one_or_none()

    if resource is None:
        name = resource_name or model.__name__
        raise not_found(name, str(resource_id))

    return resource


async def get_by_field_or_404[T](
    db: AsyncSession,
    model: type[T],
    field_name: str,
    field_value: str,
    resource_name: str | None = None,
) -> T:
    """
    Get a resource by a field value or raise 404.

    Usage:
        channel = await get_by_field_or_404(db, ChannelTable, "slug", slug, "Channel")

    Args:
        db: Database session
        model: SQLAlchemy model class
        field_name: Name of the field to filter by
        field_value: Value to match
        resource_name: Human-readable name for error messages

    Returns:
        The found resource

    Raises:
        HTTPException: 404 if not found
    """
    field = getattr(model, field_name)
    result = await db.execute(select(model).where(field == field_value))
    resource = result.scalar_one_or_none()

    if resource is None:
        name = resource_name or model.__name__
        raise not_found(name, field_value)

    return resource


def require_ownership[T](
    resource: T,
    owner_field: str,
    agent_id: UUID,
    action: str,
) -> None:
    """
    Require that the agent owns the resource.

    Usage:
        require_ownership(task, "assigned_to", agent_id, "update task")

    Args:
        resource: The resource to check
        owner_field: Name of the field containing the owner UUID
        agent_id: UUID of the requesting agent
        action: Description of the action for error message

    Raises:
        HTTPException: 403 if agent is not the owner
    """
    owner_id = getattr(resource, owner_field, None)

    if owner_id is None:
        # No owner - allow (or raise if you want to require ownership)
        return

    # Handle both UUID and string comparisons
    owner_uuid = UUID(str(owner_id)) if not isinstance(owner_id, UUID) else owner_id

    if owner_uuid != agent_id:
        raise forbidden(action, "not resource owner")


def require_recipient(
    recipients: list,  # Accept any list type (handles SQLAlchemy UUID arrays)
    agent_id: UUID,
    action: str = "access resource",
) -> None:
    """
    Require that the agent is in the recipients list.

    Usage:
        require_recipient(notification.to_agents, agent_id, "view notification")

    Args:
        recipients: List of recipient UUIDs (Python UUID, str, or SQLAlchemy UUID)
        agent_id: UUID of the requesting agent
        action: Description of the action for error message

    Raises:
        HTTPException: 403 if agent is not a recipient
    """
    # Normalize to UUIDs for comparison
    recipient_uuids = [UUID(str(r)) for r in recipients]

    if agent_id not in recipient_uuids:
        raise forbidden(action, "not a recipient")


def require_membership(
    members: list,  # Accept any list type (handles SQLAlchemy UUID arrays)
    agent_id: UUID,
    resource_name: str = "resource",
) -> None:
    """
    Require that the agent is a member.

    Usage:
        require_membership(channel.members, agent_id, "channel")

    Args:
        members: List of member UUIDs (Python UUID, str, or SQLAlchemy UUID)
        agent_id: UUID of the requesting agent
        resource_name: Name of the resource for error message

    Raises:
        HTTPException: 403 if agent is not a member
    """
    # Normalize to UUIDs for comparison
    member_uuids = [UUID(str(m)) for m in members]

    if agent_id not in member_uuids:
        raise forbidden(f"access {resource_name}", "not a member")
