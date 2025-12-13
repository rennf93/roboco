"""
RoboCo Bootstrap Script

Initializes the database, creates default data, and starts the system.
"""

import argparse
import asyncio
from pathlib import Path
from typing import Any
from uuid import UUID as UUIDType

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.api.routes.orchestrator import set_orchestrator
from roboco.db.base import get_db_context, init_db
from roboco.db.tables import (
    AgentTable,
    ChannelTable,
    GroupTable,
    MessageTable,
    SessionTable,
)
from roboco.events import EventBus
from roboco.events.handlers import register_default_handlers
from roboco.models import AgentRole, ChannelType, MessageType, SessionStatus, Team
from roboco.runtime import AgentOrchestrator


class _BootstrapHolder:
    """Holder for bootstrap singleton instances."""

    orchestrator: AgentOrchestrator | None = None


logger = structlog.get_logger()


# =============================================================================
# DEFAULT CHANNELS
# =============================================================================

DEFAULT_CHANNELS = [
    # Cell channels
    {
        "slug": "backend-cell",
        "name": "Backend Cell",
        "description": "Backend development team channel",
        "channel_type": "cell",
    },
    {
        "slug": "frontend-cell",
        "name": "Frontend Cell",
        "description": "Frontend development team channel",
        "channel_type": "cell",
    },
    {
        "slug": "uxui-cell",
        "name": "UX/UI Cell",
        "description": "UX/UI design team channel",
        "channel_type": "cell",
    },
    # Cross-cell role channels
    {
        "slug": "dev-all",
        "name": "All Developers",
        "description": "Cross-cell developer discussion",
        "channel_type": "role",
    },
    {
        "slug": "qa-all",
        "name": "All QA",
        "description": "Cross-cell QA discussion",
        "channel_type": "role",
    },
    {
        "slug": "pm-all",
        "name": "All PMs",
        "description": "Cross-cell PM coordination",
        "channel_type": "role",
    },
    {
        "slug": "doc-all",
        "name": "All Documenters",
        "description": "Cross-cell documentation discussion",
        "channel_type": "role",
    },
    # Management channels
    {
        "slug": "main-pm-board",
        "name": "Main PM & Board",
        "description": "Main PM and Board communication",
        "channel_type": "management",
    },
    {
        "slug": "board-private",
        "name": "Board Private",
        "description": "Board-only discussions",
        "channel_type": "management",
    },
    # Special channels
    {
        "slug": "announcements",
        "name": "Announcements",
        "description": "Company-wide announcements (read-only for most)",
        "channel_type": "broadcast",
    },
    {
        "slug": "all-hands",
        "name": "All Hands",
        "description": "Company-wide open discussion",
        "channel_type": "broadcast",
    },
]


# =============================================================================
# DEFAULT AGENTS
# =============================================================================

DEFAULT_AGENTS: list[dict[str, Any]] = [
    # Backend Cell
    {
        "agent_id": "be-dev-1",
        "name": "Backend Developer 1",
        "role": "developer",
        "team": "backend",
    },
    {
        "agent_id": "be-dev-2",
        "name": "Backend Developer 2",
        "role": "developer",
        "team": "backend",
    },
    {"agent_id": "be-qa", "name": "Backend QA", "role": "qa", "team": "backend"},
    {"agent_id": "be-pm", "name": "Backend PM", "role": "cell_pm", "team": "backend"},
    {
        "agent_id": "be-doc",
        "name": "Backend Documenter",
        "role": "documenter",
        "team": "backend",
    },
    # Frontend Cell
    {
        "agent_id": "fe-dev-1",
        "name": "Frontend Developer 1",
        "role": "developer",
        "team": "frontend",
    },
    {
        "agent_id": "fe-dev-2",
        "name": "Frontend Developer 2",
        "role": "developer",
        "team": "frontend",
    },
    {"agent_id": "fe-qa", "name": "Frontend QA", "role": "qa", "team": "frontend"},
    {"agent_id": "fe-pm", "name": "Frontend PM", "role": "cell_pm", "team": "frontend"},
    {
        "agent_id": "fe-doc",
        "name": "Frontend Documenter",
        "role": "documenter",
        "team": "frontend",
    },
    # UX/UI Cell
    {
        "agent_id": "ux-dev",
        "name": "UX/UI Developer",
        "role": "developer",
        "team": "uxui",
    },
    {"agent_id": "ux-qa", "name": "UX/UI QA", "role": "qa", "team": "uxui"},
    {"agent_id": "ux-pm", "name": "UX/UI PM", "role": "cell_pm", "team": "uxui"},
    {
        "agent_id": "ux-doc",
        "name": "UX/UI Documenter",
        "role": "documenter",
        "team": "uxui",
    },
    # Board / Management
    {"agent_id": "main-pm", "name": "Main PM", "role": "main_pm", "team": None},
    {
        "agent_id": "product-owner",
        "name": "Product Owner",
        "role": "product_owner",
        "team": None,
    },
    {
        "agent_id": "head-marketing",
        "name": "Head of Marketing",
        "role": "head_marketing",
        "team": None,
    },
    {"agent_id": "auditor", "name": "Auditor", "role": "auditor", "team": None},
]


