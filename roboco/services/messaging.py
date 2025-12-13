"""
Messaging Service

Comprehensive service for managing communication:
- Channels (top-level containers)
- Groups (role-based containers within channels)
- Sessions (message boundaries)
- Messages (individual communications)

Implements the communication model from HOMELAB_TEAM_V0.md.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.db.tables import (
    ChannelTable,
    GroupTable,
    MessageTable,
    SessionTable,
)
from roboco.enforcement.channel_access import validate_channel_access
from roboco.events.bus import Event, EventType, get_event_bus
from roboco.models.base import (
    AgentRole,
    ChannelType,
    MessageType,
    SessionStatus,
)

logger = structlog.get_logger()


# =============================================================================
# REQUEST DATACLASSES
# =============================================================================


@dataclass
class ChannelCreateRequest:
    """Request to create a channel."""

    name: str
    slug: str
    channel_type: ChannelType
    description: str | None = None
    members: list[UUID] | None = None
    writers: list[UUID] | None = None
    silent_observers: list[UUID] | None = None
    is_private: bool = False


@dataclass
class GroupCreateRequest:
    """Request to create a group."""

    name: str
    channel_id: UUID
    allowed_roles: list[AgentRole] | None = None
    hierarchy_level: int = 4
    members: list[UUID] | None = None


@dataclass
class SessionCreateRequest:
    """Request to create a session."""

    group_id: UUID
    max_message_count: int | None = 100
    max_content_length: int | None = 50000
    timeout_seconds: int = 300


@dataclass
class MessageCreateRequest:
    """Request to send a message."""

    agent_id: UUID
    session_id: UUID
    content: str
    message_type: MessageType = MessageType.DIALOGUE
    reply_to: UUID | None = None
    mentions: list[UUID] | None = None
    task_id: UUID | None = None
    commit_ref: str | None = None


# =============================================================================
# MESSAGING SERVICE
# =============================================================================


class MessagingService:
    """
    Service for managing all messaging operations.

    Provides:
    - Channel CRUD with access control
    - Group management within channels
    - Session lifecycle with automatic boundaries
    - Message CRUD with edit history

    Usage:
        service = MessagingService(db_session)

        # Create channel
        channel = await service.create_channel(ChannelCreateRequest(...))

        # Send message (handles session automatically)
        message = await service.send_message(MessageCreateRequest(...))
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.log = logger.bind(service="messaging")

    # =========================================================================
    # CHANNEL OPERATIONS (TASK-013)
    # =========================================================================

    async def create_channel(self, req: ChannelCreateRequest) -> ChannelTable:
        """
        Create a new channel.

        Args:
            req: Channel creation request

        Returns:
            Created channel

        Raises:
            ValueError: If slug already exists
        """
        # Check slug uniqueness
        existing = await self.session.execute(
            select(ChannelTable).where(ChannelTable.slug == req.slug)
        )
        if existing.scalar_one_or_none():
            raise ValueError(f"Channel with slug '{req.slug}' already exists")

        channel = ChannelTable(
            name=req.name,
            slug=req.slug,
            type=req.channel_type,
            description=req.description,
            members=list(req.members) if req.members else [],
            writers=list(req.writers) if req.writers else [],
            silent_observers=list(req.silent_observers) if req.silent_observers else [],
            is_private=req.is_private,
        )

        self.session.add(channel)
        await self.session.flush()

        self.log.info(
            "Channel created",
            channel_id=str(channel.id),
            slug=req.slug,
            type=req.channel_type.value,
        )
        return channel

    async def get_channel(self, channel_id: UUID) -> ChannelTable | None:
        """Get a channel by ID."""
        result = await self.session.execute(
            select(ChannelTable).where(ChannelTable.id == channel_id)
        )
        return result.scalar_one_or_none()

    async def get_channel_by_slug(self, slug: str) -> ChannelTable | None:
        """Get a channel by slug."""
        result = await self.session.execute(
            select(ChannelTable).where(ChannelTable.slug == slug)
        )
        return result.scalar_one_or_none()

    async def list_channels_for_agent(
        self,
        agent_id: UUID,
        include_archived: bool = False,
    ) -> list[ChannelTable]:
        """List channels an agent can access (member or silent observer)."""
        query = select(ChannelTable).where(
            (ChannelTable.members.contains([agent_id]))
            | (ChannelTable.silent_observers.contains([agent_id]))
        )

        if not include_archived:
            query = query.where(ChannelTable.is_archived.is_(False))

        query = query.order_by(ChannelTable.name)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def add_channel_member(
        self,
        channel_id: UUID,
        agent_id: UUID,
        can_write: bool = True,
    ) -> ChannelTable:
        """Add a member to a channel."""
        channel = await self.get_channel(channel_id)
        if not channel:
            raise ValueError(f"Channel {channel_id} not found")

        # Add to members
        if agent_id not in channel.members:
            channel.members = [*channel.members, agent_id]

        # Add to writers if requested
        if can_write and agent_id not in channel.writers:
            channel.writers = [*channel.writers, agent_id]

        await self.session.flush()

        self.log.info(
            "Member added to channel",
            channel_id=str(channel_id),
            agent_id=str(agent_id),
            can_write=can_write,
        )
        return channel

    async def remove_channel_member(
        self,
        channel_id: UUID,
        agent_id: UUID,
    ) -> ChannelTable:
        """Remove a member from a channel."""
        channel = await self.get_channel(channel_id)
        if not channel:
            raise ValueError(f"Channel {channel_id} not found")

        channel.members = [m for m in channel.members if m != agent_id]
        channel.writers = [w for w in channel.writers if w != agent_id]

        await self.session.flush()

        self.log.info(
            "Member removed from channel",
            channel_id=str(channel_id),
            agent_id=str(agent_id),
        )
        return channel

    async def archive_channel(self, channel_id: UUID) -> ChannelTable:
        """Archive a channel."""
        channel = await self.get_channel(channel_id)
        if not channel:
            raise ValueError(f"Channel {channel_id} not found")

        channel.is_archived = True
        await self.session.flush()

        self.log.info("Channel archived", channel_id=str(channel_id))
        return channel

    # =========================================================================
    # GROUP OPERATIONS
    # =========================================================================

    async def create_group(self, req: GroupCreateRequest) -> GroupTable:
        """Create a group within a channel."""
        # Verify channel exists
        channel = await self.get_channel(req.channel_id)
        if not channel:
            raise ValueError(f"Channel {req.channel_id} not found")

        group = GroupTable(
            name=req.name,
            channel_id=req.channel_id,
            allowed_roles=list(req.allowed_roles) if req.allowed_roles else [],
            hierarchy_level=req.hierarchy_level,
            members=list(req.members) if req.members else [],
        )

        self.session.add(group)

        # Update channel group count
        channel.group_count += 1

        await self.session.flush()

        self.log.info(
            "Group created",
            group_id=str(group.id),
            channel_id=str(req.channel_id),
            name=req.name,
        )
        return group

    async def get_group(self, group_id: UUID) -> GroupTable | None:
        """Get a group by ID."""
        result = await self.session.execute(
            select(GroupTable).where(GroupTable.id == group_id)
        )
        return result.scalar_one_or_none()

    async def list_groups_in_channel(self, channel_id: UUID) -> list[GroupTable]:
        """List all groups in a channel."""
        result = await self.session.execute(
            select(GroupTable)
            .where(GroupTable.channel_id == channel_id)
            .order_by(GroupTable.hierarchy_level, GroupTable.name)
        )
        return list(result.scalars().all())

    # =========================================================================
    # SESSION OPERATIONS (TASK-015)
    # =========================================================================

    async def create_session(self, req: SessionCreateRequest) -> SessionTable:
        """
        Create a new session in a group.

        Sets the session as the group's active session.
        """
        # Verify group exists
        group = await self.get_group(req.group_id)
        if not group:
            raise ValueError(f"Group {req.group_id} not found")

        # Close existing active session if any
        if group.active_session_id:
            session_id = cast("UUID", group.active_session_id)
            await self.close_session(session_id, "New session started")

        session = SessionTable(
            group_id=req.group_id,
            max_message_count=req.max_message_count,
            max_content_length=req.max_content_length,
            timeout_seconds=req.timeout_seconds,
            status=SessionStatus.ACTIVE,
        )

        self.session.add(session)

        # Update group
        group.active_session_id = session.id
        group.total_sessions += 1
        group.last_activity = datetime.now(UTC)

        await self.session.flush()

        # Publish event
        try:
            bus = get_event_bus()
            if bus._redis:
                await bus.publish(
                    Event(
                        type=EventType.SESSION_CREATED,
                        data={
                            "session_id": str(session.id),
                            "group_id": str(req.group_id),
                        },
                    )
                )
        except Exception as e:
            self.log.warning("Failed to publish session event", error=str(e))

        self.log.info(
            "Session created",
            session_id=str(session.id),
            group_id=str(req.group_id),
        )
        return session

    async def get_session(self, session_id: UUID) -> SessionTable | None:
        """Get a session by ID."""
        result = await self.session.execute(
            select(SessionTable).where(SessionTable.id == session_id)
        )
        return result.scalar_one_or_none()

    async def close_session(
        self,
        session_id: UUID,
        reason: str = "Manual close",
    ) -> SessionTable | None:
        """Close a session."""
        session = await self.get_session(session_id)
        if not session:
            return None

        if session.status != SessionStatus.ACTIVE:
            return session  # Already closed

        session.status = SessionStatus.CLOSED
        session.closed_at = datetime.now(UTC)

        # Clear group's active session
        group = await self.get_group(cast("UUID", session.group_id))
        if group and group.active_session_id == session_id:
            group.active_session_id = None

        await self.session.flush()

        # Publish event
        try:
            bus = get_event_bus()
            if bus._redis:
                await bus.publish(
                    Event(
                        type=EventType.SESSION_CLOSED,
                        data={
                            "session_id": str(session_id),
                            "reason": reason,
                        },
                    )
                )
        except Exception as e:
            self.log.warning("Failed to publish session event", error=str(e))

        self.log.info(
            "Session closed",
            session_id=str(session_id),
            reason=reason,
        )
        return session

    async def get_or_create_active_session(
        self,
        group_id: UUID,
    ) -> SessionTable:
        """Get the active session for a group, or create one if none exists."""
        group = await self.get_group(group_id)
        if not group:
            raise ValueError(f"Group {group_id} not found")

        # Return active session if exists
        if group.active_session_id:
            session = await self.get_session(cast("UUID", group.active_session_id))
            if session and session.status == SessionStatus.ACTIVE:
                return session

        # Create new session
        return await self.create_session(SessionCreateRequest(group_id=group_id))

    def _check_session_boundaries(self, session: SessionTable) -> bool:
        """Check if session has exceeded boundaries. Returns True if should close."""
        # Check message count
        if (
            session.max_message_count
            and session.message_count >= session.max_message_count
        ):
            return True

        # Check content length
        return bool(
            session.max_content_length
            and session.total_content_length >= session.max_content_length
        )

    # =========================================================================
    # MESSAGE OPERATIONS (TASK-014)
    # =========================================================================

    async def _get_message_context(
        self,
        session_id: UUID,
    ) -> tuple[SessionTable, GroupTable, ChannelTable]:
        """Get session, group, and channel for sending a message."""
        session = await self.get_session(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        if session.status != SessionStatus.ACTIVE:
            raise ValueError("Session is not active")

        group = await self.get_group(cast("UUID", session.group_id))
        if not group:
            raise ValueError(f"Group {session.group_id} not found")

        channel = await self.get_channel(cast("UUID", group.channel_id))
        if not channel:
            raise ValueError(f"Channel {group.channel_id} not found")

        return session, group, channel

    async def _validate_reply_target(
        self,
        reply_to: UUID,
        session_id: UUID,
    ) -> None:
        """Validate reply target exists in session."""
        reply_msg = await self.get_message(reply_to)
        if not reply_msg or reply_msg.session_id != session_id:
            raise ValueError("Reply target not found in this session")

    def _update_message_stats(
        self,
        session: SessionTable,
        group: GroupTable,
        channel: ChannelTable,
        content_length: int,
    ) -> None:
        """Update statistics after sending a message."""
        now = datetime.now(UTC)
        session.message_count += 1
        session.total_content_length += content_length
        session.last_activity_at = now
        group.total_messages += 1
        group.last_activity = now
        channel.message_count += 1
        channel.last_activity = now

    async def send_message(
        self,
        req: MessageCreateRequest,
        agent_slug: str | None = None,
    ) -> MessageTable:
        """
        Send a message to a session.

        Args:
            req: Message creation request
            agent_slug: Agent slug for channel access validation (optional)

        Returns:
            Created message

        Raises:
            ValueError: If session not found or not active
            ChannelAccessDeniedError: If agent cannot write to channel
        """
        session, group, channel = await self._get_message_context(req.session_id)

        if agent_slug:
            validate_channel_access(agent_slug, channel.slug, "write")
        if req.reply_to:
            await self._validate_reply_target(req.reply_to, req.session_id)

        content_length = len(req.content)
        message = MessageTable(
            agent_id=req.agent_id,
            channel_id=channel.id,
            group_id=group.id,
            session_id=session.id,
            type=req.message_type,
            content=req.content,
            content_length=content_length,
            is_reply=req.reply_to is not None,
            reply_to=req.reply_to,
            mentions=list(req.mentions) if req.mentions else [],
            task_id=req.task_id,
            commit_ref=req.commit_ref,
        )
        self.session.add(message)
        self._update_message_stats(session, group, channel, content_length)
        await self.session.flush()

        if self._check_session_boundaries(session):
            await self.close_session(cast("UUID", session.id), "Boundary exceeded")

        self.log.info(
            "Message sent",
            message_id=str(message.id),
            session_id=str(session.id),
            agent_id=str(req.agent_id),
            type=req.message_type.value,
        )
        return message

    async def get_message(self, message_id: UUID) -> MessageTable | None:
        """Get a message by ID."""
        result = await self.session.execute(
            select(MessageTable).where(MessageTable.id == message_id)
        )
        return result.scalar_one_or_none()

    async def get_messages(
        self,
        session_id: UUID,
        before: datetime | None = None,
        after: datetime | None = None,
        message_type: MessageType | None = None,
        limit: int = 50,
    ) -> tuple[list[MessageTable], bool]:
        """
        Get messages from a session.

        Args:
            session_id: Session to get messages from
            before: Get messages before this timestamp
            after: Get messages after this timestamp
            message_type: Filter by message type
            limit: Maximum messages to return

        Returns:
            Tuple of (messages, has_more)
        """
        query = select(MessageTable).where(MessageTable.session_id == session_id)

        if before:
            query = query.where(MessageTable.timestamp < before)
        if after:
            query = query.where(MessageTable.timestamp > after)
        if message_type:
            query = query.where(MessageTable.type == message_type)

        # Get one extra to check if there are more
        query = query.order_by(MessageTable.timestamp.desc()).limit(limit + 1)

        result = await self.session.execute(query)
        messages = list(result.scalars().all())

        has_more = len(messages) > limit
        if has_more:
            messages = messages[:limit]

        return messages, has_more

    async def edit_message(
        self,
        message_id: UUID,
        agent_id: UUID,
        new_content: str,
        edit_reason: str | None = None,
    ) -> MessageTable:
        """
        Edit a message.

        Only the author can edit their own messages.

        Args:
            message_id: Message to edit
            agent_id: Agent requesting the edit (must be author)
            new_content: New content
            edit_reason: Optional reason for the edit

        Returns:
            Updated message

        Raises:
            ValueError: If message not found or agent is not author
        """
        message = await self.get_message(message_id)
        if not message:
            raise ValueError(f"Message {message_id} not found")

        if message.agent_id != agent_id:
            raise ValueError("Only the author can edit this message")

        # Store edit history
        edit_entry = {
            "edited_at": datetime.now(UTC).isoformat(),
            "previous_content": message.content,
            "edit_reason": edit_reason,
        }
        message.edit_history = [*message.edit_history, edit_entry]

        # Calculate content length delta
        old_length = message.content_length
        new_length = len(new_content)
        delta = new_length - old_length

        # Update message
        message.content = new_content
        message.content_length = new_length
        message.edited_at = datetime.now(UTC)

        # Update session total content length
        session = await self.get_session(cast("UUID", message.session_id))
        if session:
            session.total_content_length += delta

        await self.session.flush()

        self.log.info(
            "Message edited",
            message_id=str(message_id),
            agent_id=str(agent_id),
        )
        return message

    async def delete_message(
        self,
        message_id: UUID,
        agent_id: UUID,
    ) -> bool:
        """
        Soft delete a message.

        Only the author can delete their own messages.

        Args:
            message_id: Message to delete
            agent_id: Agent requesting deletion (must be author)

        Returns:
            True if deleted

        Raises:
            ValueError: If message not found or agent is not author
        """
        message = await self.get_message(message_id)
        if not message:
            raise ValueError(f"Message {message_id} not found")

        if message.agent_id != agent_id:
            raise ValueError("Only the author can delete this message")

        # Soft delete - mark content as deleted
        message.content = "[deleted]"
        message.edited_at = datetime.now(UTC)

        await self.session.flush()

        self.log.info(
            "Message deleted",
            message_id=str(message_id),
            agent_id=str(agent_id),
        )
        return True


# =============================================================================
# SERVICE FACTORY
# =============================================================================


def get_messaging_service(session: AsyncSession) -> MessagingService:
    """Factory function to create a MessagingService instance."""
    return MessagingService(session)
