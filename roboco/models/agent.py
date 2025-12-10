"""
Agent Model

Represents an AI agent in the organization. Each agent has a role,
team affiliation, capabilities, and permissions.
"""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import Field

from roboco.models.base import (
    AgentRole,
    AgentStatus,
    RobocoBase,
    Team,
    TimestampMixin,
)

# =============================================================================
# SUPPORTING MODELS
# =============================================================================


class ModelConfig(RobocoBase):
    """Configuration for the LLM model an agent uses."""

    provider: str = Field(..., description="Model provider (anthropic, local, etc.)")
    name: str = Field(..., description="Model name (claude-3-opus, llama-70b, etc.)")
    fallback: str | None = Field(
        default=None, description="Fallback model if primary unavailable"
    )
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=1)


class AgentPermissions(RobocoBase):
    """Permissions for an agent."""

    can_notify: bool = Field(
        default=False,
        description="Whether agent can send notifications (only PMs, Board, Auditor)",
    )
    channels_read: list[UUID] = Field(
        default_factory=list, description="Channel IDs agent can read"
    )
    channels_write: list[UUID] = Field(
        default_factory=list, description="Channel IDs agent can write to"
    )


class AgentMetrics(RobocoBase):
    """Performance metrics for an agent."""

    tasks_completed: int = Field(default=0, ge=0)
    tasks_in_progress: int = Field(default=0, ge=0)
    avg_completion_hours: float | None = Field(
        default=None, description="Average hours to complete a task"
    )
    quality_score: float | None = Field(
        default=None, ge=0.0, le=1.0, description="QA pass rate"
    )
    last_active: datetime | None = None


# =============================================================================
# MAIN AGENT MODEL
# =============================================================================


class Agent(TimestampMixin):
    """
    An AI agent in the RoboCo organization.

    Each agent has a defined role, team affiliation, and operates
    within the organizational hierarchy.
    """

    # Identity
    id: UUID = Field(default_factory=uuid4, description="Unique agent identifier")
    name: str = Field(
        ..., min_length=1, max_length=100, description="Agent display name"
    )
    slug: str = Field(
        ...,
        min_length=1,
        max_length=50,
        pattern=r"^[a-z0-9-]+$",
        description="URL-safe identifier (e.g., be-dev-1)",
    )

    # Role & Team
    role: AgentRole = Field(..., description="Agent's organizational role")
    team: Team | None = Field(
        default=None, description="Team affiliation (None for board members)"
    )

    # Status
    status: AgentStatus = Field(default=AgentStatus.OFFLINE)
    current_task_id: UUID | None = Field(
        default=None, description="Currently assigned task"
    )

    # Configuration
    model: ModelConfig = Field(..., description="LLM model configuration")
    system_prompt: str = Field(..., description="Base system prompt for this agent")
    capabilities: list[str] = Field(
        default_factory=list,
        description="List of capabilities (code_execution, git_operations, etc.)",
    )

    # Permissions
    permissions: AgentPermissions = Field(default_factory=AgentPermissions)

    # Metrics
    metrics: AgentMetrics = Field(default_factory=AgentMetrics)

    # Journal
    journal_id: UUID | None = Field(
        default=None, description="ID of agent's personal journal"
    )

    # Description
    description: str | None = Field(
        default=None, description="Human-readable description of this agent"
    )

    @property
    def is_management(self) -> bool:
        """Check if agent is in management (PM or above)."""
        return self.role in (
            AgentRole.CEO,
            AgentRole.PRODUCT_OWNER,
            AgentRole.HEAD_MARKETING,
            AgentRole.AUDITOR,
            AgentRole.MAIN_PM,
            AgentRole.CELL_PM,
        )

    @property
    def is_board(self) -> bool:
        """Check if agent is on the board."""
        return self.role in (
            AgentRole.CEO,
            AgentRole.PRODUCT_OWNER,
            AgentRole.HEAD_MARKETING,
            AgentRole.AUDITOR,
        )

    @property
    def can_send_notifications(self) -> bool:
        """Check if agent can send formal notifications."""
        return self.permissions.can_notify or self.role in (
            AgentRole.CEO,
            AgentRole.AUDITOR,
            AgentRole.MAIN_PM,
            AgentRole.CELL_PM,
            AgentRole.PRODUCT_OWNER,
            AgentRole.HEAD_MARKETING,
        )

    def go_online(self) -> None:
        """Mark agent as online/active."""
        self.status = AgentStatus.ACTIVE
        self.metrics.last_active = datetime.utcnow()

    def go_idle(self) -> None:
        """Mark agent as idle (online but not working)."""
        self.status = AgentStatus.IDLE
        self.current_task_id = None

    def go_offline(self) -> None:
        """Mark agent as offline."""
        self.status = AgentStatus.OFFLINE
        self.current_task_id = None

    def assign_task(self, task_id: UUID) -> None:
        """Assign a task to this agent."""
        self.current_task_id = task_id
        self.status = AgentStatus.ACTIVE
        self.metrics.tasks_in_progress += 1

    def complete_task(self, hours_spent: float | None = None) -> None:
        """Mark current task as complete."""
        self.current_task_id = None
        self.metrics.tasks_completed += 1
        self.metrics.tasks_in_progress = max(0, self.metrics.tasks_in_progress - 1)

        # Update average completion time
        if hours_spent is not None:
            if self.metrics.avg_completion_hours is None:
                self.metrics.avg_completion_hours = hours_spent
            else:
                # Running average
                total = self.metrics.tasks_completed
                self.metrics.avg_completion_hours = (
                    self.metrics.avg_completion_hours * (total - 1) + hours_spent
                ) / total

        self.status = AgentStatus.IDLE


# =============================================================================
# CREATE/UPDATE SCHEMAS
# =============================================================================


class AgentCreate(RobocoBase):
    """Schema for creating a new agent."""

    name: str = Field(..., min_length=1, max_length=100)
    slug: str = Field(..., min_length=1, max_length=50, pattern=r"^[a-z0-9-]+$")
    role: AgentRole
    team: Team | None = None
    model: ModelConfig
    system_prompt: str
    capabilities: list[str] = Field(default_factory=list)
    description: str | None = None


class AgentUpdate(RobocoBase):
    """Schema for updating an agent."""

    name: str | None = None
    status: AgentStatus | None = None
    model: ModelConfig | None = None
    system_prompt: str | None = None
    capabilities: list[str] | None = None
    permissions: AgentPermissions | None = None
    description: str | None = None
