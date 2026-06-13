"""
Base models and enums for RoboCo.

Contains all enumeration types and the base Pydantic model configuration.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# AgentRole and Team are canonicalized in roboco/foundation/identity.py.
# These bindings keep existing imports (`from roboco.models.base import
# AgentRole, Team`) working during the migration. SQLAlchemy column types
# bound as `sa.Enum(AgentRole, name="agentrole")` continue to work because
# Python identity is preserved — AgentRole IS identity.Role (same class
# object), so SQLAlchemy maps to the same postgres `agentrole` enum.
# Removed in Phase 4 housekeeping after every consumer is migrated.
from roboco.foundation import identity

AgentRole = identity.Role
Team = identity.Team

# =============================================================================
# ENUMS
# =============================================================================


class TaskStatus(StrEnum):
    """Task lifecycle states."""

    BACKLOG = "backlog"  # PM setup phase - session must be created before activation
    PENDING = "pending"  # Ready for work - orchestrator can spawn agents
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    PAUSED = "paused"
    VERIFYING = "verifying"
    NEEDS_REVISION = "needs_revision"
    AWAITING_QA = "awaiting_qa"
    AWAITING_DOCUMENTATION = "awaiting_documentation"  # Docs + Dev PR in parallel
    AWAITING_PM_REVIEW = "awaiting_pm_review"  # After docs + PR ready
    AWAITING_CEO_APPROVAL = "awaiting_ceo_approval"  # PMs approved, CEO decides
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class BlockerResolverType(StrEnum):
    """Who is expected to resolve a BLOCKED task.

    Distinguishes blocks that an agent can work through vs blocks that
    require a human (CEO) to intervene — so the dispatcher doesn't keep
    respawning agents when it's waiting on manual action.
    """

    AGENT = "agent"  # Another agent resolves — dispatcher can respawn
    HUMAN = "human"  # HITL/CEO only — dispatcher must NOT respawn


class TaskType(StrEnum):
    """Task classification. ALL types follow git workflow."""

    CODE = "code"  # Source code changes
    DOCUMENTATION = "documentation"  # Documentation updates
    RESEARCH = "research"  # Research findings committed as notes
    PLANNING = "planning"  # Plans/architecture committed as docs
    DESIGN = "design"  # Designs/specs committed as assets
    ADMINISTRATIVE = "administrative"  # Process docs committed


class TaskNature(StrEnum):
    """Task nature classification - technical vs non-technical work."""

    TECHNICAL = "technical"
    NON_TECHNICAL = "non_technical"


class Complexity(StrEnum):
    """Task complexity levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AgentStatus(StrEnum):
    """Agent operational states."""

    ACTIVE = "active"
    IDLE = "idle"
    OFFLINE = "offline"


class SubstituteReason(StrEnum):
    """Reasons for agent substitution request.

    Used when an agent needs to release a task and allow another agent to take over.
    This bypasses the normal "can't claim while in_progress" rule.
    """

    LOW_CONTEXT = "low_context"  # Insufficient context to continue safely
    OUT_OF_SCOPE_TEAM = "out_of_scope_team"  # Task belongs to different team
    OUT_OF_SCOPE_ROLE = "out_of_scope_role"  # Task requires different role
    TASK_COMPLETE = "task_complete"  # Finished work, releasing task
    MAX_RETRIES = "max_retries"  # Exceeded retry limit, need fresh perspective
    BLOCKED_EXTERNAL = "blocked_external"  # Need skills outside agent's capabilities


class SessionStatus(StrEnum):
    """Session states."""

    ACTIVE = "active"
    CLOSED = "closed"
    TIMED_OUT = "timed_out"


class MessageType(StrEnum):
    """Types of extracted messages from agent streams."""

    REASONING = "reasoning"
    DIALOGUE = "dialogue"
    DECISION = "decision"
    ACTION = "action"
    BLOCKER = "blocker"
    TECHNICAL = "technical"


class NotificationType(StrEnum):
    """Formal notification types."""

    TASK_ASSIGNMENT = "task_assignment"
    PRIORITY_CHANGE = "priority_change"
    BLOCKER_ESCALATION = "blocker_escalation"
    REVIEW_REQUEST = "review_request"
    DOCUMENTATION_REQUEST = "documentation_request"
    APPROVAL = "approval"  # Board-level approval requests (PO/HM/Main PM)
    ALERT = "alert"
    BROADCAST = "broadcast"
    KNOWLEDGE_SHARE = "knowledge_share"  # Cross-agent learning notification
    MENTION = "mention"  # @mention in chat
    A2A_REQUEST = "a2a_request"  # Agent-to-agent direct request


class NotificationPriority(StrEnum):
    """Notification priority levels."""

    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class ChannelType(StrEnum):
    """Channel types for communication."""

    CELL = "cell"  # Internal team
    CROSS_CELL = "cross_cell"  # Coordination
    MANAGEMENT = "management"
    SPECIAL = "special"  # Announcements, all-hands


class JournalEntryType(StrEnum):
    """Types of journal entries."""

    TASK_REFLECTION = "task_reflection"
    DECISION_LOG = "decision_log"
    LEARNING = "learning"
    STRUGGLE = "struggle"
    GENERAL = "general"


class HandoffStatus(StrEnum):
    """
    Documenter handoff states.

    NOTE: Reserved for future HandoffTable implementation.
    Currently unused - see HandoffTable docstring for details.
    """

    PENDING = "pending"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    ACCEPTED = "accepted"
    COMPLETED = "completed"


class ModelProvider(StrEnum):
    """LLM provider options.

    `ANTHROPIC` is the built-in default — routed via the mounted `~/.claude/`
    credentials inside each agent container. `OLLAMA_CLOUD` routes via
    `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` env injection at spawn.
    `LOCAL` is the self-hosted Ollama provider: the operator configures its
    base URL via PUT /api/providers/self-hosted (seeded by migration 028).
    Agents assigned to LOCAL are routed to that server at spawn time.
    `OPENAI` is reserved for future use.
    """

    ANTHROPIC = "anthropic"
    OLLAMA_CLOUD = "ollama_cloud"
    OPENAI = "openai"
    LOCAL = "local"


class AssignmentScope(StrEnum):
    """Scope for a model_assignment row.

    Resolution precedence at spawn time:
        AGENT_SLUG > ROLE > GLOBAL
    """

    GLOBAL = "global"
    ROLE = "role"
    AGENT_SLUG = "agent_slug"


# =============================================================================
# BASE MODEL
# =============================================================================


class RobocoBase(BaseModel):
    """
    Base model for all RoboCo models.

    Provides common configuration for JSON serialization,
    immutability, and validation.
    """

    model_config = ConfigDict(
        # Use enum values for serialization
        use_enum_values=True,
        # Validate on assignment
        validate_assignment=True,
        # Allow population by field name
        populate_by_name=True,
        # Extra fields are forbidden
        extra="forbid",
    )


# =============================================================================
# COMMON FIELD TYPES
# =============================================================================

# Annotated types for common fields
AgentID = Annotated[UUID, Field(description="Unique agent identifier")]
TaskID = Annotated[UUID, Field(description="Unique task identifier")]
ChannelID = Annotated[UUID, Field(description="Unique channel identifier")]
MessageID = Annotated[UUID, Field(description="Unique message identifier")]
SessionID = Annotated[UUID, Field(description="Unique session identifier")]
GroupID = Annotated[UUID, Field(description="Unique group identifier")]


class TimestampMixin(RobocoBase):
    """Mixin for models that track creation and update times."""

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None
