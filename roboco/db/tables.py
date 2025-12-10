"""
SQLAlchemy Table Definitions

ORM mappings for all RoboCo data models.
"""

from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    Interval,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from roboco.db.base import Base
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    ChannelType,
    Complexity,
    HandoffStatus,
    JournalEntryType,
    MessageType,
    NotificationPriority,
    NotificationType,
    SessionStatus,
    TaskStatus,
    Team,
)

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def utcnow() -> datetime:
    """Get current UTC time."""
    return datetime.utcnow()


# =============================================================================
# AGENT TABLE
# =============================================================================


class AgentTable(Base):
    """SQLAlchemy table for agents."""

    __tablename__ = "agents"

    # Identity
    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )

    # Role & Team
    role: Mapped[AgentRole] = mapped_column(Enum(AgentRole), nullable=False)
    team: Mapped[Team | None] = mapped_column(Enum(Team), nullable=True)

    # Status
    status: Mapped[AgentStatus] = mapped_column(
        Enum(AgentStatus), nullable=False, default=AgentStatus.OFFLINE
    )
    current_task_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )

    # Configuration (stored as JSON)
    model_config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)

    # Permissions (stored as JSON)
    permissions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # Metrics (stored as JSON)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # Journal
    journal_id: Mapped[UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # Description
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, onupdate=utcnow, nullable=True
    )

    # Relationships
    current_task: Mapped["TaskTable | None"] = relationship(
        "TaskTable", foreign_keys=[current_task_id], lazy="selectin"
    )


# =============================================================================
# TASK TABLE
# =============================================================================


class TaskTable(Base):
    """SQLAlchemy table for tasks."""

    __tablename__ = "tasks"

    # Identity
    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    acceptance_criteria: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False
    )

    # Status
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus), nullable=False, default=TaskStatus.PENDING, index=True
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=2)

    # Ownership
    created_by: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False
    )
    assigned_to: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    team: Mapped[Team] = mapped_column(Enum(Team), nullable=False, index=True)

    # Relationships
    parent_task_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    dependency_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), default=list
    )
    blocker_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), default=list
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, onupdate=utcnow, nullable=True
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    target_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Planning (stored as JSON)
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    estimated_complexity: Mapped[Complexity] = mapped_column(
        Enum(Complexity), nullable=False, default=Complexity.MEDIUM
    )

    # Execution (stored as JSON)
    execution_log: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    checkpoints: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    progress_updates: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    # Artifacts (stored as JSON)
    commits: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    documents: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    outputs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    # Documentation
    dev_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    qa_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    auditor_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Review Status
    self_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    qa_verified: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Quick Context
    quick_context: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    creator: Mapped["AgentTable"] = relationship(
        "AgentTable", foreign_keys=[created_by], lazy="selectin"
    )
    assignee: Mapped["AgentTable | None"] = relationship(
        "AgentTable", foreign_keys=[assigned_to], lazy="selectin"
    )
    parent_task: Mapped["TaskTable | None"] = relationship(
        "TaskTable", remote_side=[id], lazy="selectin"
    )


# =============================================================================
# CHANNEL TABLE
# =============================================================================


class ChannelTable(Base):
    """SQLAlchemy table for channels."""

    __tablename__ = "channels"

    # Identity
    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )
    type: Mapped[ChannelType] = mapped_column(Enum(ChannelType), nullable=False)

    # Description
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    topic: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Access Control
    members: Mapped[list[UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)
    writers: Mapped[list[UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)
    silent_observers: Mapped[list[UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), default=list
    )

    # Settings
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    is_private: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_threads: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_reactions: Mapped[bool] = mapped_column(Boolean, default=True)
    message_retention_days: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=90
    )
    max_message_length: Mapped[int] = mapped_column(Integer, default=10000)

    # Statistics
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    group_count: Mapped[int] = mapped_column(Integer, default=0)
    last_activity: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, onupdate=utcnow, nullable=True
    )

    # Relationships
    groups: Mapped[list["GroupTable"]] = relationship(
        "GroupTable", back_populates="channel", lazy="selectin"
    )


# =============================================================================
# GROUP TABLE
# =============================================================================


class GroupTable(Base):
    """SQLAlchemy table for groups."""

    __tablename__ = "groups"

    # Identity
    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    channel_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Access Control
    allowed_roles: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    hierarchy_level: Mapped[int] = mapped_column(Integer, default=4)
    members: Mapped[list[UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)

    # Settings
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Current Session
    active_session_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # Session Configuration (stored as JSON)
    default_session_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # Statistics
    total_sessions: Mapped[int] = mapped_column(Integer, default=0)
    total_messages: Mapped[int] = mapped_column(Integer, default=0)
    last_activity: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, onupdate=utcnow, nullable=True
    )

    # Relationships
    channel: Mapped["ChannelTable"] = relationship(
        "ChannelTable", back_populates="groups"
    )
    sessions: Mapped[list["SessionTable"]] = relationship(
        "SessionTable", back_populates="group", lazy="selectin"
    )


# =============================================================================
# SESSION TABLE
# =============================================================================


class SessionTable(Base):
    """SQLAlchemy table for sessions."""

    __tablename__ = "sessions"

    # Identity
    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    group_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Boundaries
    max_time_window: Mapped[timedelta | None] = mapped_column(
        Interval, nullable=True, default=timedelta(minutes=30)
    )
    max_message_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=100
    )
    max_content_length: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=50000
    )

    # Timeout
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=300)

    # State
    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus), nullable=False, default=SessionStatus.ACTIVE, index=True
    )

    # Timestamps
    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Statistics
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    total_content_length: Mapped[int] = mapped_column(Integer, default=0)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )

    # Relationships
    group: Mapped["GroupTable"] = relationship("GroupTable", back_populates="sessions")
    messages: Mapped[list["MessageTable"]] = relationship(
        "MessageTable", back_populates="session", lazy="selectin"
    )


