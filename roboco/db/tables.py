"""
SQLAlchemy Table Definitions

ORM mappings for all RoboCo data models.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID as PyUUID
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Interval,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from roboco.db.base import Base
from roboco.models.a2a import A2AConversationStatus, A2AMessageKind
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    AssignmentScope,
    BlockerResolverType,
    ChannelType,
    Complexity,
    HandoffStatus,
    JournalEntryType,
    MessageType,
    ModelProvider,
    NotificationPriority,
    NotificationType,
    SessionStatus,
    TaskNature,
    TaskStatus,
    TaskType,
    Team,
)
from roboco.models.session import SessionScope
from roboco.models.work_session import WorkSessionStatus

# Python class name → canonical postgres enum name (only the cases where
# the lowercased class name does NOT match the migration's `name=...`).
# Audited from alembic/versions/*.py — every other StrEnum uses
# lower(class_name), so we default to that.
_PG_ENUM_NAME_OVERRIDES: dict[str, str] = {
    # The foundation's `Role` class binds to the postgres `agentrole` enum
    # (see alembic 001 + 012). Without this override, SQLAlchemy infers
    # `role` from the class name, producing the `operator does not exist:
    # agentrole = role` regression that smoke run 2 hit.
    "Role": "agentrole",
}


def _str_enum(enum_cls: type) -> Enum:
    """SQLAlchemy Enum that serializes by `.value` (lowercase) for StrEnum types.

    Matches the lowercase values declared in alembic/versions/001_initial_schema.py.
    Without values_callable, SQLAlchemy uses `.name` (uppercase) which does not
    match the alembic-declared enum members.

    The `name=` is pinned to the canonical postgres enum name so that
    ``Base.metadata.create_all`` (test setup) produces the same enum
    types the migrations create. Default is ``lower(class_name)`` —
    matches every alembic migration's name=...; the only override is
    ``Role`` → ``agentrole`` (E2 fix).
    """
    name = _PG_ENUM_NAME_OVERRIDES.get(enum_cls.__name__, enum_cls.__name__.lower())
    return Enum(
        enum_cls,
        name=name,
        values_callable=lambda obj: [m.value for m in obj],
    )


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
    role: Mapped[AgentRole] = mapped_column(_str_enum(AgentRole), nullable=False)
    team: Mapped[Team | None] = mapped_column(_str_enum(Team), nullable=True)

    # Status
    status: Mapped[AgentStatus] = mapped_column(
        _str_enum(AgentStatus), nullable=False, default=AgentStatus.OFFLINE
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
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=lambda: datetime.now(UTC), nullable=True
    )

    # Relationships - use lazy="joined" for single optional relationship
    current_task: Mapped["TaskTable | None"] = relationship(
        "TaskTable", foreign_keys=[current_task_id], lazy="joined"
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
        _str_enum(TaskStatus), nullable=False, default=TaskStatus.PENDING, index=True
    )
    # Only meaningful when status == BLOCKED. Tells the dispatcher whether
    # another agent can be respawned to resolve the block (`agent`) or
    # whether the block is waiting on a human and spawning is wasted work
    # (`human`). NULL for never-blocked tasks and for pre-existing rows.
    blocker_resolver_type: Mapped[BlockerResolverType | None] = mapped_column(
        _str_enum(BlockerResolverType), nullable=True
    )
    # Agent who raised the current block/escalation. Escalate reassigns the
    # task to the resolver (PM) so they can work the fix, which loses the
    # original dev's identity. We stash it here so `unblock` can flip
    # assignment back and the orchestrator respawns the right agent. NULL
    # = never blocked or pre-migration row.
    blocker_raised_by: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=2)

    # Task Type & Git Configuration (all tasks follow git workflow)
    task_type: Mapped[TaskType] = mapped_column(
        _str_enum(TaskType), nullable=False, default=TaskType.CODE
    )
    nature: Mapped[TaskNature] = mapped_column(
        _str_enum(TaskNature), nullable=False, default=TaskNature.TECHNICAL
    )

    # Project & Branch (branch auto-created on claim).
    # Nullable: a board/fan-out task carries `product_id` (a cell->project map)
    # instead of a single project — it does no git itself; its cell subtasks
    # each resolve a real project from the product. A task must have one or the
    # other (enforced in TaskCreate).
    project_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    product_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    branch_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    work_session_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("work_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )

    # PR Tracking
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pr_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Parallel Execution Tracking (for AWAITING_DOCUMENTATION phase)
    docs_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pr_created: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Board review handoff: a board/coordination task stays pending while the
    # Product Owner + Head of Marketing review it. Set True once both reviewers
    # finish, so the CEO's Approve & Start button appears only after the board
    # is actually done — never on a freshly created pending board task.
    board_review_complete: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

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
    team: Mapped[Team] = mapped_column(_str_enum(Team), nullable=False, index=True)

    # Relationships
    parent_task_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    dependency_ids: Mapped[list[PyUUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), default=list
    )
    blocker_ids: Mapped[list[PyUUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), default=list
    )
    # Dependencies that have since completed and been cleared from
    # ``dependency_ids`` — kept so the unblock briefing can tell the revived
    # dependent which upstream task just landed.
    completed_dependency_ids: Mapped[list[PyUUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), default=list, server_default="{}"
    )

    # Ordering (for sibling tasks under the same parent)
    sequence: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, index=True
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=lambda: datetime.now(UTC), nullable=True
    )
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claimed_by: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    target_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Planning (stored as JSON)
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    estimated_complexity: Mapped[Complexity] = mapped_column(
        _str_enum(Complexity), nullable=False, default=Complexity.MEDIUM
    )

    # Execution (stored as JSON)
    checkpoints: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    progress_updates: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    # Artifacts (stored as JSON)
    commits: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    documents: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    # Documentation
    dev_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    qa_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    auditor_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Review Status
    self_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    qa_verified: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Quick Context
    quick_context: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Proactive Knowledge Context (injected when task is claimed)
    proactive_context: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )

    # Gateway coordination (added in migration 006_gateway_columns).
    # active_claimant_id + last_heartbeat_at implement the single-claimant
    # invariant: only one agent holds a task at a time and they prove
    # liveness via periodic heartbeats. Stale claims are cleaned up by the
    # trigger filter.
    active_claimant_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # pre_block_* snapshot the task's state at the moment it was blocked so
    # `unblock(restore=True)` can return it to its prior status/assignee/
    # metadata instead of dumping the agent into pending.
    pre_block_state: Mapped[str | None] = mapped_column(String(40), nullable=True)
    pre_block_assignee: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )
    pre_block_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    # acceptance_criteria_status: per-criterion records of the form
    # {"criterion": "<text>", "referencing_artifact_id": "<commit-sha|note-id>"}.
    # The tracing gate refuses transitions until every criterion has one.
    acceptance_criteria_status: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    # qa_evidence_inspected: set true by claim_review when the QA agent
    # inspects the inline diff/commits. The pass-review gate refuses the
    # pass transition unless this is true.
    qa_evidence_inspected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # Prompter origin tracking: tasks drafted by the Prompter LLM assistant
    # require human confirmation before entering the workflow. The task creation
    # route enforces that prompter-originated tasks cannot bypass human review.
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="manual")
    confirmed_by_human: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # Relationships
    creator: Mapped["AgentTable"] = relationship(
        "AgentTable", foreign_keys=[created_by], lazy="joined"
    )
    assignee: Mapped["AgentTable | None"] = relationship(
        "AgentTable", foreign_keys=[assigned_to], lazy="joined"
    )
    parent_task: Mapped["TaskTable | None"] = relationship(
        "TaskTable", remote_side=[id], lazy="select"
    )
    project: Mapped["ProjectTable | None"] = relationship(
        "ProjectTable", foreign_keys=[project_id], lazy="joined"
    )
    # Session links (many-to-many via SessionTaskTable).
    # passive_deletes=True tells SA to trust the DB's ON DELETE CASCADE and
    # NOT emit `UPDATE session_tasks SET task_id=NULL` before the delete —
    # the task_id column is NOT NULL, so that pre-null attempt hits an
    # IntegrityError and DELETE /tasks/{id} fails with NotNullViolation.
    session_links: Mapped[list["SessionTaskTable"]] = relationship(
        "SessionTaskTable",
        back_populates="task",
        lazy="select",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        # Composite indexes for common queries
        Index("ix_tasks_team_status", "team", "status"),
        Index("ix_tasks_assigned_status", "assigned_to", "status"),
        Index("ix_tasks_created_by_status", "created_by", "status"),
        Index("ix_tasks_project_status", "project_id", "status"),
        Index("ix_tasks_product_status", "product_id", "status"),
    )


# =============================================================================
# PROJECT TABLE
# =============================================================================


class ProjectTable(Base):
    """SQLAlchemy table for projects (git repositories)."""

    __tablename__ = "projects"

    # Identity
    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )

    # Git Configuration
    git_url: Mapped[str] = mapped_column(String(500), nullable=False)
    default_branch: Mapped[str] = mapped_column(
        String(100), nullable=False, default="master"
    )
    protected_branches: Mapped[list[str]] = mapped_column(
        ARRAY(String), default=lambda: ["main", "master"]
    )
    git_token_encrypted: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # Fernet-encrypted GitHub PAT

    # CI/CD Commands (optional)
    test_command: Mapped[str | None] = mapped_column(String(500), nullable=True)
    lint_command: Mapped[str | None] = mapped_column(String(500), nullable=True)
    format_command: Mapped[str | None] = mapped_column(String(500), nullable=True)
    typecheck_command: Mapped[str | None] = mapped_column(String(500), nullable=True)
    build_command: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Fast pre-submit gate command (lint+types+complexity, no tests). When set,
    # the agent i_am_done gate runs this in the dev's workspace instead of the
    # lint/typecheck pair — e.g. "make gate".
    quality_command: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Access Control
    assigned_cell: Mapped[Team] = mapped_column(_str_enum(Team), nullable=False)
    allowed_agents: Mapped[list[PyUUID] | None] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=True
    )

    # Runtime State
    workspace_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    head_commit: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # Metadata
    created_by: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=lambda: datetime.now(UTC), nullable=True
    )

    # Relationships
    creator: Mapped["AgentTable"] = relationship("AgentTable", lazy="joined")

    __table_args__ = (
        Index("ix_projects_cell", "assigned_cell"),
        Index("ix_projects_active", "is_active"),
    )


class ProductTable(Base):
    """A product groups a per-cell Project mapping (a repo topology)."""

    __tablename__ = "products"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=lambda: datetime.now(UTC), nullable=True
    )

    creator: Mapped["AgentTable"] = relationship("AgentTable", lazy="joined")
    cells: Mapped[list["ProductProjectTable"]] = relationship(
        "ProductProjectTable",
        back_populates="product",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class ProductProjectTable(Base):
    """One Project per cell per Product (the per-cell routing map)."""

    __tablename__ = "product_projects"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    product_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    team: Mapped[Team] = mapped_column(_str_enum(Team), nullable=False)
    project_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    product: Mapped["ProductTable"] = relationship(
        "ProductTable", back_populates="cells"
    )
    project: Mapped["ProjectTable"] = relationship(
        "ProjectTable", foreign_keys=[project_id], lazy="joined"
    )

    __table_args__ = (
        UniqueConstraint("product_id", "team", name="uq_product_projects_product_team"),
    )


class PitchTable(Base):
    """A Board proposal the CEO approves to auto-provision a product.

    Independent of the task lifecycle: a pitch carries its own status
    (proposed -> provisioned/rejected/failed). On approval the provisioning
    flow creates repos, registers Projects (+ a Product when multi-cell), and
    seeds a Main-PM delivery task, recording the produced ids here.
    """

    __tablename__ = "pitches"

    id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )
    problem: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_solution: Mapped[str] = mapped_column(Text, nullable=False)
    target_cells: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="proposed", index=True
    )
    created_by: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    decided_by: Mapped[PyUUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    decision_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    provisioned_product_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    provisioned_project_ids: Mapped[list[str] | None] = mapped_column(
        JSON, nullable=True
    )
    seed_task_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=lambda: datetime.now(UTC), nullable=True
    )


class SecretaryDirectiveTable(Base):
    """One action the Secretary took (or queued) on the CEO's behalf.

    Direct (low-risk) directives are written already ``executed``; gated
    (high-impact) ones are ``pending`` until the CEO confirms, then run. The
    full row is the command audit trail.
    """

    __tablename__ = "secretary_directives"

    id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", index=True
    )
    requested_by: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    decided_by: Mapped[PyUUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    result: Mapped[str | None] = mapped_column(Text, nullable=True)


# =============================================================================
# WORK SESSION TABLE
# =============================================================================


class WorkSessionTable(Base):
    """SQLAlchemy table for work sessions (git working sessions)."""

    __tablename__ = "work_sessions"

    # Identity
    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    project_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Branch Management
    branch_name: Mapped[str] = mapped_column(String(500), nullable=False)
    base_branch: Mapped[str] = mapped_column(String(500), nullable=False)
    target_branch: Mapped[str] = mapped_column(String(500), nullable=False)

    # Lifecycle
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[WorkSessionStatus] = mapped_column(
        _str_enum(WorkSessionStatus),
        nullable=False,
        default=WorkSessionStatus.ACTIVE,
        index=True,
    )

    # Audit Trail
    commits: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    files_modified: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)

    # PR Tracking
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pr_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    pr_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    pr_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    pr_merged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Merge tracking
    merged_by: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=lambda: datetime.now(UTC), nullable=True
    )

    # Relationships
    project: Mapped["ProjectTable"] = relationship("ProjectTable", lazy="joined")
    agent: Mapped["AgentTable | None"] = relationship(
        "AgentTable", foreign_keys=[agent_id], lazy="joined"
    )
    merger: Mapped["AgentTable | None"] = relationship(
        "AgentTable", foreign_keys=[merged_by], lazy="select"
    )

    __table_args__ = (
        Index("ix_work_sessions_project_status", "project_id", "status"),
        Index("ix_work_sessions_task", "task_id"),
        Index("ix_work_sessions_agent_status", "agent_id", "status"),
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
    type: Mapped[ChannelType] = mapped_column(_str_enum(ChannelType), nullable=False)

    # Description
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    topic: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Access Control
    members: Mapped[list[PyUUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), default=list
    )
    writers: Mapped[list[PyUUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), default=list
    )
    silent_observers: Mapped[list[PyUUID]] = mapped_column(
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
    last_activity: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=lambda: datetime.now(UTC), nullable=True
    )

    # Relationships - use lazy="select" for collections to avoid N+1
    groups: Mapped[list["GroupTable"]] = relationship(
        "GroupTable", back_populates="channel", lazy="select"
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
    members: Mapped[list[PyUUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), default=list
    )

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
    last_activity: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=lambda: datetime.now(UTC), nullable=True
    )

    # Relationships - use lazy="select" for collections to avoid N+1
    channel: Mapped["ChannelTable"] = relationship(
        "ChannelTable", back_populates="groups"
    )
    sessions: Mapped[list["SessionTable"]] = relationship(
        "SessionTable", back_populates="group", lazy="select"
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
        _str_enum(SessionStatus),
        nullable=False,
        default=SessionStatus.ACTIVE,
        index=True,
    )

    # Scope (for smart context loading)
    scope: Mapped[SessionScope] = mapped_column(
        _str_enum(SessionScope), nullable=False, default=SessionScope.TASK, index=True
    )

    # Timestamps
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Statistics
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    total_content_length: Mapped[int] = mapped_column(Integer, default=0)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    # Relationships - CRITICAL: use lazy="select" for messages (sessions can have 100+)
    group: Mapped["GroupTable"] = relationship("GroupTable", back_populates="sessions")
    messages: Mapped[list["MessageTable"]] = relationship(
        "MessageTable", back_populates="session", lazy="select"
    )
    # Task links (many-to-many via SessionTaskTable).
    # passive_deletes mirrors the TaskTable side — the DB FK is CASCADE, so
    # deleting a session should cascade-delete session_tasks rows. Without
    # passive_deletes, SA tries to NULL task_id first, violating NOT NULL.
    task_links: Mapped[list["SessionTaskTable"]] = relationship(
        "SessionTaskTable",
        back_populates="session",
        lazy="select",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        # Composite indexes for common queries
        Index("ix_sessions_group_status", "group_id", "status"),
        Index("ix_sessions_status_started", "status", "started_at"),
    )


# =============================================================================
# SESSION-TASK JUNCTION TABLE
# =============================================================================


class SessionTaskTable(Base):
    """
    Junction table for many-to-many Session ↔ Task relationship.

    Enables PMs to create work sessions as discussion contexts for tasks.
    A task can have multiple sessions (planning, review, retrospective).
    A session can discuss multiple related tasks.
    """

    __tablename__ = "session_tasks"

    # Identity
    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )

    # Foreign Keys (indexes defined in __table_args__)
    session_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Relationship Metadata
    is_primary: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )  # Primary discussion session for this task
    relationship_type: Mapped[str] = mapped_column(
        String(50), default="discussion", nullable=False
    )  # "discussion", "planning", "review", "retrospective"

    # Audit
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    added_by: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,  # Allow NULL if PM is deleted
    )

    # Relationships
    session: Mapped["SessionTable"] = relationship(
        "SessionTable", back_populates="task_links", lazy="joined"
    )
    task: Mapped["TaskTable"] = relationship(
        "TaskTable", back_populates="session_links", lazy="joined"
    )
    added_by_agent: Mapped["AgentTable | None"] = relationship(
        "AgentTable", lazy="select"
    )

    __table_args__ = (
        # Each session-task pair is unique
        UniqueConstraint("session_id", "task_id", name="uq_session_task"),
        # Partial unique index: only one primary session per task
        Index(
            "ix_session_tasks_primary_per_task",
            "task_id",
            unique=True,
            postgresql_where=(is_primary.is_(True)),
        ),
        # Fast lookups
        Index("ix_session_tasks_task_id", "task_id"),
        Index("ix_session_tasks_session_id", "session_id"),
        Index("ix_session_tasks_type", "relationship_type"),
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
        _str_enum(MessageType), nullable=False, index=True
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
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
    )

    # Extraction metadata
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    raw_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Edit tracking (stored as JSON)
    edited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    edit_history: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    # Relationships - use lazy="joined" for agent to avoid N+1 on message lists
    agent: Mapped["AgentTable"] = relationship("AgentTable", lazy="joined")
    session: Mapped["SessionTable"] = relationship(
        "SessionTable", back_populates="messages"
    )
    parent_message: Mapped["MessageTable | None"] = relationship(
        "MessageTable", remote_side=[id], lazy="select"
    )

    __table_args__ = (
        # Composite indexes for efficient queries
        Index("ix_messages_channel_timestamp", "channel_id", "timestamp"),
        Index("ix_messages_agent_timestamp", "agent_id", "timestamp"),
        Index("ix_messages_session_timestamp", "session_id", "timestamp"),
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
        _str_enum(NotificationType), nullable=False, index=True
    )
    priority: Mapped[NotificationPriority] = mapped_column(
        _str_enum(NotificationPriority),
        nullable=False,
        default=NotificationPriority.NORMAL,
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
    acked_by: Mapped[list[PyUUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), default=list
    )
    acked_at: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # Context
    related_task_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    related_message_ids: Mapped[list[PyUUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), default=list
    )

    # Timing
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Read tracking
    read_by: Mapped[list[PyUUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), default=list
    )

    # Delivery tracking
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    # Relationships
    sender: Mapped["AgentTable"] = relationship("AgentTable", lazy="joined")
    related_task: Mapped["TaskTable | None"] = relationship("TaskTable", lazy="select")

    __table_args__ = (
        # Composite indexes for notification queries
        Index("ix_notifications_from_agent_timestamp", "from_agent", "timestamp"),
        Index("ix_notifications_type_priority", "type", "priority"),
        Index("ix_notifications_timestamp_priority", "timestamp", "priority"),
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
    last_entry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Summary
    latest_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Growth metrics (stored as JSON)
    entries_by_type: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=lambda: datetime.now(UTC), nullable=True
    )

    # Relationships - use lazy="select" for entries collection to avoid N+1
    agent: Mapped["AgentTable"] = relationship("AgentTable", lazy="joined")
    entries: Mapped[list["JournalEntryTable"]] = relationship(
        "JournalEntryTable", back_populates="journal", lazy="select"
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
        _str_enum(JournalEntryType), nullable=False, index=True
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
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
    )
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)

    # Sentiment
    sentiment: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Visibility
    is_private: Mapped[bool] = mapped_column(Boolean, default=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=lambda: datetime.now(UTC), nullable=True
    )

    # Relationships
    journal: Mapped["JournalTable"] = relationship(
        "JournalTable", back_populates="entries"
    )

    __table_args__ = (
        # Composite indexes for journal entry queries
        Index("ix_journal_entries_journal_timestamp", "journal_id", "timestamp"),
        Index("ix_journal_entries_journal_type", "journal_id", "type"),
        Index("ix_journal_entries_task_id", "task_id"),
    )


# =============================================================================
# HANDOFF TABLE
# =============================================================================


class HandoffTable(Base):
    """
    SQLAlchemy table for structured documentation handoffs.

    STATUS: RESERVED FOR FUTURE USE
    ===============================
    This table exists in the schema but has no service layer or API yet.

    Current Implementation:
        Handoffs use the simpler `dev_notes` + `handoff_summary` parameters
        in the submit/open_pr flow, stored directly on the task.

    Future Enhancement:
        This table enables richer, structured handoff documents with:
        - Categorized changes (new functionality, breaking changes)
        - Required vs optional documentation items
        - Code samples, gotchas, key learnings
        - Linked commits and file locations

        To implement: create HandoffService + API routes + MCP tools.
    """

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
        _str_enum(HandoffStatus),
        nullable=False,
        default=HandoffStatus.PENDING,
        index=True,
    )
    assigned_to: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=lambda: datetime.now(UTC), nullable=True
    )
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Documenter feedback
    documenter_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    task: Mapped["TaskTable"] = relationship("TaskTable", lazy="joined")
    assignee: Mapped["AgentTable | None"] = relationship("AgentTable", lazy="joined")

    __table_args__ = (
        UniqueConstraint("task_id", name="uq_handoffs_task_id"),
        # Indexes for handoff queries
        Index("ix_handoffs_assigned_status", "assigned_to", "status"),
        Index("ix_handoffs_status_created", "status", "created_at"),
    )


# =============================================================================
# INDEXED DOCUMENT TABLE (Knowledge Base tracking)
# =============================================================================


class IndexedDocumentTable(Base):
    """
    Track documents indexed into the knowledge base.

    This provides:
    - Actual document count (vs chunk count from vector DB)
    - Browsing capability for the KB UI
    - Source tracking for re-indexing
    """

    __tablename__ = "indexed_documents"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )

    # Index type (code, docs, conversations, journals, errors, standards, etc.)
    index_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # Source information
    source: Mapped[str] = mapped_column(String(1000), nullable=False)
    source_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # SHA256 for dedup

    # Document title (extracted or filename)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Content preview (first 500 chars for UI)
    preview: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Chunk count for this document
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)

    # Extra data (extracted during indexing)
    extra_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # Timestamps
    indexed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=lambda: datetime.now(UTC), nullable=True
    )

    __table_args__ = (
        # Prevent duplicate indexing of same source
        UniqueConstraint("index_type", "source_hash", name="uq_indexed_doc_source"),
        Index("ix_indexed_docs_type_time", "index_type", "indexed_at"),
    )


# =============================================================================
# A2A CONVERSATION TABLE
# =============================================================================


class A2AConversationTable(Base):
    """
    Persistent A2A conversation between two agents.

    Uses canonical ordering (agent_a < agent_b) for unique pair identification.
    This enables persistent chat history across agent spawns.
    """

    __tablename__ = "a2a_conversations"

    # Identity
    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )

    # The pair (canonical order: lexically smaller first)
    agent_a: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    agent_b: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # Context
    topic: Mapped[str | None] = mapped_column(String(255), nullable=True)
    task_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Status
    status: Mapped[A2AConversationStatus] = mapped_column(
        _str_enum(A2AConversationStatus),
        nullable=False,
        default=A2AConversationStatus.ACTIVE,
        index=True,
    )
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Stats
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unread_by_a: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unread_by_b: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    task: Mapped["TaskTable | None"] = relationship("TaskTable", lazy="select")
    messages: Mapped[list["A2AMessageTable"]] = relationship(
        "A2AMessageTable", back_populates="conversation", lazy="select"
    )

    __table_args__ = (
        # Unique pair + topic combination
        UniqueConstraint("agent_a", "agent_b", "topic", name="uq_a2a_pair_topic"),
        # Ensure canonical ordering
        # CheckConstraint("agent_a < agent_b", name="ck_a2a_agent_order"),
        # Composite indexes
        Index("ix_a2a_conv_pair", "agent_a", "agent_b"),
        Index("ix_a2a_conv_status_updated", "status", "updated_at"),
    )


# =============================================================================
# A2A MESSAGE TABLE
# =============================================================================


class A2AMessageTable(Base):
    """
    Individual message in persistent A2A conversation.

    Supports threading via response_to_id and read tracking.
    """

    __tablename__ = "a2a_messages"

    # Identity
    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    conversation_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("a2a_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Sender (must be agent_a or agent_b from conversation)
    from_agent: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # Content
    content: Mapped[str] = mapped_column(Text, nullable=False)
    message_kind: Mapped[A2AMessageKind] = mapped_column(
        _str_enum(A2AMessageKind),
        nullable=False,
        default=A2AMessageKind.MESSAGE,
    )

    # Threading
    response_to_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("a2a_messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    requires_response: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # Read tracking
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
    )

    # Edit support
    edited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    edit_history: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    # Relationships
    conversation: Mapped["A2AConversationTable"] = relationship(
        "A2AConversationTable", back_populates="messages"
    )
    response_to: Mapped["A2AMessageTable | None"] = relationship(
        "A2AMessageTable", remote_side=[id], lazy="select"
    )

    __table_args__ = (
        # Composite indexes for message queries
        Index("ix_a2a_msg_conv_created", "conversation_id", "created_at"),
        Index("ix_a2a_msg_from_created", "from_agent", "created_at"),
        # Partial index for pending responses
        Index(
            "ix_a2a_msg_pending",
            "conversation_id",
            postgresql_where=(requires_response.is_(True)),
        ),
    )


# =============================================================================
# ORCHESTRATOR WAITING RECORDS
# =============================================================================


class WaitingRecordTable(Base):
    """Persistent backing for orchestrator agents in WAITING_LONG state.

    Previously kept only in `AgentOrchestrator._waiting_records` (in-memory),
    so an orchestrator restart stranded every waiting agent permanently.
    """

    __tablename__ = "waiting_records"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    agent_id: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    task_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    waiting_for: Mapped[str] = mapped_column(String(64), nullable=False)
    waiting_since: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    __table_args__ = (Index("ix_waiting_records_waiting_for", "waiting_for"),)


# =============================================================================
# AUDIT LOG
# =============================================================================


class AuditLogTable(Base):
    """Durable audit events for compliance and the Auditor agent.

    Replaces log-only audit. Dot-separated event_type (task.claimed,
    session.closed, project.deleted, etc.) + JSON details.
    """

    __tablename__ = "audit_log"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    agent_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )
    target_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_id: Mapped[UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    # JSONB (not generic JSON) so the comparator exposes `.astext` —
    # `AuditService.has_recent_tracing_gap` filters
    # `details->>'reason' == 'tracing_gap'`, which the generic JSON Comparator
    # doesn't support (raises AttributeError). JSONB also supports GIN
    # indexing for future audit queries.
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
    )

    __table_args__ = (
        Index("ix_audit_log_agent_timestamp", "agent_id", "timestamp"),
        Index("ix_audit_log_target", "target_type", "target_id"),
    )


# =============================================================================
# PROVIDER ROUTING TABLES
# =============================================================================


class ProviderConfigTable(Base):
    """SQLAlchemy table for model-provider connections.

    One row per "logical provider" — e.g., "Anthropic (default)" (a
    pointer-only row with no secret, served by the mounted ~/.claude auth)
    or "Ollama Cloud Kimi" (holds an encrypted API key and a base URL).
    `ModelAssignmentTable` rows reference these via `provider_config_id`.
    """

    __tablename__ = "provider_configs"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    name: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )
    type: Mapped[ModelProvider] = mapped_column(
        _str_enum(ModelProvider), nullable=False
    )
    # `base_url = NULL` → Anthropic-default path: no ANTHROPIC_BASE_URL
    # injection, Claude Code inside the container uses its mounted
    # ~/.claude credentials. Non-null routes to that endpoint instead.
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Fernet-encrypted auth token (Ollama API key for ollama_cloud rows;
    # NULL for anthropic rows — their auth lives in the mounted directory).
    auth_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=lambda: datetime.now(UTC), nullable=True
    )

    __table_args__ = (Index("ix_provider_configs_enabled", "enabled"),)


class SystemSettingTable(Base):
    """Key-value store for runtime-editable, panel-tunable system settings.

    Operator-tunable values that must persist across restarts and be editable
    from the panel (first user: ``transcript_retention_days``). One row per key;
    the value is stored as text and parsed by the reader. Code defaults in
    ``roboco.config`` are the fallback when a key has no row yet.
    """

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )


class CompanyGoalsTable(Base):
    """Singleton company charter — north star, objectives, operating policy.

    Exactly one row (the all-zeros singleton id). The CEO owns it (writes are
    CEO-only via the API); it is injected compactly into every agent's
    ``context_briefing`` so all work is goal-aware.
    """

    __tablename__ = "company_goals"

    id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    north_star: Mapped[str] = mapped_column(Text, nullable=False, default="")
    objectives: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    constraints: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    operating_policy: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_by: Mapped[PyUUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class ModelAssignmentTable(Base):
    """SQLAlchemy table for (scope, provider, model) routing rows.

    Precedence at spawn time (implemented in `ModelRoutingService`):
        AGENT_SLUG > ROLE > GLOBAL
    with a legacy fallback to `ROLE_MODEL_MAP` when no row applies.
    """

    __tablename__ = "model_assignments"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    scope: Mapped[AssignmentScope] = mapped_column(
        _str_enum(AssignmentScope), nullable=False
    )
    # NULL when scope = 'global'; role name for 'role'; agent slug for 'agent_slug'.
    scope_value: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provider_config_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("provider_configs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Raw Claude Code `--model` identifier (`claude-opus-4-7`,
    # `kimi-k2.6:cloud`, etc.). The orchestrator's CLI-translation only
    # fires for anthropic providers; non-anthropic values pass through.
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=lambda: datetime.now(UTC), nullable=True
    )

    provider: Mapped["ProviderConfigTable"] = relationship(
        "ProviderConfigTable", lazy="joined"
    )

    __table_args__ = (
        # NULLS NOT DISTINCT so the global row (scope_value=NULL) can't be
        # duplicated. Requires PostgreSQL 15+ (roboco runs on pgvector 16).
        Index(
            "ux_model_assignments_scope_key",
            "scope",
            "scope_value",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_model_assignments_provider", "provider_config_id"),
    )


# =============================================================================
# GATEWAY TRIGGER TABLE
# =============================================================================


class GatewayTriggerTable(Base):
    """Records every dispatcher spawn-decision (spawn / queue / drop) for
    observability and gateway-tuning.
    """

    __tablename__ = "gateway_triggers"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    trigger_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    trigger_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    task_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    target_role: Mapped[str] = mapped_column(String(40), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    decision_reason: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    __table_args__ = (
        Index("ix_gateway_triggers_task_id", "task_id"),
        Index("ix_gateway_triggers_created_at", "created_at"),
        Index("ix_gateway_triggers_kind_decision", "trigger_kind", "decision"),
    )


# =============================================================================
# TOKEN USAGE TABLES
# =============================================================================


class AgentSpawnSessionTable(Base):
    """Records each agent container spawn lifecycle.

    Opened when the orchestrator successfully starts a container; closed
    (ended_at set) when stop_agent() finishes.  Final token counts are
    accumulated from the agent SDK's /usage/status endpoint.
    """

    __tablename__ = "agent_spawn_sessions"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    agent_slug: Mapped[str] = mapped_column(String(100), nullable=False)
    team: Mapped[str] = mapped_column(String(50), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # BIGINT — token counts can exceed INT32 for long sessions
    tokens_input: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    tokens_output: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    tokens_cache_read: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    tokens_cache_write: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    exit_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Relationship to snapshots (backref for convenience)
    snapshots: Mapped[list["TokenUsageSnapshotTable"]] = relationship(
        "TokenUsageSnapshotTable",
        back_populates="session",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_agent_spawn_sessions_agent_slug", "agent_slug"),
        Index("ix_agent_spawn_sessions_started_at", "started_at"),
        Index("ix_agent_spawn_sessions_ended_at", "ended_at"),
        Index("ix_agent_spawn_sessions_team", "team"),
    )


class TokenUsageSnapshotTable(Base):
    """Periodic (every ~60 s) snapshot of cumulative token usage for an
    active agent_spawn_session.

    The sweeper inserts one row per active agent per sweep cycle when
    token counts are non-zero.  Snapshots allow tracking how token usage
    grows over session lifetime.
    """

    __tablename__ = "token_usage_snapshots"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    agent_spawn_session_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_spawn_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    snapshotted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    tokens_input: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    tokens_output: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    tokens_cache_read: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    tokens_cache_write: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )

    session: Mapped["AgentSpawnSessionTable"] = relationship(
        "AgentSpawnSessionTable", back_populates="snapshots"
    )

    __table_args__ = (
        Index("ix_token_usage_snapshots_session_id", "agent_spawn_session_id"),
        Index("ix_token_usage_snapshots_snapshotted_at", "snapshotted_at"),
    )


class DailyUsageRollupTable(Base):
    """Pre-aggregated daily token usage per (date, agent_slug, team, model).

    Populated by the orchestrator sweeper via an upsert query over
    closed agent_spawn_sessions.  Unique constraint on the natural key
    enables ON CONFLICT DO UPDATE so the sweep is idempotent.
    """

    __tablename__ = "daily_usage_rollups"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    date: Mapped[Any] = mapped_column(Date, nullable=False)  # datetime.date
    agent_slug: Mapped[str] = mapped_column(String(100), nullable=False)
    team: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    tokens_input: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    tokens_output: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    tokens_cache_read: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    tokens_cache_write: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    total_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    session_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint(
            "date",
            "agent_slug",
            "team",
            "model",
            name="uq_daily_rollup_date_agent_team_model",
        ),
        Index("ix_daily_rollups_date", "date"),
        Index("ix_daily_rollups_agent_slug", "agent_slug"),
    )


# =============================================================================
# PROMPTER TABLES
# =============================================================================


class PrompterSessionTable(Base):
    """A Prompter conversation session owned by an agent."""

    __tablename__ = "prompter_sessions"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    agent_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Enum(
            "active",
            "draft_ready",
            "confirmed",
            "abandoned",
            name="promptersessionstatus",
        ),
        nullable=False,
        default="active",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=lambda: datetime.now(UTC), nullable=True
    )

    # Relationships
    messages: Mapped[list["PrompterMessageTable"]] = relationship(
        "PrompterMessageTable",
        back_populates="session",
        order_by="PrompterMessageTable.created_at",
        cascade="all, delete-orphan",
        lazy="select",
    )
    drafts: Mapped[list["TaskDraftTable"]] = relationship(
        "TaskDraftTable",
        back_populates="session",
        cascade="all, delete-orphan",
        lazy="select",
    )

    __table_args__ = (
        Index("ix_prompter_sessions_agent_id", "agent_id"),
        Index("ix_prompter_sessions_status", "status"),
    )


class PrompterMessageTable(Base):
    """A single message turn within a Prompter conversation session."""

    __tablename__ = "prompter_messages"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    session_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("prompter_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(
        Enum("user", "assistant", "system", name="promptermessagerole"),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    # Relationships
    session: Mapped["PrompterSessionTable"] = relationship(
        "PrompterSessionTable", back_populates="messages"
    )

    __table_args__ = (
        Index("ix_prompter_messages_session_id", "session_id"),
        Index("ix_prompter_messages_session_created", "session_id", "created_at"),
    )


class TaskDraftTable(Base):
    """A structured task draft extracted from a Prompter conversation."""

    __tablename__ = "task_drafts"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    session_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("prompter_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    draft_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    task_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=lambda: datetime.now(UTC), nullable=True
    )

    # Relationships
    session: Mapped["PrompterSessionTable"] = relationship(
        "PrompterSessionTable", back_populates="drafts"
    )

    __table_args__ = (
        Index("ix_task_drafts_session_id", "session_id"),
        Index("ix_task_drafts_task_id", "task_id"),
    )
