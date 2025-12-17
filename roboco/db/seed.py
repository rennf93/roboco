"""
Database Seeding Operations

Functions to populate the database with initial data.
Separates database operations from bootstrap orchestration.
"""

import contextlib
from uuid import UUID as UUIDType

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.db.base import get_db_context, init_db
from roboco.db.tables import (
    AgentTable,
    ChannelTable,
    GroupTable,
    MessageTable,
    SessionTable,
)
from roboco.models import AgentRole, ChannelType, MessageType, SessionStatus, Team
from roboco.seeds import (
    AUDITOR_SILENT_ACCESS,
    CHANNEL_MEMBERSHIPS,
    DEFAULT_AGENTS,
    DEFAULT_CHANNELS,
    INITIAL_MESSAGES,
)

logger = structlog.get_logger()


# =============================================================================
# CHANNEL OPERATIONS
# =============================================================================


async def create_channels(session: AsyncSession) -> dict[str, str]:
    """Create default channels. Returns slug -> id mapping."""
    channel_ids = {}

    for channel_data in DEFAULT_CHANNELS:
        # Check if exists
        result = await session.execute(
            select(ChannelTable).where(ChannelTable.slug == channel_data["slug"])
        )
        existing = result.scalar_one_or_none()

        if existing:
            channel_ids[channel_data["slug"]] = str(existing.id)
            logger.info("Channel exists", slug=channel_data["slug"])
            continue

        # Create channel using ORM
        channel = ChannelTable(
            name=channel_data["name"],
            slug=channel_data["slug"],
            type=ChannelType(channel_data["channel_type"]),
            description=channel_data["description"],
        )
        session.add(channel)
        await session.flush()

        channel_ids[channel_data["slug"]] = str(channel.id)
        logger.info("Channel created", slug=channel_data["slug"])

    return channel_ids


# =============================================================================
# AGENT OPERATIONS
# =============================================================================


async def create_agents(session: AsyncSession) -> dict[str, str]:
    """Create default agents. Returns agent_id (slug) -> db_id mapping."""

    agent_ids = {}

    for agent_data in DEFAULT_AGENTS:
        slug = agent_data["agent_id"]

        # Check if exists
        result = await session.execute(
            select(AgentTable).where(AgentTable.slug == slug)
        )
        existing = result.scalar_one_or_none()

        if existing:
            agent_ids[slug] = str(existing.id)
            logger.info("Agent exists", agent_id=slug)
            continue

        # Map role string to enum
        role_str = agent_data["role"]
        role = AgentRole(role_str)

        # Map team string to enum (if present)
        team_str = agent_data.get("team")
        team = Team(team_str) if team_str else None

        # If slug is a valid UUID, use it as the database ID
        # (important for CEO so X-Agent-ID header matches the DB id)
        explicit_id: UUIDType | None = None
        with contextlib.suppress(ValueError):
            explicit_id = UUIDType(slug)

        # Create agent using ORM
        agent = AgentTable(
            id=explicit_id,  # Will use slug as ID if it's a valid UUID
            name=agent_data["name"],
            slug=slug,
            role=role,
            team=team,
            model_config={},
            system_prompt=f"You are {agent_data['name']}, a {role_str} agent.",
        )
        session.add(agent)
        await session.flush()

        agent_ids[slug] = str(agent.id)
        logger.info("Agent created", agent_id=slug)

    return agent_ids


# =============================================================================
# CHANNEL MEMBERSHIP OPERATIONS
# =============================================================================


async def _get_channel(
    session: AsyncSession,
    channel_id: str,
) -> ChannelTable | None:
    """Fetch a channel by ID."""
    result = await session.execute(
        select(ChannelTable).where(ChannelTable.id == UUIDType(channel_id))
    )
    return result.scalar_one_or_none()


def _build_member_uuids(
    agent_slugs: list[str],
    agent_ids: dict[str, str],
) -> list[UUIDType]:
    """Build a list of UUIDs from agent slugs."""
    return [UUIDType(agent_ids[slug]) for slug in agent_slugs if slug in agent_ids]


