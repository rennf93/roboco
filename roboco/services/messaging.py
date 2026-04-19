"""
Messaging Service

Comprehensive service for managing communication:
- Channels (top-level containers)
- Groups (role-based containers within channels)
- Sessions (message boundaries)
- Messages (individual communications)

Implements the communication model.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any, ClassVar, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from roboco.db.tables import (
    ChannelTable,
    GroupTable,
    MessageTable,
    NotificationTable,
    SessionTable,
    SessionTaskTable,
)
from roboco.enforcement import validate_channel_access
from roboco.events import Event, EventType, get_event_bus
from roboco.models.base import (
    MessageType,
    NotificationPriority,
    NotificationType,
    SessionStatus,
)
from roboco.models.messaging import (
    ChannelCreateRequest,
    GroupCreateRequest,
    MessageCreateRequest,
    SessionCreateRequest,
)
from roboco.models.session import (
    SessionForTasksCreate,
    SessionTaskRelationshipType,
)
from roboco.services.base import BaseService, ConflictError, NotFoundError
from roboco.utils.converters import require_uuid, to_python_uuid

# =============================================================================
# MESSAGING SERVICE
# =============================================================================


class MessagingService(BaseService):
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

    service_name: ClassVar[str] = "messaging"
    _background_tasks: ClassVar[set[asyncio.Task[Any]]] = set()

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

    async def get_or_create_channel_by_slug(self, slug: str) -> ChannelTable | None:
        """Get a channel by slug, auto-creating from config if needed.

        If the channel doesn't exist in the database but is defined in
        DEFAULT_CHANNELS, it will be automatically created.

        This allows the system to work without requiring explicit database
        seeding while still validating that only configured channels are used.

        Args:
            slug: Channel slug to look up

        Returns:
            Channel if found or created, None if not a valid channel
        """
        # First try database
        channel = await self.get_channel_by_slug(slug)
        if channel:
            return channel

        # Not in DB - check if it's a valid channel from config
        from roboco.models.base import ChannelType
        from roboco.seeds import DEFAULT_CHANNELS

        channel_data = next(
            (c for c in DEFAULT_CHANNELS if c["slug"] == slug),
            None,
        )
        if not channel_data:
            return None

        # Auto-create from config
        channel = ChannelTable(
            name=channel_data["name"],
            slug=channel_data["slug"],
            type=ChannelType(channel_data["channel_type"]),
            description=channel_data.get("description", ""),
        )
        self.session.add(channel)
        await self.session.flush()

        self.log.info(
            "Channel auto-created from config",
            slug=slug,
            type=channel_data["channel_type"],
        )
        return channel

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
            scope=req.scope,
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
            if bus.is_connected():
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

    async def sweep_timed_out_sessions(self) -> int:
        """Close sessions whose inactivity exceeds `timeout_seconds`.

        `SessionTable.timeout_seconds` and `SessionTable.max_time_window` were
        stored but never enforced; sessions stayed ACTIVE indefinitely.
        The orchestrator's session-sweeper loop calls this periodically.

        Returns the number of sessions closed.
        """

        now = datetime.now(UTC)
        result = await self.session.execute(
            select(SessionTable).where(SessionTable.status == SessionStatus.ACTIVE)
        )
        active_sessions = list(result.scalars().all())

        closed = 0
        for session in active_sessions:
            last_active = session.last_activity_at or session.started_at
            idle = (now - last_active).total_seconds()

            timeout_exceeded = (
                session.timeout_seconds is not None
                and idle >= session.timeout_seconds
            )
            window_exceeded = (
                session.max_time_window is not None
                and (now - session.started_at) >= session.max_time_window
            )
            if not (timeout_exceeded or window_exceeded):
                continue

            reason = "Inactivity timeout" if timeout_exceeded else "Max time window"
            await self.close_session(cast("UUID", session.id), reason)
            closed += 1

        if closed:
            self.log.info("Session sweeper closed sessions", count=closed)
        return closed

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
            if bus.is_connected():
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

    # =========================================================================
    # SESSION-TASK LINKING OPERATIONS
    # =========================================================================

    async def link_session_to_task(
        self,
        session_id: UUID,
        task_id: UUID,
        added_by: UUID,
        is_primary: bool = False,
        relationship_type: SessionTaskRelationshipType = (
            SessionTaskRelationshipType.DISCUSSION
        ),
    ) -> SessionTaskTable:
        """
        Link a session to a task.

        Args:
            session_id: Session to link
            task_id: Task to link
            added_by: PM who created this link
            is_primary: Mark as primary discussion session for this task
            relationship_type: Type of relationship

        Returns:
            Created link

        Raises:
            NotFoundError: If session not found
            ConflictError: If link already exists or primary constraint violated
        """
        # Verify session exists
        session = await self.get_session(session_id)
        if not session:
            raise NotFoundError(f"Session {session_id} not found")

        # Check if link already exists
        existing = await self.session.execute(
            select(SessionTaskTable).where(
                SessionTaskTable.session_id == session_id,
                SessionTaskTable.task_id == task_id,
            )
        )
        if existing.scalar_one_or_none():
            raise ConflictError(
                f"Session {session_id} is already linked to task {task_id}"
            )

        # If marking as primary, check if task already has a primary session
        if is_primary:
            existing_primary = await self.session.execute(
                select(SessionTaskTable).where(
                    SessionTaskTable.task_id == task_id,
                    SessionTaskTable.is_primary.is_(True),
                )
            )
            if existing_primary.scalar_one_or_none():
                raise ConflictError(f"Task {task_id} already has a primary session")

        # Handle both enum and string (RobocoBase uses use_enum_values=True)
        rel_value = getattr(relationship_type, "value", relationship_type)

        link = SessionTaskTable(
            session_id=session_id,
            task_id=task_id,
            is_primary=is_primary,
            relationship_type=rel_value,
            added_by=added_by,
        )

        self.session.add(link)
        await self.session.flush()

        self.log.info(
            "Session linked to task",
            session_id=str(session_id),
            task_id=str(task_id),
            is_primary=is_primary,
            relationship_type=rel_value,
        )
        return link

    async def unlink_session_from_task(
        self,
        session_id: UUID,
        task_id: UUID,
    ) -> bool:
        """
        Remove a session-task link.

        Args:
            session_id: Session to unlink
            task_id: Task to unlink

        Returns:
            True if link was removed, False if not found
        """
        result = await self.session.execute(
            select(SessionTaskTable).where(
                SessionTaskTable.session_id == session_id,
                SessionTaskTable.task_id == task_id,
            )
        )
        link = result.scalar_one_or_none()

        if not link:
            return False

        await self.session.delete(link)
        await self.session.flush()

        self.log.info(
            "Session unlinked from task",
            session_id=str(session_id),
            task_id=str(task_id),
        )
        return True

    async def get_sessions_for_task(
        self,
        task_id: UUID,
        relationship_type: SessionTaskRelationshipType | None = None,
    ) -> list[SessionTaskTable]:
        """
        Get all sessions linked to a task.

        Args:
            task_id: Task to get sessions for
            relationship_type: Filter by relationship type

        Returns:
            List of session-task links (with session→group→channel loaded)
        """
        query = (
            select(SessionTaskTable)
            .where(SessionTaskTable.task_id == task_id)
            .options(
                joinedload(SessionTaskTable.session)
                .joinedload(SessionTable.group)
                .joinedload(GroupTable.channel)
            )
        )

        if relationship_type:
            rel_value = getattr(relationship_type, "value", relationship_type)
            query = query.where(SessionTaskTable.relationship_type == rel_value)

        query = query.order_by(SessionTaskTable.added_at.desc())
        result = await self.session.execute(query)
        return list(result.scalars().unique().all())

    async def get_primary_session_for_task(
        self,
        task_id: UUID,
    ) -> SessionTaskTable | None:
        """
        Get the primary session for a task.

        Args:
            task_id: Task to get primary session for

        Returns:
            Primary session-task link, or None if no primary session
        """
        result = await self.session.execute(
            select(SessionTaskTable).where(
                SessionTaskTable.task_id == task_id,
                SessionTaskTable.is_primary.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def get_tasks_for_session(
        self,
        session_id: UUID,
    ) -> list[SessionTaskTable]:
        """
        Get all tasks linked to a session.

        Args:
            session_id: Session to get tasks for

        Returns:
            List of session-task links (with task relationship loaded)
        """
        result = await self.session.execute(
            select(SessionTaskTable)
            .where(SessionTaskTable.session_id == session_id)
            .order_by(SessionTaskTable.added_at.desc())
        )
        return list(result.scalars().all())

    async def _resolve_group_from_parent_tasks(
        self,
        task_ids: list[UUID],
    ) -> GroupTable | None:
        """
        Resolve group by looking at parent tasks' sessions.

        When creating a session for subtasks, inherit the group from
        the parent task's session. This maintains proper hierarchy.

        Args:
            task_ids: Task IDs to check for parent sessions

        Returns:
            Group from parent task's session, or None if no parent has a session
        """
        from roboco.db.tables import TaskTable

        for task_id in task_ids:
            # Get the task to find its parent
            task_result = await self.session.execute(
                select(TaskTable).where(TaskTable.id == task_id)
            )
            task = task_result.scalar_one_or_none()

            if not task or not task.parent_task_id:
                continue

            # Find parent task's primary session
            parent_session_link = await self.session.execute(
                select(SessionTaskTable)
                .where(
                    SessionTaskTable.task_id == task.parent_task_id,
                    SessionTaskTable.is_primary.is_(True),
                )
                .options(
                    selectinload(SessionTaskTable.session).selectinload(
                        SessionTable.group
                    )
                )
            )
            link = parent_session_link.scalar_one_or_none()

            if link and link.session and link.session.group:
                self.log.info(
                    "Inherited group from parent task's session",
                    task_id=str(task_id),
                    parent_task_id=str(task.parent_task_id),
                    group_id=str(link.session.group.id),
                    group_name=link.session.group.name,
                )
                return link.session.group

        return None

    async def create_session_for_tasks(
        self,
        req: SessionForTasksCreate,
        pm_agent_id: UUID,
    ) -> tuple[SessionTable, list[SessionTaskTable]]:
        """
        Create a new session linked to one or more tasks (PM operation).

        This is the main entry point for PMs to create work sessions.

        Args:
            req: Session creation request with task IDs
            pm_agent_id: PM agent creating the session

        Returns:
            Tuple of (created session, list of created links)

        Raises:
            NotFoundError: If channel not found
            ValueError: If no groups found in channel
        """
        # Get channel
        channel = await self.get_channel_by_slug(req.channel_slug)
        if not channel:
            raise NotFoundError(f"Channel '{req.channel_slug}' not found")

        # Resolve group: explicit > inherited from parent > fallback to first
        group: GroupTable | None = None

        if req.group_id:
            # Explicit group_id provided
            group_result = await self.session.execute(
                select(GroupTable).where(GroupTable.id == req.group_id)
            )
            group = group_result.scalar_one_or_none()
            if not group:
                raise NotFoundError(f"Group '{req.group_id}' not found")
        else:
            # Try to inherit group from parent task's session
            group = await self._resolve_group_from_parent_tasks(req.task_ids)

            if not group:
                # Fall back to first group in channel (last resort)
                groups = await self.list_groups_in_channel(cast("UUID", channel.id))
                if not groups:
                    raise ValueError(
                        f"No groups found in channel '{req.channel_slug}'. "
                        "Create a group first or specify group_id."
                    )
                group = groups[0]
                self.log.warning(
                    "Session created without explicit group, using first group",
                    channel_slug=req.channel_slug,
                    group_name=group.name,
                    task_ids=[str(t) for t in req.task_ids],
                )

        # Create session with config and scope
        session_req = SessionCreateRequest(
            group_id=cast("UUID", group.id),
            max_message_count=(req.config.max_message_count if req.config else None),
            max_content_length=(req.config.max_content_length if req.config else None),
            timeout_seconds=(req.config.timeout_seconds if req.config else 300),
            scope=req.scope,
        )
        session = await self.create_session(session_req)

        # Link all tasks
        links: list[SessionTaskTable] = []
        for i, task_id in enumerate(req.task_ids):
            # First task is primary
            is_primary = i == 0
            link = await self.link_session_to_task(
                session_id=cast("UUID", session.id),
                task_id=task_id,
                added_by=pm_agent_id,
                is_primary=is_primary,
                relationship_type=req.relationship_type,
            )
            links.append(link)

        self.log.info(
            "Session created for tasks",
            session_id=str(session.id),
            task_count=len(req.task_ids),
            channel_slug=req.channel_slug,
            pm_agent_id=str(pm_agent_id),
        )
        return session, links

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

    async def _notify_mentions(
        self,
        message: MessageTable,
        sender_id: UUID,
        channel_slug: str,
    ) -> None:
        """
        Create and deliver notifications for mentioned agents.

        Publishes NOTIFICATION_SENT events to Redis Streams for real-time
        delivery via the WebSocket bridge.
        """
        if not message.mentions:
            return

        # Lazy import to avoid circular dependency
        from roboco.services.notification_delivery import (
            get_notification_delivery_service,
        )

        delivery_service = get_notification_delivery_service(self.session)

        for mentioned_id in message.mentions:
            # Don't notify yourself
            if mentioned_id == sender_id:
                continue

            # Create mention notification
            notification = NotificationTable(
                type=NotificationType.MENTION,
                priority=NotificationPriority.NORMAL,
                from_agent=sender_id,
                to_agents=[mentioned_id],
                subject=f"You were mentioned in #{channel_slug}",
                body=message.content[:500],  # Truncate for notification
                related_task_id=message.task_id,
            )
            self.session.add(notification)
            await self.session.flush()

            # Deliver via Redis Streams -> WebSocket
            await delivery_service.deliver(require_uuid(notification.id))

            self.log.debug(
                "Mention notification sent",
                mentioned_id=str(mentioned_id),
                message_id=str(message.id),
            )

    async def _index_message_async(self, message: MessageTable) -> None:
        """Index message in RAG system (fire-and-forget)."""
        from roboco.models.optimal import IndexConversationParams
        from roboco.services.optimal import get_optimal_service

        try:
            optimal = await get_optimal_service()
            await optimal.index_conversation(
                IndexConversationParams(
                    content=message.content,
                    channel_id=require_uuid(message.channel_id),
                    session_id=require_uuid(message.session_id),
                    agent_id=require_uuid(message.agent_id),
                    task_id=to_python_uuid(message.task_id),
                    message_type=message.type.value if message.type else None,
                )
            )
            self.log.debug("Message indexed", message_id=str(message.id))
        except Exception as e:
            self.log.warning(
                "Failed to index message",
                message_id=str(message.id),
                error=str(e),
            )

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

        # Notify mentioned agents via Redis Streams
        await self._notify_mentions(message, req.agent_id, channel.slug)

        # Index message in RAG (fire-and-forget)
        bg_task = asyncio.create_task(self._index_message_async(message))
        self._background_tasks.add(bg_task)
        bg_task.add_done_callback(self._background_tasks.discard)

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