# =============================================================================
# MESSAGE TABLE
# =============================================================================


class MessageTable(Base):
    """SQLAlchemy table for messages."""

    __tablename__ = "messages"

    # Identity
    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )

    # Source & Context
    agent_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False, index=True
    )
    channel_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    group_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    session_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Content
    type: Mapped[MessageType] = mapped_column(
        Enum(MessageType), nullable=False, index=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_length: Mapped[int] = mapped_column(Integer, nullable=False)

    # Threading
    is_reply: Mapped[bool] = mapped_column(Boolean, default=False)
    reply_to: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Mentions
    mentions: Mapped[list[UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), default=list
    )

    # Task Context
    task_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    commit_ref: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # Metadata
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )

    # Extraction metadata
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    raw_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Edit tracking (stored as JSON)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    edit_history: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )

    # Relationships
    agent: Mapped["AgentTable"] = relationship("AgentTable", lazy="selectin")
    session: Mapped["SessionTable"] = relationship(
        "SessionTable", back_populates="messages"
    )
    parent_message: Mapped["MessageTable | None"] = relationship(
        "MessageTable", remote_side=[id], lazy="selectin"
    )

    __table_args__ = (
        # Index for efficient channel message queries
        # Index"ix_messages_channel_timestamp", channel_id, timestamp.desc()),
    )


# =============================================================================
# NOTIFICATION TABLE
# =============================================================================


class NotificationTable(Base):
    """SQLAlchemy table for notifications."""

    __tablename__ = "notifications"

    # Identity
    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    type: Mapped[NotificationType] = mapped_column(
        Enum(NotificationType), nullable=False, index=True
    )
    priority: Mapped[NotificationPriority] = mapped_column(
        Enum(NotificationPriority), nullable=False, default=NotificationPriority.NORMAL
    )

    # Routing
    from_agent: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False
    )
    to_agents: Mapped[list[UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False
    )

    # Content
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    # Acknowledgment
    requires_ack: Mapped[bool] = mapped_column(Boolean, default=True)
    acked_by: Mapped[list[UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), default=list
    )
    acked_at: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # Context
    related_task_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    related_message_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), default=list
    )

    # Timing
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Read tracking
    read_by: Mapped[list[UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )

    # Relationships
    sender: Mapped["AgentTable"] = relationship("AgentTable", lazy="selectin")
    related_task: Mapped["TaskTable | None"] = relationship(
        "TaskTable", lazy="selectin"
    )


# =============================================================================
# JOURNAL TABLE
# =============================================================================


class JournalTable(Base):
    """SQLAlchemy table for journals."""

    __tablename__ = "journals"

    # Identity
    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    agent_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    # Metadata
    total_entries: Mapped[int] = mapped_column(Integer, default=0)
    last_entry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Summary
    latest_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Growth metrics (stored as JSON)
    entries_by_type: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, onupdate=utcnow, nullable=True
    )

    # Relationships
    agent: Mapped["AgentTable"] = relationship("AgentTable", lazy="selectin")
    entries: Mapped[list["JournalEntryTable"]] = relationship(
        "JournalEntryTable", back_populates="journal", lazy="selectin"
    )


class JournalEntryTable(Base):
    """SQLAlchemy table for journal entries."""

    __tablename__ = "journal_entries"

    # Identity
    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    journal_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("journals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Content
    type: Mapped[JournalEntryType] = mapped_column(
        Enum(JournalEntryType), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Context
    task_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    session_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Metadata
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)

    # Sentiment
    sentiment: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Visibility
    is_private: Mapped[bool] = mapped_column(Boolean, default=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, onupdate=utcnow, nullable=True
    )

    # Relationships
    journal: Mapped["JournalTable"] = relationship(
        "JournalTable", back_populates="entries"
    )


# =============================================================================
# HANDOFF TABLE
# =============================================================================


class HandoffTable(Base):
    """SQLAlchemy table for documentation handoffs."""

    __tablename__ = "handoffs"

    # Identity
    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    task_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Summary
    summary: Mapped[str] = mapped_column(Text, nullable=False)

    # What Changed (stored as JSON arrays)
    new_functionality: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    modified_behavior: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    breaking_changes: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)

    # Documentation Needed (stored as JSON)
    required_docs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    optional_docs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    # Key Commits (stored as JSON)
    commits: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)

    # Code Locations (stored as JSON)
    new_files: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    modified_files: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)

    # Conversations (stored as JSON)
    key_conversations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    # Code Samples (stored as JSON)
    code_samples: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    # Gotchas (stored as JSON)
    gotchas: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)

    # Related Documentation
    related_docs: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)

    # Changelog Entry
    changelog_entry: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Key Learnings (stored as JSON)
    key_learnings: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    key_decisions: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)

    # Questions
    questions: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)

    # Dev Notes Location
    dev_notes_location: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Status
    status: Mapped[HandoffStatus] = mapped_column(
        Enum(HandoffStatus), nullable=False, default=HandoffStatus.PENDING, index=True
    )
    assigned_to: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, onupdate=utcnow, nullable=True
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Documenter feedback
    documenter_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    task: Mapped["TaskTable"] = relationship("TaskTable", lazy="selectin")
    assignee: Mapped["AgentTable | None"] = relationship("AgentTable", lazy="selectin")

    __table_args__ = (UniqueConstraint("task_id", name="uq_handoffs_task_id"),)