async def _configure_channel_members(
    session: AsyncSession,
    channel_ids: dict[str, str],
    agent_ids: dict[str, str],
) -> None:
    """Configure members and writers for all channels."""
    for channel_slug, members in CHANNEL_MEMBERSHIPS.items():
        channel_id = channel_ids.get(channel_slug)
        if not channel_id:
            continue

        channel = await _get_channel(session, channel_id)
        if not channel:
            continue

        member_uuids = _build_member_uuids(members, agent_ids)
        channel.members = member_uuids
        channel.writers = member_uuids  # All members can write by default


async def _add_auditor_silent_access(
    session: AsyncSession,
    channel_ids: dict[str, str],
    auditor_uuid: UUIDType,
) -> None:
    """Add auditor as silent observer to specified channels."""
    for channel_slug in AUDITOR_SILENT_ACCESS:
        channel_id = channel_ids.get(channel_slug)
        if not channel_id:
            continue

        channel = await _get_channel(session, channel_id)
        if not channel:
            continue

        observers = channel.silent_observers or []
        if auditor_uuid not in observers:
            channel.silent_observers = [*observers, auditor_uuid]


async def create_channel_memberships(
    session: AsyncSession,
    channel_ids: dict[str, str],
    agent_ids: dict[str, str],
) -> None:
    """
    Configure channel memberships.

    Note: ChannelTable uses arrays for members/writers/silent_observers
    rather than a separate membership table.
    """
    await _configure_channel_members(session, channel_ids, agent_ids)

    auditor_db_id = agent_ids.get("auditor")
    if auditor_db_id:
        await _add_auditor_silent_access(session, channel_ids, UUIDType(auditor_db_id))

    logger.info("Channel memberships configured")


# =============================================================================
# INITIAL MESSAGE OPERATIONS
# =============================================================================


async def create_initial_messages(
    session: AsyncSession,
    channel_ids: dict[str, str],
    agent_ids: dict[str, str],
) -> None:
    """Create initial welcome messages in channels."""
    for channel_slug, message_data in INITIAL_MESSAGES.items():
        channel_id_str = channel_ids.get(channel_slug)
        agent_id_str = agent_ids.get(message_data["agent_id"])

        if not channel_id_str or not agent_id_str:
            continue

        channel_uuid = UUIDType(channel_id_str)
        agent_uuid = UUIDType(agent_id_str)

        # Check if channel already has messages (don't duplicate)
        existing_result = await session.execute(
            select(MessageTable.id)
            .where(MessageTable.channel_id == channel_uuid)
            .limit(1)
        )
        if existing_result.scalar_one_or_none():
            logger.info("Channel already has messages", channel=channel_slug)
            continue

        # Create a group and session for the message
        group = GroupTable(
            channel_id=channel_uuid,
            name="General",
            hierarchy_level=0,
            is_active=True,
        )
        session.add(group)
        await session.flush()

        msg_session = SessionTable(
            group_id=group.id,
            status=SessionStatus.ACTIVE,
        )
        session.add(msg_session)
        await session.flush()

        group.active_session_id = msg_session.id

        # Create the message
        message = MessageTable(
            agent_id=agent_uuid,
            channel_id=channel_uuid,
            group_id=group.id,
            session_id=msg_session.id,
            type=MessageType.DIALOGUE,
            content=message_data["content"],
            content_length=len(message_data["content"]),
        )
        session.add(message)

        logger.info("Initial message created", channel=channel_slug)

    logger.info("Initial channel messages created")


# =============================================================================
# HIGH-LEVEL BOOTSTRAP
# =============================================================================


async def bootstrap_database() -> None:
    """Initialize database with default data."""
    logger.info("Starting database bootstrap...")

    # Initialize database schema
    await init_db()
    logger.info("Database schema initialized")

    # Create default data
    async with get_db_context() as session:
        channel_ids = await create_channels(session)
        agent_ids = await create_agents(session)
        await create_channel_memberships(session, channel_ids, agent_ids)
        await create_initial_messages(session, channel_ids, agent_ids)
        await session.commit()

    logger.info("Database bootstrap complete")
