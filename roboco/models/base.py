"""
Base models and enums for RoboCo.

Contains all enumeration types and the base Pydantic model configuration.
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# ENUMS
# =============================================================================


class TaskStatus(str, Enum):
    """Task lifecycle states."""

    PENDING = "pending"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    PAUSED = "paused"
    VERIFYING = "verifying"
    NEEDS_REVISION = "needs_revision"
    AWAITING_QA = "awaiting_qa"
    AWAITING_DOCUMENTATION = "awaiting_documentation"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Complexity(str, Enum):
    """Task complexity levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Team(str, Enum):
    """Organizational teams/cells."""

    BACKEND = "backend"
    FRONTEND = "frontend"
    UX_UI = "ux_ui"
    BOARD = "board"
    MARKETING = "marketing"


class AgentRole(str, Enum):
    """Agent roles in the organization."""

    # System (internal orchestrator operations)
    SYSTEM = "system"

    # Executive
    CEO = "ceo"

    # Board
    PRODUCT_OWNER = "product_owner"
    HEAD_MARKETING = "head_marketing"
    AUDITOR = "auditor"

    # Management
    MAIN_PM = "main_pm"
    CELL_PM = "cell_pm"

    # Cell Members
    DEVELOPER = "developer"
    QA = "qa"
    DOCUMENTER = "documenter"


class AgentStatus(str, Enum):
    """Agent operational states."""

    ACTIVE = "active"
    IDLE = "idle"
    OFFLINE = "offline"


class SessionStatus(str, Enum):
    """Session states."""

    ACTIVE = "active"
    CLOSED = "closed"
    TIMED_OUT = "timed_out"


class MessageType(str, Enum):
    """Types of extracted messages from agent streams."""

    REASONING = "reasoning"
    DIALOGUE = "dialogue"
    DECISION = "decision"
    ACTION = "action"
    BLOCKER = "blocker"
    TECHNICAL = "technical"


class NotificationType(str, Enum):
    """Formal notification types."""

    TASK_ASSIGNMENT = "task_assignment"
    PRIORITY_CHANGE = "priority_change"
    BLOCKER_ESCALATION = "blocker_escalation"
    REVIEW_REQUEST = "review_request"
    DOCUMENTATION_REQUEST = "documentation_request"
    ALERT = "alert"
    BROADCAST = "broadcast"


class NotificationPriority(str, Enum):
    """Notification priority levels."""

    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class ChannelType(str, Enum):
    """Channel types for communication."""

    CELL = "cell"  # Internal team
    CROSS_CELL = "cross_cell"  # Coordination
    MANAGEMENT = "management"
    SPECIAL = "special"  # Announcements, all-hands


class JournalEntryType(str, Enum):
    """Types of journal entries."""

    TASK_REFLECTION = "task_reflection"
    DECISION_LOG = "decision_log"
    LEARNING = "learning"
    STRUGGLE = "struggle"
    GENERAL = "general"


class HandoffStatus(str, Enum):
    """Documenter handoff states."""

    PENDING = "pending"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    ACCEPTED = "accepted"
    COMPLETED = "completed"


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