# =============================================================================
# CHANNEL MEMBERSHIP
# =============================================================================

CHANNEL_MEMBERSHIPS = {
    # Cell channels - cell members
    "backend-cell": ["be-dev-1", "be-dev-2", "be-qa", "be-pm", "be-doc"],
    "frontend-cell": ["fe-dev-1", "fe-dev-2", "fe-qa", "fe-pm", "fe-doc"],
    "uxui-cell": ["ux-dev", "ux-qa", "ux-pm", "ux-doc"],
    # Role channels
    "dev-all": ["be-dev-1", "be-dev-2", "fe-dev-1", "fe-dev-2", "ux-dev"],
    "qa-all": ["be-qa", "fe-qa", "ux-qa"],
    "pm-all": ["be-pm", "fe-pm", "ux-pm", "main-pm"],
    "doc-all": ["be-doc", "fe-doc", "ux-doc"],
    # Management channels
    "main-pm-board": ["main-pm", "product-owner", "head-marketing", "auditor"],
    "board-private": ["product-owner", "head-marketing", "auditor"],
    # Broadcast channels - everyone
    "announcements": [a["agent_id"] for a in DEFAULT_AGENTS],
    "all-hands": [a["agent_id"] for a in DEFAULT_AGENTS],
}

# Auditor has silent read access to all channels
AUDITOR_SILENT_ACCESS = [
    "backend-cell",
    "frontend-cell",
    "uxui-cell",
    "dev-all",
    "qa-all",
    "pm-all",
    "doc-all",
]


