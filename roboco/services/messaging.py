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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from roboco.config import settings
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


@dataclass(frozen=True)
class ApiSessionCreate:
    """Service-side view of the API's session-create request.

    Keeps api/schemas types from leaking into the service layer; routes
    translate their pydantic model into this dataclass at the boundary.
    """

    group_id: UUID
    max_time_window_minutes: int | None
    max_message_count: int | None
    max_content_length: int | None
    timeout_seconds: int | None


def _minutes_to_timedelta(value: int | None) -> timedelta | None:
    return timedelta(minutes=value) if value is not None else None


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

    async def list_channels_paginated(
        self,
        *,
        accessible_slugs: list[str],
        include_archived: bool,
        page: int,
        page_size: int,
    ) -> tuple[list[ChannelTable], int]:
        """Return (channels, total) for channels filtered to `accessible_slugs`.

        Two queries so the route can expose an accurate total even when
        pagination clips the window. Route passes the slug set it computed
        from `PermissionService`; the DB work stays here.
        """
        base = select(ChannelTable).where(ChannelTable.slug.in_(accessible_slugs))
        if not include_archived:
            base = base.where(ChannelTable.is_archived.is_(False))

        from sqlalchemy import func

        count_query = select(func.count(ChannelTable.id)).where(
            ChannelTable.slug.in_(accessible_slugs)
        )
        if not include_archived:
            count_query = count_query.where(ChannelTable.is_archived.is_(False))
        count_result = await self.session.execute(count_query)
        total = count_result.scalar() or 0

        offset = (page - 1) * page_size
        base = base.order_by(ChannelTable.name).offset(offset).limit(page_size)
        result = await self.session.execute(base)
        return list(result.scalars().all()), total

    async def get_channel_with_groups_or_raise(self, channel_id: UUID) -> ChannelTable:
        """Return a channel with its groups eager-loaded; raise if missing."""
        result = await self.session.execute(
            select(ChannelTable)
            .where(ChannelTable.id == channel_id)
            .options(selectinload(ChannelTable.groups))
        )
        channel = result.scalar_one_or_none()
        if not channel:
            raise NotFoundError(resource_type="Channel", resource_id=str(channel_id))
        return channel

    async def get_channel_or_raise(self, channel_id: UUID) -> ChannelTable:
        """Return a channel by id or raise NotFoundError."""
        channel = await self.get_channel(channel_id)
        if not channel:
            raise NotFoundError(resource_type="Channel", resource_id=str(channel_id))
        return channel

    async def update_channel_fields(
        self,
        *,
        channel_id: UUID,
        fields: dict[str, Any],
    ) -> ChannelTable:
        """Apply a subset of fields to a channel.

        Keeps the setattr loop out of the route module. `fields` is a plain
        dict of column → new value; unknown keys are ignored to keep the
        service tolerant of incidental extras on the API side.
        """
        channel = await self.get_channel_or_raise(channel_id)
        # Mirrors the pre-refactor CHANNEL_UPDATE_FIELDS allowlist. Membership
        # (members/writers/silent_observers) is mutated via the dedicated
        # add/remove endpoints, not via PATCH.
        allowed = {
            "name",
            "description",
            "topic",
            "is_archived",
            "allow_threads",
            "allow_reactions",
            "message_retention_days",
            "max_message_length",
        }
        for key, value in fields.items():
            if value is None or key not in allowed:
                continue
            setattr(channel, key, value)
        await self.session.flush()
        return channel

    async def add_channel_member_or_raise(
        self,
        *,
        channel_id: UUID,
        member_id: UUID,
        can_write: bool,
    ) -> None:
        """Add a member (and optionally writer) to a channel; 404 if missing."""
        channel = await self.get_channel_or_raise(channel_id)
        if member_id not in channel.members:
            channel.members = [*channel.members, member_id]
        if can_write and member_id not in channel.writers:
            channel.writers = [*channel.writers, member_id]
        await self.session.flush()

    async def remove_channel_member_or_raise(
        self,
        *,
        channel_id: UUID,
        member_id: UUID,
    ) -> None:
        """Remove a member (and any writer entry) from a channel; 404 if missing."""
        channel = await self.get_channel_or_raise(channel_id)
        channel.members = [m for m in channel.members if m != member_id]
        channel.writers = [w for w in channel.writers if w != member_id]
        await self.session.flush()

    async def get_channel_by_slug(self, slug: str) -> ChannelTable | None:
        """Get a channel by slug.

        Strips a leading ``#`` so agents passing channel names with the
        Slack-style ``#`` prefix (e.g. ``#main-pm-board``) resolve to the
        same row stored without it.
        """
        normalized = slug.lstrip("#") if slug else slug
        result = await self.session.execute(
            select(ChannelTable).where(ChannelTable.slug == normalized)
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
        normalized = slug.lstrip("#") if slug else slug
        # First try database
        channel = await self.get_channel_by_slug(normalized)
        if channel:
            return channel

        # Not in DB - check if it's a valid channel from config
        from roboco.models.base import ChannelType
        from roboco.seeds import DEFAULT_CHANNELS

        channel_data = next(
            (c for c in DEFAULT_CHANNELS if c["slug"] == normalized),
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

    @staticmethod
    def _resolve_session_timeout(requested: int | None) -> int:
        """Resolve a session's idle-timeout, defaulting to the configurable value.

        An unset timeout previously fell through to the column default of 300s,
        which is shorter than a human conversation pause — the session was swept
        between messages and a new one opened on the next post. Resolve it to
        ``session_idle_timeout_seconds`` so human-paced chats stay on one session.
        """
        if requested is not None:
            return requested
        return settings.session_idle_timeout_seconds

    async def create_session(self, req: SessionCreateRequest) -> SessionTable:
        """
        Create a new session in a group.

        Sets the session as the group's active session.
        """
        # Verify group exists
        group = await self.get_group(req.group_id)
        if not group:
            raise ValueError(f"Group {req.group_id} not found")

        # A group has ONE live session that all participants post into. Reuse it
        # instead of closing it and opening a new one on every call — the
        # close-and-recreate pattern churned a single conversation across many
        # sessions (the smoke run showed ~one session per message, and the CEO
        # could not hold a conversation). Only open a fresh session when none is
        # currently active (the prior one closed via timeout / boundary / merge).
        if group.active_session_id:
            existing = await self.get_session(cast("UUID", group.active_session_id))
            if existing is not None and existing.status == SessionStatus.ACTIVE:
                return existing

        session = SessionTable(
            group_id=req.group_id,
            max_message_count=req.max_message_count,
            max_content_length=req.max_content_length,
            timeout_seconds=self._resolve_session_timeout(req.timeout_seconds),
            status=SessionStatus.ACTIVE,
            scope=req.scope,
        )

        self.session.add(session)
        # Flush so session.id (a flush-time uuid4 default) is materialized BEFORE we
        # link it on the group. active_session_id is a plain scalar FK with no
        # relationship, so SQLAlchemy cannot defer-populate it — assigning session.id
        # while it is still None persists active_session_id as NULL, and the group then
        # opens a brand-new session on every post instead of reusing this one.
        await self.session.flush()

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
                session.timeout_seconds is not None and idle >= session.timeout_seconds
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

    async def list_group_sessions_for_agent(
        self,
        *,
        group_id: UUID,
        agent_id: UUID,
        status_filter: SessionStatus | None,
        limit: int,
    ) -> list[SessionTable]:
        """Return sessions visible to `agent_id` within `group_id`.

        Auth: agent must be a group-channel member/observer, or hold a
        privileged role. Raises NotFoundError if the group doesn't exist
        and PermissionError if the agent can't read it. Task-links are
        eager-loaded so the route can render them without extra queries.
        """
        from roboco.services.permissions import has_privileged_access

        group_result = await self.session.execute(
            select(GroupTable)
            .where(GroupTable.id == group_id)
            .options(selectinload(GroupTable.channel))
        )
        group = group_result.scalar_one_or_none()
        if not group:
            raise NotFoundError(resource_type="Group", resource_id=str(group_id))

        channel = group.channel
        allowed = (
            agent_id in channel.members
            or agent_id in channel.silent_observers
            or await has_privileged_access(self.session, agent_id)
        )
        if not allowed:
            raise PermissionError("You don't have access to this group")

        query = (
            select(SessionTable)
            .where(SessionTable.group_id == group_id)
            .options(
                selectinload(SessionTable.task_links).selectinload(
                    SessionTaskTable.task
                )
            )
        )
        if status_filter is not None:
            query = query.where(SessionTable.status == status_filter)
        query = query.order_by(SessionTable.started_at.desc()).limit(limit)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def create_session_with_access_check(
        self,
        *,
        agent_id: UUID,
        request: "ApiSessionCreate",
    ) -> SessionTable:
        """Create a session after verifying the agent may write to the group's channel.

        Mirrors the old route-inline logic: fetches the group + channel,
        rejects if the agent isn't in `channel.writers` (unless privileged),
        closes any existing ACTIVE session, then creates the new one. Uses
        primitive fields (wrapped in `ApiSessionCreate`) so api/schemas types
        never leak into service signatures.
        """
        from roboco.services.permissions import has_privileged_access

        group_result = await self.session.execute(
            select(GroupTable)
            .where(GroupTable.id == request.group_id)
            .options(selectinload(GroupTable.channel))
        )
        group = group_result.scalar_one_or_none()
        if not group:
            raise NotFoundError(
                resource_type="Group", resource_id=str(request.group_id)
            )

        channel = group.channel
        may_write = agent_id in channel.writers or await has_privileged_access(
            self.session, agent_id
        )
        if not may_write:
            raise PermissionError("You don't have write access to this group")

        active_result = await self.session.execute(
            select(SessionTable).where(
                SessionTable.group_id == request.group_id,
                SessionTable.status == SessionStatus.ACTIVE,
            )
        )
        active = active_result.scalar_one_or_none()
        if active is not None:
            # Reuse the group's live session (see create_session). The CEO and
            # agents post into one session per group, not a fresh one per open —
            # closing + recreating here is what fragmented one conversation
            # across many sessions.
            return active

        new_session = SessionTable(
            group_id=request.group_id,
            max_time_window=(_minutes_to_timedelta(request.max_time_window_minutes)),
            max_message_count=request.max_message_count,
            max_content_length=request.max_content_length,
            timeout_seconds=self._resolve_session_timeout(request.timeout_seconds),
            status=SessionStatus.ACTIVE,
        )
        self.session.add(new_session)
        # Flush so new_session.id is materialized before we link it on the group;
        # assigning it pre-flush persists active_session_id as NULL (see
        # create_session).
        await self.session.flush()
        group.active_session_id = new_session.id
        group.total_sessions += 1
        await self.session.flush()

        await self._inject_proactive_context(
            session_id=cast("UUID", new_session.id), agent_id=agent_id
        )
        return new_session

    async def _inject_proactive_context(
        self, *, session_id: UUID, agent_id: UUID
    ) -> None:
        """Fire-and-forget proactive-context injection for a new session.

        Failure is swallowed: context injection is a best-effort enhancement,
        not a hard requirement of session creation.
        """
        from roboco.services.proactive import get_proactive_service

        try:
            proactive = await get_proactive_service()
            context = await proactive.get_context_for_session(
                session_id=session_id, agent_id=agent_id
            )
            if context and not context.is_empty():
                self.log.info(
                    "Injected session proactive context",
                    session_id=str(session_id),
                    agent_id=str(agent_id),
                )
        except Exception as e:
            self.log.warning(
                "Failed to inject session context",
                session_id=str(session_id),
                error=str(e),
            )

    async def close_session_or_raise(self, session_id: UUID) -> SessionTable:
        """Close an active session; raise if missing or already closed.

        Routes call this instead of reading/mutating session state directly.
        """
        session_row = await self.get_session(session_id)
        if not session_row:
            raise NotFoundError(resource_type="Session", resource_id=str(session_id))
        if session_row.status != SessionStatus.ACTIVE:
            raise ValueError("Session is not active")

        session_row.status = SessionStatus.CLOSED
        session_row.closed_at = datetime.now(UTC)

        group = await self.get_group(cast("UUID", session_row.group_id))
        if group and group.active_session_id == session_id:
            group.active_session_id = None

        await self.session.flush()
        return session_row

    async def get_session_or_raise(self, session_id: UUID) -> SessionTable:
        """Return a session or raise NotFoundError.

        Keeps `None`-handling out of route modules.
        """
        session_row = await self.get_session(session_id)
        if not session_row:
            raise NotFoundError(resource_type="Session", resource_id=str(session_id))
        return session_row

    async def get_channel_by_slug_or_raise(self, slug: str) -> ChannelTable:
        """Return a channel by slug or raise NotFoundError."""
        channel = await self.get_channel_by_slug(slug)
        if not channel:
            raise NotFoundError(resource_type="Channel", resource_id=slug)
        return channel

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
        Link a session to a task (idempotent).

        Args:
            session_id: Session to link
            task_id: Task to link
            added_by: PM who created this link
            is_primary: Mark as primary discussion session for this task
            relationship_type: Type of relationship

        Returns:
            Created link, or the existing link if (session_id, task_id) is
            already linked. Re-linking the same pair is a no-op — upstream
            callers sometimes re-issue the link after a create_session_for_
            _tasks call, and that should be a cheap success, not a 409.

        Raises:
            NotFoundError: If session not found
            ConflictError: If `is_primary=True` but the task already has a
                different primary session.
        """
        # Verify session exists
        session = await self.get_session(session_id)
        if not session:
            raise NotFoundError(f"Session {session_id} not found")

        # Idempotent duplicate handling: if this exact (session, task) pair
        # is already linked, return the existing row. Re-creating sessions
        # in agent flows (create_session_for_tasks → ancestor reuse → then
        # an explicit link call) is common; the 409 it used to produce was
        # pure noise.
        existing = await self.session.execute(
            select(SessionTaskTable).where(
                SessionTaskTable.session_id == session_id,
                SessionTaskTable.task_id == task_id,
            )
        )
        existing_link = existing.scalar_one_or_none()
        if existing_link:
            return existing_link

        # Primary constraint holds across sessions: if the task already has
        # a primary on a *different* session, that's a real conflict —
        # promoting two distinct sessions to primary for the same task
        # would break the session-of-record invariant.
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

    async def propagate_sessions_to_subtask(
        self,
        parent_task_id: UUID,
        subtask_id: UUID,
        added_by: UUID,
    ) -> list[SessionTaskTable]:
        """Link every session attached to ``parent_task_id`` onto ``subtask_id``.

        The pre-gateway flow created a session with the whole task tree at
        once, so subtasks were visible in the parent's group chat the moment
        they existed. The gateway creates subtasks one at a time via
        ``delegate()``, so this step re-attaches every existing parent
        session link to the new child.

        ``link_session_to_task`` is idempotent on duplicate (session, task)
        pairs, so re-runs are no-ops. Primary status is NOT propagated —
        each subtask owns its own primary slot, and a primary on the
        parent should not auto-claim the subtask's primary too.
        """
        parent_links = await self.get_sessions_for_task(parent_task_id)
        propagated: list[SessionTaskTable] = []
        for parent_link in parent_links:
            session_id = cast("UUID", parent_link.session_id)
            rel_raw = parent_link.relationship_type
            try:
                rel = SessionTaskRelationshipType(rel_raw)
            except (TypeError, ValueError):
                rel = SessionTaskRelationshipType.DISCUSSION
            link = await self.link_session_to_task(
                session_id=session_id,
                task_id=subtask_id,
                added_by=added_by,
                is_primary=False,
                relationship_type=rel,
            )
            propagated.append(link)
        return propagated

    async def _walk_task_ancestors(self, task_id: UUID) -> list["TaskTable"]:  # type: ignore[name-defined]  # noqa: F821
        """Return [parent, grandparent, ..., root] for a task, empty if none.

        Walks the `parent_task_id` chain without returning the task itself.
        Cycle-safe via a visited set (would only hit on data corruption).
        """
        from roboco.db.tables import TaskTable

        ancestors: list[TaskTable] = []
        seen: set[UUID] = {task_id}
        current_id: UUID | None = task_id
        while current_id:
            result = await self.session.execute(
                select(TaskTable).where(TaskTable.id == current_id)
            )
            task = result.scalar_one_or_none()
            if not task or not task.parent_task_id:
                break
            parent_id = to_python_uuid(task.parent_task_id)
            if parent_id is None or parent_id in seen:
                break
            seen.add(parent_id)
            parent_result = await self.session.execute(
                select(TaskTable).where(TaskTable.id == parent_id)
            )
            parent = parent_result.scalar_one_or_none()
            if not parent:
                break
            ancestors.append(parent)
            current_id = to_python_uuid(parent.parent_task_id)
        return ancestors

    async def _primary_session_link_for_task(
        self, task_id: UUID
    ) -> SessionTaskTable | None:
        """Fetch the task's primary session link with group+session eager-loaded."""
        result = await self.session.execute(
            select(SessionTaskTable)
            .where(
                SessionTaskTable.task_id == task_id,
                SessionTaskTable.is_primary.is_(True),
            )
            .options(
                selectinload(SessionTaskTable.session).selectinload(SessionTable.group)
            )
        )
        return result.scalar_one_or_none()

    async def _resolve_group_from_parent_tasks(
        self,
        task_ids: list[UUID],
    ) -> GroupTable | None:
        """Resolve group by walking ancestors' primary sessions.

        Groups belong to the root-task initiative: same root → same group on
        a given channel. Walks the full ancestry so an ancestor at any depth
        with a primary session lets subtasks reuse that group.
        """
        for task_id in task_ids:
            for ancestor in await self._walk_task_ancestors(task_id):
                link = await self._primary_session_link_for_task(
                    cast("UUID", ancestor.id)
                )
                if link and link.session and link.session.group:
                    self.log.info(
                        "Inherited group from ancestor task's session",
                        task_id=str(task_id),
                        ancestor_task_id=str(ancestor.id),
                        group_id=str(link.session.group.id),
                        group_name=link.session.group.name,
                    )
                    return link.session.group
        return None

    async def _find_ancestor_session_on_channel(
        self,
        task_ids: list[UUID],
        channel_id: UUID,
    ) -> SessionTable | None:
        """Find an active session on ``channel_id`` owned by any ancestor.

        Drives the "same task tree → same group chat" rule: if any ancestor
        of the tasks already has an active primary session in the requested
        channel, new subtasks link to it instead of opening a new session.
        """
        for task_id in task_ids:
            for ancestor in await self._walk_task_ancestors(task_id):
                link = await self._primary_session_link_for_task(
                    cast("UUID", ancestor.id)
                )
                if (
                    link
                    and link.session
                    and link.session.group
                    and link.session.group.channel_id == channel_id
                    and link.session.status == SessionStatus.ACTIVE
                ):
                    return link.session
        return None

    async def _resolve_group_for_session(
        self,
        req: SessionForTasksCreate,
        channel: ChannelTable,
    ) -> GroupTable:
        """Resolve the group for a new session: explicit > inherited > first."""
        if req.group_id:
            group_result = await self.session.execute(
                select(GroupTable).where(GroupTable.id == req.group_id)
            )
            group = group_result.scalar_one_or_none()
            if not group:
                raise NotFoundError(f"Group '{req.group_id}' not found")
            return group

        group = await self._resolve_group_from_parent_tasks(req.task_ids)
        if group:
            return group

        groups = await self.list_groups_in_channel(cast("UUID", channel.id))
        if not groups:
            # Auto-create a default group so sessions don't fail
            default_group = GroupTable(
                name="General",
                channel_id=cast("UUID", channel.id),
                hierarchy_level=1,
                allowed_roles=[],
                members=[],
            )
            self.session.add(default_group)
            channel.group_count += 1
            await self.session.flush()
            self.log.info(
                "Auto-created default group for channel",
                channel_slug=req.channel_slug,
                group_id=str(default_group.id),
            )
            return default_group

        fallback_group = groups[0]
        self.log.warning(
            "Session created without explicit group, using first group",
            channel_slug=req.channel_slug,
            group_name=fallback_group.name,
            task_ids=[str(t) for t in req.task_ids],
        )
        return fallback_group

    @staticmethod
    def _build_session_request(
        req: SessionForTasksCreate, group: GroupTable
    ) -> SessionCreateRequest:
        """Build a SessionCreateRequest from tasks-create input."""
        return SessionCreateRequest(
            group_id=cast("UUID", group.id),
            max_message_count=(req.config.max_message_count if req.config else None),
            max_content_length=(req.config.max_content_length if req.config else None),
            timeout_seconds=(
                req.config.timeout_seconds
                if req.config and req.config.timeout_seconds is not None
                else settings.session_idle_timeout_seconds
            ),
            scope=req.scope,
        )

    async def _link_tasks_to_session(
        self,
        session: SessionTable,
        req: SessionForTasksCreate,
        pm_agent_id: UUID,
    ) -> list[SessionTaskTable]:
        """Attach each task in ``req`` to the newly-created session."""
        links: list[SessionTaskTable] = []
        for i, task_id in enumerate(req.task_ids):
            link = await self.link_session_to_task(
                session_id=cast("UUID", session.id),
                task_id=task_id,
                added_by=pm_agent_id,
                is_primary=i == 0,
                relationship_type=req.relationship_type,
            )
            links.append(link)
        return links

    async def _link_tasks_to_existing_session(
        self,
        session: SessionTable,
        req: SessionForTasksCreate,
        pm_agent_id: UUID,
    ) -> list[SessionTaskTable]:
        """Link tasks to an existing session.

        `link_session_to_task` is itself idempotent on duplicate
        (session, task) pairs — re-links return the existing row — so
        we always get a link back regardless of prior state.
        """
        links: list[SessionTaskTable] = []
        session_id = cast("UUID", session.id)
        for task_id in req.task_ids:
            link = await self.link_session_to_task(
                session_id=session_id,
                task_id=task_id,
                added_by=pm_agent_id,
                is_primary=False,
                relationship_type=req.relationship_type,
            )
            links.append(link)
        return links

    async def create_session_for_tasks(
        self,
        req: SessionForTasksCreate,
        pm_agent_id: UUID,
    ) -> tuple[SessionTable, list[SessionTaskTable]]:
        """
        Create (or reuse) a session linked to one or more tasks (PM operation).

        If any ancestor of the requested tasks already has an active primary
        session in the target channel, we link to that session instead of
        opening a new one — keeping the whole task tree in one group chat.

        Args:
            req: Session creation request with task IDs
            pm_agent_id: PM agent creating the session

        Returns:
            Tuple of (session, list of newly-created links)

        Raises:
            NotFoundError: If channel not found
            ValueError: If no groups found in channel
        """
        channel = await self.get_channel_by_slug(req.channel_slug)
        if not channel:
            raise NotFoundError(f"Channel '{req.channel_slug}' not found")

        channel_id = cast("UUID", channel.id)
        reusable = await self._find_ancestor_session_on_channel(
            req.task_ids, channel_id
        )
        if reusable:
            links = await self._link_tasks_to_existing_session(
                reusable, req, pm_agent_id
            )
            self.log.info(
                "Reused ancestor session for subtasks",
                session_id=str(reusable.id),
                task_count=len(req.task_ids),
                newly_linked=len(links),
                channel_slug=req.channel_slug,
                pm_agent_id=str(pm_agent_id),
            )
            return reusable, links

        group = await self._resolve_group_for_session(req, channel)
        session = await self.create_session(self._build_session_request(req, group))
        links = await self._link_tasks_to_session(session, req, pm_agent_id)

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
        """Get session, group, and channel for sending a message.

        If the requested session has closed (timed out, boundary hit,
        manually closed), transparently redirect to the group's current
        active session — or open a fresh one if none exists. QA/PM agents
        that held a session reference from earlier in the task lifecycle
        shouldn't be blocked from posting an escalation just because the
        session expired; the message still belongs in the group.
        """
        session = await self.get_session(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        if session.status != SessionStatus.ACTIVE:
            group_id = cast("UUID", session.group_id)
            group = await self.get_group(group_id)
            if not group:
                raise ValueError(f"Group {group_id} not found")
            channel = await self.get_channel(cast("UUID", group.channel_id))
            if not channel:
                raise ValueError(f"Channel {group.channel_id} not found")
            # Find the group's current active session, or create one.
            session = await self.get_or_create_active_session(group_id)
            return session, group, channel

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

    _MAX_MSG_CHARS: ClassVar[int] = 10_000

    def _assert_content(self, raw: str | None) -> None:
        """Reject empty / over-cap message content with a readable error."""
        trimmed = (raw or "").strip()
        if not trimmed:
            raise ValueError("EMPTY_MESSAGE: message content cannot be blank.")
        if len(raw or "") > self._MAX_MSG_CHARS:
            raise ValueError(
                f"MESSAGE_TOO_LONG: {len(raw or '')} chars exceeds "
                f"{self._MAX_MSG_CHARS}. Split the message or link a doc."
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
            ValueError: If session not found or not active, or if content is
                blank/too long.
            ChannelAccessDeniedError: If agent cannot write to channel
        """
        self._assert_content(req.content)
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

    async def get_message_or_raise(self, message_id: UUID) -> MessageTable:
        """Return a message or raise NotFoundError."""
        message = await self.get_message(message_id)
        if not message:
            raise NotFoundError(resource_type="Message", resource_id=str(message_id))
        return message

    async def list_messages_for_session(
        self,
        *,
        session_id: UUID,
        before: datetime | None,
        after: datetime | None,
        message_type: MessageType | None,
        limit: int,
    ) -> tuple[list[MessageTable], bool]:
        """Session-scoped message list, verifying the session exists first.

        Existing `get_messages` skips the session-existence check — this
        variant raises NotFoundError when the session is missing so routes
        can return a clean 404 without issuing their own query.
        """
        await self.get_session_or_raise(session_id)
        return await self.get_messages(
            session_id,
            before=before,
            after=after,
            message_type=message_type,
            limit=limit,
        )

    async def edit_message_or_raise(
        self,
        *,
        message_id: UUID,
        agent_id: UUID,
        new_content: str,
        edit_reason: str | None,
    ) -> MessageTable:
        """Edit a message; raise NotFoundError / PermissionError on miss."""
        message = await self.get_message(message_id)
        if not message:
            raise NotFoundError(resource_type="Message", resource_id=str(message_id))
        if message.agent_id != agent_id:
            raise PermissionError("Only the author can edit this message")

        edit_entry = {
            "edited_at": datetime.now(UTC).isoformat(),
            "previous_content": message.content,
            "edit_reason": edit_reason,
        }
        message.edit_history = [*message.edit_history, edit_entry]

        old_length = message.content_length
        new_length = len(new_content)
        delta = new_length - old_length
        message.content = new_content
        message.content_length = new_length
        message.edited_at = datetime.now(UTC)

        session_row = await self.get_session(cast("UUID", message.session_id))
        if session_row:
            session_row.total_content_length += delta

        await self.session.flush()
        self.log.info(
            "Message edited",
            message_id=str(message_id),
            agent_id=str(agent_id),
        )
        return message

    async def delete_message_or_raise(
        self,
        *,
        message_id: UUID,
        agent_id: UUID,
    ) -> None:
        """Hard-delete a message; adjust session counters accordingly.

        The soft-delete variant (`delete_message`) is kept for callers that
        want tombstoned content; this method is the hard-delete path the
        API exposes.
        """
        message = await self.get_message(message_id)
        if not message:
            raise NotFoundError(resource_type="Message", resource_id=str(message_id))
        if message.agent_id != agent_id:
            raise PermissionError("Only the author can delete this message")

        session_row = await self.get_session(cast("UUID", message.session_id))
        if session_row:
            session_row.message_count -= 1
            session_row.total_content_length -= message.content_length

        await self.session.delete(message)
        await self.session.flush()

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

    # =========================================================================
    # GATEWAY (CONTENT_ACTIONS) BACKFILL
    # =========================================================================

    async def _default_group_for_channel(
        self,
        channel: ChannelTable,
    ) -> GroupTable:
        """Return a usable group for posting into `channel`.

        Strategy: pick the first existing group ordered by hierarchy_level
        then name. If the channel has no groups yet (fresh channel), create
        a single default group. Channels were originally designed to have
        explicit groups created at provisioning time, but the gateway
        `say` verb addresses the channel as a whole — so we paper over
        that boundary here rather than forcing every caller to know about
        groups.
        """
        groups = await self.list_groups_in_channel(cast("UUID", channel.id))
        if groups:
            return groups[0]
        return await self.create_group(
            GroupCreateRequest(
                name="default",
                channel_id=cast("UUID", channel.id),
                allowed_roles=[],
                hierarchy_level=4,
                members=[],
            )
        )

    @staticmethod
    def _task_group_name(task_id: UUID) -> str:
        """Deterministic group name that threads a task's channel chatter.

        Encoding the task id in the group name lets us locate the same
        per-(channel, task) group on every post without a new column —
        `GroupTable.name` is reused as the lookup key.
        """
        return f"task:{task_id}"

    async def _task_group_for_channel(
        self,
        channel: ChannelTable,
        task_id: UUID,
    ) -> GroupTable:
        """Return the per-(channel, task) group, creating it on first post.

        A task's discussion on a given channel threads into ONE group so it
        is not scattered across the channel's standing groups. The group is
        keyed by `_task_group_name(task_id)` within the channel.
        """
        wanted = self._task_group_name(task_id)
        groups = await self.list_groups_in_channel(cast("UUID", channel.id))
        for group in groups:
            if group.name == wanted:
                return group
        return await self.create_group(
            GroupCreateRequest(
                name=wanted,
                channel_id=cast("UUID", channel.id),
                allowed_roles=[],
                hierarchy_level=4,
                members=[],
            )
        )

    async def post_to_channel(
        self,
        *,
        agent_id: UUID,
        channel_slug: str,
        content: str,
        task_id: UUID | None = None,
    ) -> MessageTable:
        """Gateway adapter — post a message to a channel by slug.

        The gateway `say` verb addresses channels by slug (`backend-cell`,
        `all-hands`, ...) and doesn't carry session/group IDs. This adapter
        resolves the channel by slug, picks the target group, gets or
        creates the active session for that group, then sends a message via
        `send_message`. When `task_id` is supplied the message threads into
        the per-(channel, task) group so a task's discussion lives in one
        place per channel rather than scattering across standing groups;
        otherwise it falls back to the channel's default group.

        Channel write-access is validated UP FRONT (before any group or
        session is created) so a denied write leaves no side effects. The
        agent slug is resolved from `agent_id`; if the lookup returns None
        (unknown / removed agent) we fail closed by raising
        ChannelAccessDeniedError. `send_message` re-validates access as
        defense in depth.
        """
        from roboco.enforcement.channel_access import ChannelAccessDeniedError
        from roboco.services.repositories import get_agent_slug

        channel = await self.get_channel_by_slug_or_raise(channel_slug)
        agent_slug = await get_agent_slug(self.session, agent_id)
        if agent_slug is None:
            raise ChannelAccessDeniedError(
                agent_id=str(agent_id),
                channel_slug=channel_slug,
                action="write",
                message=(f"agent {agent_id} not found; cannot validate channel access"),
            )
        validate_channel_access(agent_slug, channel_slug, "write")

        if task_id is not None:
            group = await self._task_group_for_channel(channel, task_id)
        else:
            group = await self._default_group_for_channel(channel)
        session = await self.get_or_create_active_session(cast("UUID", group.id))
        return await self.send_message(
            MessageCreateRequest(
                agent_id=agent_id,
                session_id=cast("UUID", session.id),
                content=content,
                task_id=task_id,
            ),
            agent_slug=agent_slug,
        )


# =============================================================================
# SERVICE FACTORY
# =============================================================================


def get_messaging_service(session: AsyncSession) -> MessagingService:
    """Factory function to create a MessagingService instance."""
    return MessagingService(session)
