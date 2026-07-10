"""
SQLAlchemy Table Definitions

ORM mappings for all RoboCo data models.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
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
    String,
    Text,
    UniqueConstraint,
    text,
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
    Complexity,
    HandoffStatus,
    JournalEntryType,
    ModelProvider,
    NotificationPriority,
    NotificationType,
    TaskNature,
    TaskStatus,
    TaskType,
    Team,
)
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
    # Server-derived architectural constraints (project baseline conventions
    # block), moved out of `description` (migration 068). Nullable: flag-off /
    # no-conventions / pre-migration rows have none. The conventions also
    # reach the agent at spawn via the ambient block — this column is for
    # panel visibility, not agent correctness.
    constraints: Mapped[str | None] = mapped_column(Text, nullable=True)
    acceptance_criteria: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False
    )
    # Stable id per acceptance_criteria element (1:1, same order). Lets a child
    # task's parent_ac_refs point at specific parent criteria (migration 036).
    acceptance_criteria_ids: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    # On a decomposition child: the parent AC ids this child is responsible for
    # (empty on non-children). Powers the coverage + roll-up AC gates.
    parent_ac_refs: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )

    if TYPE_CHECKING:
        # ponytail: transient, non-persisted — set only by TaskService.cancel()
        # to surface orphaned parent-AC coverage without a response-schema
        # change. Declared here only so mypy accepts the attribute; SQLAlchemy
        # never sees it (TYPE_CHECKING is False at runtime).
        orphaned_parent_acs: list[str] | None

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
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
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

    # Sequenced batch intake ("Mega task"): a batch of top-level tasks created
    # together. ``batch_id`` groups them; the three descriptors are the per-task
    # collision surface the SequencingService reads to compute dependency waves.
    batch_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    intends_to_touch: Mapped[list[str] | None] = mapped_column(
        ARRAY(String), nullable=True
    )
    adds_migration: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    touches_shared: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
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
    # Rework: incremented on every transition into needs_revision so the
    # rework rate is an O(1) column read instead of an audit_log scan.
    revision_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    # Quick Context
    quick_context: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Proactive Knowledge Context (injected when task is claimed)
    proactive_context: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )

    # Structured content (migration 041). notes_structured is the typed source
    # of truth for every role's note; the TEXT note columns above are a derived
    # mirror. orchestration_markers holds the machine markers split out of
    # quick_context (never human-facing). pr_reviewer_notes is the reviewer's
    # own slot so a review no longer overwrites qa_notes / dev_notes.
    pr_reviewer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    doc_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes_structured: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    orchestration_markers: Mapped[dict[str, Any] | None] = mapped_column(
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
    # Per-cell project map for an ad-hoc (non-Product) coordination root —
    # mirrors product_projects but is owned by the task itself, so a MegaTask
    # root-subtask can target a per-cell map mixing projects from different
    # products / OSS libs. selectin so the fan-out resolvers see the map on
    # any task fetch; passive_deletes trusts the ON DELETE CASCADE FK.
    cell_projects: Mapped[list["TaskCellProjectTable"]] = relationship(
        "TaskCellProjectTable",
        back_populates="task",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
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

    # Autonomous maintenance opt-in (multi-repo CI-watch). Default-off: a
    # project is watched only when ci_watch_enabled is set; ci_watch_workflow
    # scopes the CI signal to one workflow file (null → the engine default).
    ci_watch_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )
    ci_watch_workflow: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Video-engine opt-in. The global ROBOCO_VIDEO_ENGINE_ENABLED flag arms the
    # subsystem; a project opts in via video_engine_enabled before any
    # authoring task opens against its motion/ dir. Default-off, mirroring
    # ci_watch_enabled (migration 048).
    video_engine_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )

    # Dependency-update bot opt-in. A project participates only when
    # dep_update_command is set (e.g. "uv lock --upgrade"); dep_update_paths are
    # the lockfile globs the probe inspects (null → infer uv.lock/pnpm-lock.yaml).
    dep_update_command: Mapped[str | None] = mapped_column(String(500), nullable=True)
    dep_update_paths: Mapped[list[str] | None] = mapped_column(
        ARRAY(String), nullable=True
    )

    # Sandboxed per-agent-spawn DB/Redis opt-in. A project participates only
    # when sandbox_services is set (e.g. ["postgres", "redis"]); values are
    # validated by the Project pydantic model before reaching here.
    sandbox_services: Mapped[list[str] | None] = mapped_column(
        ARRAY(String), nullable=True
    )

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


class TaskCellProjectTable(Base):
    """One Project per cell for an ad-hoc (non-Product) coordination root.

    Mirrors ``product_projects`` but the map is owned by the task, not a Product:
    a MegaTask root-subtask that spans multiple cells (and may mix per-cell
    projects from different products / OSS libs) carries its per-cell routing
    here instead of a ``product_id``. The root itself does git per repo (it cuts
    ``feature/main_pm/{root}`` and opens a root->master PR per repo exactly like a
    Product fan-out root); the cell children each resolve their project from this
    map via ``_resolve_subtask_project``. ``UNIQUE (task_id, team)`` enforces one
    project per cell per task.
    """

    __tablename__ = "task_cell_projects"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    task_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
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

    task: Mapped["TaskTable"] = relationship(
        "TaskTable", back_populates="cell_projects"
    )
    project: Mapped["ProjectTable"] = relationship(
        "ProjectTable", foreign_keys=[project_id], lazy="joined"
    )

    __table_args__ = (
        UniqueConstraint("task_id", "team", name="uq_task_cell_projects_task_team"),
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


class PlaybookTable(Base):
    """A curated, reusable procedure: when to use it + the steps.

    A *learning* records "this happened"; a *playbook* records "here is how to do
    X". An agent drafts one (status=draft); the Auditor approves it
    (status=approved) and only then is it embedded into the PLAYBOOKS RAG index
    and auto-suggested. Orthogonal to the task lifecycle — its own entity, no
    task status. Status is a plain string (the PlaybookStatus StrEnum carries the
    valid values at the service layer), matching the pitches convention.
    """

    __tablename__ = "playbooks"

    id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(
        String(80), unique=True, nullable=False, index=True
    )
    problem: Mapped[str] = mapped_column(Text, nullable=False)
    procedure: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    team: Mapped[str | None] = mapped_column(String(20), nullable=True)
    scope: Mapped[str] = mapped_column(String(20), nullable=False, default="org")
    source_task_ids: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", index=True
    )
    created_by: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    approved_by: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Distinct from approval: who retired it (archive/reject) and when. Stamping
    # the archiver into approved_by overwrote the approval provenance.
    archived_by: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Durable index-state: False on the approve status flip, set True only
    # after a successful index_playbook. A startup reconcile re-indexes
    # APPROVED rows left False by a mid-approval embedder outage.
    indexed_ok: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    indexed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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
    agent_id: Mapped[UUID | None] = mapped_column(
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

    # Toolchain matching — the Python the workspace was provisioned with, and
    # whether the project's test suite can actually be executed in it.
    toolchain_python: Mapped[str | None] = mapped_column(String(20), nullable=True)
    toolchain_status: Mapped[str | None] = mapped_column(String(20), nullable=True)

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
        # A task is owned by one agent at a time → at most one ACTIVE session.
        # Without this, a re-claim by a different agent left duplicate ACTIVE
        # rows and get_active_for_task crashed with MultipleResultsFound,
        # wedging i_will_plan into a respawn loop. Mirrored by migration 047.
        Index(
            "uq_work_sessions_one_active_per_task",
            "task_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
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
    # Capability this A2A concerns (e.g. ``code_review``); nullable for legacy /
    # unspecified messages. Migration 054.
    skill: Mapped[str | None] = mapped_column(String(100), nullable=True)

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


class RespawnTrackerTable(Base):
    """Persistent backing for the orchestrator's PM-respawn loop counter.

    ``AgentOrchestrator._pm_respawn_tracker`` is the circuit breaker against
    respawning the same PM on the same task forever. Kept only in memory it
    reset to ``count=1`` on every orchestrator restart, re-burning the whole
    strike threshold against a still-wedged task. This mirror survives a
    restart. Default-on; inert when empty (degrades to in-memory-only).

    ``task_id`` is deliberately NOT a FK to ``tasks``: the startup loader
    validates each row against live tasks (skipping terminal/missing ones), so
    a stale counter can never resurrect against a fixed/deleted task and the
    deletion authority stays in one explicit, tested place.
    """

    __tablename__ = "respawn_tracker"

    agent_slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_check: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    tracing_resets: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revisit_resets: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    __table_args__ = (Index("ix_respawn_tracker_last_check", "last_check"),)


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
        # Powers the observability cycle-time / rework reconstruction: per-task
        # transition journeys are read by (target_id, event_type) ordered by time.
        Index("ix_audit_log_target_event_ts", "target_id", "event_type", "timestamp"),
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
    # CEO-authored brand-voice sample/direction — a first-class, long charter
    # field (like north_star), not folded into the catch-all operating_policy
    # JSON blob, so it gets the same discoverability. Feeds XEngine._voice_guide
    # and the Head of Marketing's briefing; empty until the CEO sets it.
    brand_voice: Mapped[str] = mapped_column(Text, nullable=False, default="")
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
    # LLM iterations (unique assistant messages) + tool invocations for this
    # stint, captured at finalize from the SDK /usage/status (turns has a
    # transcript fallback). Default 0 — historical/Grok rows read 0 ("n/a").
    turns: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    tool_calls: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
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


class MemberPerformanceDailyTable(Base):
    """Pre-aggregated daily per-member performance (the granular scorecard rollup).

    One row per (date, member_kind, agent_slug), populated by the orchestrator
    sweeper from agent_spawn_sessions + audit_log. The CEO is a first-class
    ``member_kind='ceo'`` row with ``agent_slug=''`` — a distinct natural-key
    tuple from every ``member_kind='agent'`` row, so it never collides.
    Overwrite-upsert on the natural key makes the sweep idempotent. All counters
    default 0.
    """

    __tablename__ = "member_performance_daily"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    date: Mapped[Any] = mapped_column(Date, nullable=False)  # datetime.date
    member_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    agent_slug: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    team: Mapped[str | None] = mapped_column(String(50), nullable=True)
    role: Mapped[str | None] = mapped_column(String(50), nullable=True)
    tasks_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tasks_first_pass: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revisions_caused: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revisions_received: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_runtime_seconds: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    turns: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    ceo_approval_dwell_seconds: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    ceo_unblock_dwell_seconds: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    godmode_actions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # The four CEO-approved extras (+ blocked_seconds).
    qa_reviews_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    qa_reviews_passed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    escalations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    blocked_others: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    idle_seconds: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    blocked_seconds: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint(
            "date", "member_kind", "agent_slug", name="uq_member_perf_day"
        ),
        Index("ix_member_perf_date", "date"),
        Index("ix_member_perf_agent_slug", "agent_slug"),
        Index("ix_member_perf_team", "team"),
        Index("ix_member_perf_kind", "member_kind"),
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


# =============================================================================
# PROJECT CONVENTIONS CACHE TABLE
# =============================================================================


class ProjectConventionsCacheTable(Base):
    """Cached effective conventions map, keyed by (project, commit SHA).

    The effective map (auto-derived defaults overlaid by the committed
    ``.roboco/conventions.yml``) is re-parsed only when HEAD moves; every
    consumer reads this cache. ``status`` records how the file resolved at that
    SHA: ``ok`` | ``degraded`` (unparseable, fell back) | ``missing``.
    """

    __tablename__ = "project_conventions_cache"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    project_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    effective_map: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    derived_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "project_id", "commit_sha", name="uq_project_conventions_cache_sha"
        ),
    )


# =============================================================================
# PROJECT CONVENTION FINDINGS TABLE (violations feed)
# =============================================================================


class ProjectConventionFindingTable(Base):
    """A persisted architectural-conventions finding for the violations feed.

    Records the latest validator findings per task (re-recorded on each check —
    ``ConventionsService.record_findings`` replaces a task's rows), so the panel
    can show recent block/warn violations across a project and drift stays
    visible. Written best-effort from the i_am_done gate in its own committed
    session, so a finding that *blocked* the submit is still captured.
    """

    __tablename__ = "project_convention_findings"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    project_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task_id: Mapped[UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    file: Mapped[str] = mapped_column(String(500), nullable=False)
    line: Mapped[int] = mapped_column(Integer, nullable=False)
    rule: Mapped[str] = mapped_column(String(100), nullable=False)
    level: Mapped[str] = mapped_column(String(20), nullable=False)
    kind: Mapped[str | None] = mapped_column(String(40), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
    )


# =============================================================================
# CLOUD AUTH USERS TABLE (FastAPI Users schema)
# =============================================================================


class UserTable(Base):
    """The single seeded CEO login user for cloud auth (default off).

    Field set matches the FastAPI Users ``UserProtocol`` (structural typing —
    no mixin inheritance needed): id/email/hashed_password/is_active/
    is_superuser/is_verified. No registration router is mounted; the one row
    is idempotently upserted at startup from cloud_auth_email/password
    (``roboco.api.auth.seed.ensure_seed_user``).

    The ``TYPE_CHECKING`` split (bare types vs. ``Mapped[...]``) mirrors
    ``fastapi_users_db_sqlalchemy``'s own base table: mypy's structural
    Protocol check needs the plain-type view to accept this class as a
    ``UserProtocol``; the ``else`` branch is what SQLAlchemy actually builds
    at runtime.
    """

    __tablename__ = "users"

    if TYPE_CHECKING:
        id: PyUUID
        email: str
        hashed_password: str
        is_active: bool
        is_superuser: bool
        is_verified: bool
    else:
        id: Mapped[UUID] = mapped_column(
            UUID(as_uuid=True), primary_key=True, default=uuid4
        )
        email: Mapped[str] = mapped_column(
            String(320), unique=True, index=True, nullable=False
        )
        hashed_password: Mapped[str] = mapped_column(String(1024), nullable=False)
        is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
        is_superuser: Mapped[bool] = mapped_column(
            Boolean, default=False, nullable=False
        )
        is_verified: Mapped[bool] = mapped_column(
            Boolean, default=False, nullable=False
        )


# =============================================================================
# X (TWITTER) ACCOUNT TABLES
# =============================================================================


class XCredentialsTable(Base):
    """Singleton row holding the Fernet-encrypted X OAuth 1.0a user-context
    secrets (mirrors ``ProviderConfigTable``'s encrypted-token column). At most
    one row ever exists; ``XCredentialsService`` upserts it. Never read by an
    agent — decrypted only server-side, by ``x_client``."""

    __tablename__ = "x_credentials"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_token_secret_encrypted: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=lambda: datetime.now(UTC), nullable=True
    )


class XSeenMentionTable(Base):
    """Dedup ledger for the mentions poll — one row per mention id the engine
    has ever turned into a held reply proposal (or decided to skip). Never
    pruned by task terminal state, so a rejected/completed proposal's mention
    is never re-proposed on a later cycle."""

    __tablename__ = "x_seen_mentions"

    mention_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class XSeenFeatureTable(Base):
    """Dedup ledger for feature-spotlight drafts — one row per feature slug the
    engine has ever turned into a held spotlight. Prevents re-covering the same
    shipped capability on a later cycle. Never pruned by task terminal state."""

    __tablename__ = "x_seen_features"

    feature_slug: Mapped[str] = mapped_column(String(128), primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


# =============================================================================
# TIKTOK ACCOUNT TABLES
# =============================================================================


class TikTokCredentialsTable(Base):
    """Singleton row holding the Fernet-encrypted TikTok OAuth2 secrets
    (mirrors ``XCredentialsTable``). Unlike X's OAuth 1.0a secrets,
    access_token/refresh_token rotate on refresh — ``TikTokCredentialsService.
    update_tokens`` is the narrower write for that path; client_key/
    client_secret never change post-setup. Never read by an agent — decrypted
    only server-side, by ``tiktok_client``."""

    __tablename__ = "tiktok_credentials"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    client_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=lambda: datetime.now(UTC), nullable=True
    )


# =============================================================================
# RAG INDEX DEAD-LETTER
# =============================================================================


class RagIndexFailureTable(Base):
    """Dead-letter for fire-and-forget RAG index writes that dropped on failure.

    ``_schedule_rag_index`` (journal) and ``_extract_completion_learnings``
    (task) index off the critical path: an embedder 429 after retries was
    swallowed + logged, leaving the entry invisible to ``optimal.search`` /
    ``similar_memory`` though it lived in the DB. A failure here is persisted
    instead of dropped; a startup janitor reclaims due rows with backoff — on
    success the row is deleted, on failure ``attempts`` bumps and
    ``next_retry_at`` advances. Best-effort: a failed index never blocks the
    caller's commit.
    """

    __tablename__ = "rag_index_failures"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    doc_source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_error: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    next_retry_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