# =============================================================================
# BOOTSTRAP FUNCTIONS
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

        # Create agent using ORM
        agent = AgentTable(
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
    for channel_slug, members in CHANNEL_MEMBERSHIPS.items():
        channel_id = channel_ids.get(channel_slug)
        if not channel_id:
            continue

        # Get the channel
        result = await session.execute(
            select(ChannelTable).where(ChannelTable.id == UUIDType(channel_id))
        )
        channel = result.scalar_one_or_none()
        if not channel:
            continue

        # Build member and writer UUID lists
        member_uuids = []
        writer_uuids = []

        for agent_slug in members:
            db_agent_id = agent_ids.get(agent_slug)
            if db_agent_id:
                uuid = UUIDType(db_agent_id)
                member_uuids.append(uuid)
                writer_uuids.append(uuid)  # All members can write by default

        # Update channel
        channel.members = member_uuids
        channel.writers = writer_uuids

    # Add auditor silent access to specified channels
    auditor_db_id = agent_ids.get("auditor")
    if auditor_db_id:
        auditor_uuid = UUIDType(auditor_db_id)

        for channel_slug in AUDITOR_SILENT_ACCESS:
            channel_id = channel_ids.get(channel_slug)
            if not channel_id:
                continue

            result = await session.execute(
                select(ChannelTable).where(ChannelTable.id == UUIDType(channel_id))
            )
            channel = result.scalar_one_or_none()
            # Add auditor to silent_observers (read-only)
            observers = channel.silent_observers or [] if channel else []
            if channel and auditor_uuid not in observers:
                channel.silent_observers = [*observers, auditor_uuid]

    logger.info("Channel memberships configured")


# =============================================================================
# INITIAL CHANNEL MESSAGES
# =============================================================================

INITIAL_MESSAGES = {
    "announcements": {
        "agent_id": "main-pm",
        "content": """Welcome to RoboCo!

This is the official announcements channel. Company-wide updates will be posted here.

**Key Channels:**
- `#backend-cell`, `#frontend-cell`, `#uxui-cell` - Team communication
- `#dev-all`, `#qa-all`, `#pm-all`, `#doc-all` - Cross-cell role channels
- `#all-hands` - Company-wide open discussion

**Workflow:**
1. Check `roboco_task_scan()` for pending work
2. Claim tasks in your team
3. Follow the lifecycle: CLAIM → IN_PROGRESS → VERIFY → QA → DOCS → COMPLETE
4. Use your journal to track learning and decisions

Let's build something great together!
""",
    },
    "all-hands": {
        "agent_id": "main-pm",
        "content": """This is the all-hands channel for company-wide discussions.

Feel free to:
- Ask questions that span multiple teams
- Share interesting findings
- Discuss architecture decisions that affect everyone
- Celebrate wins and completed tasks

Please keep cell-specific discussions in your respective cell channels.
""",
    },
    "backend-cell": {
        "agent_id": "be-pm",
        "content": """Welcome to the Backend Cell channel!

**Team:**
- be-dev-1, be-dev-2: Backend Developers
- be-qa: Backend QA
- be-pm: Backend PM (me)
- be-doc: Backend Documenter

**Our Focus:**
- API development
- Database design
- Service architecture
- Performance optimization

Check `roboco_task_scan(team="backend")` for pending backend tasks.
""",
    },
    "frontend-cell": {
        "agent_id": "fe-pm",
        "content": """Welcome to the Frontend Cell channel!

**Team:**
- fe-dev-1, fe-dev-2: Frontend Developers
- fe-qa: Frontend QA
- fe-pm: Frontend PM (me)
- fe-doc: Frontend Documenter

**Our Focus:**
- UI development
- User experience
- Component architecture
- State management

Check `roboco_task_scan(team="frontend")` for pending frontend tasks.
""",
    },
    "uxui-cell": {
        "agent_id": "ux-pm",
        "content": """Welcome to the UX/UI Cell channel!

**Team:**
- ux-dev: UX/UI Developer
- ux-qa: UX/UI QA
- ux-pm: UX/UI PM (me)
- ux-doc: UX/UI Documenter

**Our Focus:**
- Design systems
- User research
- Prototyping
- Accessibility

Check `roboco_task_scan(team="uxui")` for pending UX/UI tasks.
""",
    },
}


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


async def run_orchestrator() -> None:
    """Start the agent orchestrator."""
    orchestrator = AgentOrchestrator(
        blueprints_dir=Path("agents/blueprints"),
    )

    await orchestrator.start()
    logger.info("Orchestrator started")

    try:
        # Keep running until interrupted
        while True:
            await asyncio.sleep(60)
            status = orchestrator.get_status_summary()
            logger.info(
                "Orchestrator status",
                total=status["total"],
                by_state=status["by_state"],
            )
    except asyncio.CancelledError:
        pass
    finally:
        await orchestrator.stop()


async def main(
    skip_db: bool = False,
    skip_orchestrator: bool = False,
    spawn_agents: list[str] | None = None,
) -> None:
    """
    Main bootstrap entry point.

    Args:
        skip_db: Skip database initialization
        skip_orchestrator: Skip starting orchestrator
        spawn_agents: List of agent IDs to spawn immediately
    """
    logger.info("RoboCo Bootstrap starting...")

    if not skip_db:
        await bootstrap_database()

    if skip_orchestrator:
        logger.info("Orchestrator skipped, exiting")
        return

    # Initialize event bus
    event_bus = EventBus()
    await event_bus.connect()
    register_default_handlers(event_bus)
    await event_bus.start_listening()
    logger.info("Event bus initialized")

    # Initialize orchestrator
    orchestrator = AgentOrchestrator(
        blueprints_dir=Path("agents/blueprints"),
    )
    _BootstrapHolder.orchestrator = orchestrator

    # Set orchestrator in API routes
    set_orchestrator(orchestrator)

    await orchestrator.start()

    # Spawn requested agents
    if spawn_agents:
        startup_prompt = (
            "You are starting up. Call roboco_task_scan() to look for pending work."
        )
        for agent_id in spawn_agents:
            try:
                await orchestrator.spawn_agent(
                    agent_id=agent_id,
                    initial_prompt=startup_prompt,
                )
            except Exception as e:
                logger.error("Failed to spawn agent", agent_id=agent_id, error=str(e))

    try:
        # Keep running
        while True:
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        pass
    finally:
        await orchestrator.stop()
        await event_bus.disconnect()
        _BootstrapHolder.orchestrator = None
        logger.info("RoboCo shutdown complete")


def cli() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="RoboCo Bootstrap")
    parser.add_argument(
        "--skip-db",
        action="store_true",
        help="Skip database initialization",
    )
    parser.add_argument(
        "--skip-orchestrator",
        action="store_true",
        help="Skip starting the orchestrator",
    )
    parser.add_argument(
        "--spawn",
        nargs="*",
        help="Agent IDs to spawn immediately",
    )
    parser.add_argument(
        "--db-only",
        action="store_true",
        help="Only initialize database, then exit",
    )

    args = parser.parse_args()

    if args.db_only:
        asyncio.run(bootstrap_database())
    else:
        asyncio.run(
            main(
                skip_db=args.skip_db,
                skip_orchestrator=args.skip_orchestrator,
                spawn_agents=args.spawn,
            )
        )


if __name__ == "__main__":
    cli()
