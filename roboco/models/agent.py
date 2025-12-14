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

    # NOTE: Role-based permission checks should use roboco.agents_config:
    # - is_management(agent_id) - Check if agent is in management
    # - is_board_member(agent_id) - Check if agent is on the board
    # - can_send_notifications(agent_id) - Check if agent can send notifications
    #
    # Agent state mutations should be performed through a service layer,
    # not directly on the model.


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
