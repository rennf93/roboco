"""
Channel Model

Channels are the top-level organizational unit for communication.
They map to team structure (#backend-cell, #pm-all, etc.).
"""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import Field

from roboco.models.base import (
    ChannelType,
    RobocoBase,
    TimestampMixin,
)

# =============================================================================
# MAIN CHANNEL MODEL
# =============================================================================


class Channel(TimestampMixin):
    """
    Top-level organizational unit for communication.

    Channels map to team structure and contain groups
    with different access levels.
    """

    # Identity
    id: UUID = Field(default_factory=uuid4, description="Channel ID")
    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        pattern=r"^#?[a-z0-9-]+$",
        description="Channel name (e.g., #backend-cell)",
    )
    slug: str = Field(
        ...,
        min_length=1,
        max_length=50,
        pattern=r"^[a-z0-9-]+$",
        description="URL-safe identifier",
    )
    type: ChannelType = Field(..., description="Channel type")

    # Description
    description: str | None = Field(
        default=None, description="Channel description/purpose"
    )
    topic: str | None = Field(default=None, description="Current channel topic")

    # Access Control
    members: list[UUID] = Field(
        default_factory=list, description="Agent IDs who can see the channel"
    )
    writers: list[UUID] = Field(
        default_factory=list, description="Agent IDs who can write to the channel"
    )
    silent_observers: list[UUID] = Field(
        default_factory=list, description="Agent IDs with silent read access (Auditor)"
    )

    # Settings
    is_archived: bool = Field(default=False)
    is_private: bool = Field(default=False)
    allow_threads: bool = Field(default=True)
    allow_reactions: bool = Field(default=True)
    message_retention_days: int | None = Field(
        default=90, description="How long to retain messages (None = forever)"
    )
    max_message_length: int = Field(default=10000, ge=1)

    # Statistics
    message_count: int = Field(default=0, ge=0)
    group_count: int = Field(default=0, ge=0)
    last_activity: datetime | None = None

    def add_member(self, agent_id: UUID, can_write: bool = True) -> None:
        """Add a member to the channel."""
        if agent_id not in self.members:
            self.members.append(agent_id)
        if can_write and agent_id not in self.writers:
            self.writers.append(agent_id)

    def remove_member(self, agent_id: UUID) -> None:
        """Remove a member from the channel."""
        if agent_id in self.members:
            self.members.remove(agent_id)
        if agent_id in self.writers:
            self.writers.remove(agent_id)

    def add_silent_observer(self, agent_id: UUID) -> None:
        """Add a silent observer (like Auditor)."""
        if agent_id not in self.silent_observers:
            self.silent_observers.append(agent_id)

    def can_read(self, agent_id: UUID) -> bool:
        """Check if an agent can read this channel."""
        return agent_id in self.members or agent_id in self.silent_observers

    def can_write(self, agent_id: UUID) -> bool:
        """Check if an agent can write to this channel."""
        return agent_id in self.writers

    def record_activity(self) -> None:
        """Record activity in the channel."""
        self.last_activity = datetime.utcnow()

    def archive(self) -> None:
        """Archive the channel."""
        self.is_archived = True

    def unarchive(self) -> None:
        """Unarchive the channel."""
        self.is_archived = False


# =============================================================================
# PREDEFINED CHANNEL FACTORIES
# =============================================================================


def create_cell_channel(
    cell_name: str,
    member_ids: list[UUID],
    auditor_id: UUID,
) -> Channel:
    """Create a standard cell channel."""
    return Channel(
        name=f"#{cell_name}-cell",
        slug=f"{cell_name}-cell",
        type=ChannelType.CELL,
        description=f"Internal channel for {cell_name} cell",
        members=member_ids,
        writers=member_ids,
        silent_observers=[auditor_id],
    )


def create_cross_cell_channel(
    name: str,
    member_ids: list[UUID],
    main_pm_id: UUID,
    auditor_id: UUID,
) -> Channel:
    """Create a cross-cell coordination channel."""
    all_members = [*member_ids, main_pm_id]
    return Channel(
        name=f"#{name}",
        slug=name,
        type=ChannelType.CROSS_CELL,
        description=f"Cross-cell channel: {name}",
        members=all_members,
        writers=member_ids,
        silent_observers=[auditor_id],
    )


def create_announcements_channel(
    all_agent_ids: list[UUID],
    board_ids: list[UUID],
    main_pm_id: UUID,
    auditor_id: UUID,
) -> Channel:
    """Create the announcements channel (read-only for most)."""
    return Channel(
        name="#announcements",
        slug="announcements",
        type=ChannelType.SPECIAL,
        description="Company-wide announcements (read-only except for Board and Main PM)",
        members=all_agent_ids,
        writers=[*board_ids, main_pm_id],
        silent_observers=[auditor_id],
    )


# =============================================================================
# CREATE/UPDATE SCHEMAS
# =============================================================================


class ChannelCreate(RobocoBase):
    """Schema for creating a new channel."""

    name: str = Field(..., min_length=1, max_length=100, pattern=r"^#?[a-z0-9-]+$")
    slug: str = Field(..., min_length=1, max_length=50, pattern=r"^[a-z0-9-]+$")
    type: ChannelType
    description: str | None = None
    members: list[UUID] = Field(default_factory=list)
    writers: list[UUID] = Field(default_factory=list)
    silent_observers: list[UUID] = Field(default_factory=list)
    is_private: bool = False


class ChannelUpdate(RobocoBase):
    """Schema for updating a channel."""

    name: str | None = Field(default=None, pattern=r"^#?[a-z0-9-]+$")
    description: str | None = None
    topic: str | None = None
    is_archived: bool | None = None
    allow_threads: bool | None = None
    allow_reactions: bool | None = None
    message_retention_days: int | None = None
    max_message_length: int | None = Field(default=None, ge=1)
