"""
Group Model

Groups are role-based chat containers within channels.
They hold sessions and control access by hierarchy level.
"""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import Field

from roboco.models.base import (
    AgentRole,
    RobocoBase,
    TimestampMixin,
)
from roboco.models.session import SessionConfig

# =============================================================================
# MAIN GROUP MODEL
# =============================================================================


class Group(TimestampMixin):
    """
    Role-based group chat container.

    Groups hold sessions which hold messages.
    Access is controlled by hierarchy level.
    """

    # Identity
    id: UUID = Field(default_factory=uuid4, description="Group ID")
    name: str = Field(..., min_length=1, max_length=100, description="Group name")
    channel_id: UUID = Field(..., description="Parent channel ID")

    # Access Control
    allowed_roles: list[AgentRole] = Field(
        default_factory=list, description="Roles that can access this group"
    )
    hierarchy_level: int = Field(
        default=4,
        ge=0,
        le=4,
        description="Access level: 0=CEO, 1=Board, 2=MainPM, 3=CellPM, 4=CellMembers",
    )

    # Members (can have explicit additions beyond role-based access)
    members: list[UUID] = Field(
        default_factory=list, description="Explicit member agent IDs"
    )

    # Settings
    is_active: bool = Field(default=True)

    # Current Session
    active_session_id: UUID | None = Field(
        default=None, description="Currently active session"
    )

    # Session Configuration (defaults for new sessions)
    default_session_config: SessionConfig = Field(
        default_factory=SessionConfig, description="Default config for new sessions"
    )

    # Statistics
    total_sessions: int = Field(default=0, ge=0)
    total_messages: int = Field(default=0, ge=0)
    last_activity: datetime | None = None

    def add_member(self, agent_id: UUID) -> None:
        """Add an explicit member to the group."""
        if agent_id not in self.members:
            self.members.append(agent_id)

    def remove_member(self, agent_id: UUID) -> None:
        """Remove a member from the group."""
        if agent_id in self.members:
            self.members.remove(agent_id)

    def has_access(self, agent_role: AgentRole, agent_id: UUID) -> bool:
        """Check if an agent has access to this group."""
        # Explicit members always have access
        if agent_id in self.members:
            return True

        # Check role-based access
        return agent_role in self.allowed_roles

    def record_activity(self) -> None:
        """Record activity in the group."""
        self.last_activity = datetime.utcnow()

    def increment_messages(self) -> None:
        """Increment message count."""
        self.total_messages += 1
        self.record_activity()

    def start_new_session(self, session_id: UUID) -> None:
        """Start a new session in this group."""
        self.active_session_id = session_id
        self.total_sessions += 1
        self.record_activity()

    def close_session(self) -> None:
        """Close the current session."""
        self.active_session_id = None


# =============================================================================
# CREATE/UPDATE SCHEMAS
# =============================================================================


class GroupCreate(RobocoBase):
    """Schema for creating a new group."""

    name: str = Field(..., min_length=1, max_length=100)
    channel_id: UUID
    allowed_roles: list[AgentRole] = Field(default_factory=list)
    hierarchy_level: int = Field(default=4, ge=0, le=4)
    members: list[UUID] = Field(default_factory=list)
    default_session_config: SessionConfig | None = None


class GroupUpdate(RobocoBase):
    """Schema for updating a group."""

    name: str | None = None
    allowed_roles: list[AgentRole] | None = None
    hierarchy_level: int | None = Field(default=None, ge=0, le=4)
    is_active: bool | None = None
    default_session_config: SessionConfig | None = None
