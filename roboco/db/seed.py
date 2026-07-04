"""
Database Seeding Operations

Functions to populate the database with initial data.
Separates database operations from bootstrap orchestration.
"""

from uuid import UUID as UUIDType

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.db.base import get_db_context, init_db
from roboco.db.tables import AgentTable
from roboco.models import AgentRole, Team
from roboco.seeds import DEFAULT_AGENTS

logger = structlog.get_logger()


# =============================================================================
# AGENT OPERATIONS
# =============================================================================


async def create_agents(session: AsyncSession) -> dict[str, str]:
    """Create default agents. Returns slug -> db_id (UUID) mapping."""

    agent_ids = {}

    for agent_data in DEFAULT_AGENTS:
        slug = agent_data["slug"]
        static_id = agent_data["id"]  # Static UUID from initial_data.py

        # Check if exists
        result = await session.execute(
            select(AgentTable).where(AgentTable.slug == slug)
        )
        existing = result.scalar_one_or_none()

        if existing:
            agent_ids[slug] = str(existing.id)
            logger.info("Agent exists", slug=slug, id=str(existing.id))
            continue

        # Map role string to enum
        role_str = agent_data["role"]
        role = AgentRole(role_str)

        # Map team string to enum (if present)
        team_str = agent_data.get("team")
        team = Team(team_str) if team_str else None

        # Use the static UUID from initial_data.py
        agent_uuid = UUIDType(static_id)

        # Create agent using ORM with static UUID
        agent = AgentTable(
            id=agent_uuid,
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
        logger.info("Agent created", slug=slug, id=str(agent.id))

    return agent_ids


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
        await create_agents(session)
        await session.commit()

    logger.info("Database bootstrap complete")
