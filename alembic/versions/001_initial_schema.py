"""Initial schema - all tables

Revision ID: 001_initial_schema
Revises:
Create Date: 2025-12-09

Creates all tables for the RoboCo AI Agents Company system:
- agents: AI agent definitions and state
- tasks: Work items with lifecycle management
- channels: Communication channels
- groups: Role-based groups within channels
- sessions: Bounded message sessions
- session_tasks: Many-to-many session-task links (PM work sessions)
- messages: Extracted messages
- notifications: Formal notifications
- journals: Agent personal logs
- journal_entries: Individual log entries
- handoffs: Documentation handoffs
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ==========================================================================
    # AGENTS TABLE
    # ==========================================================================
    op.create_table(
        "agents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("slug", sa.String(50), nullable=False, unique=True, index=True),
        sa.Column(
            "role",
            sa.Enum(
                "ceo",
                "product_owner",
                "head_marketing",
                "auditor",
                "main_pm",
                "cell_pm",
                "developer",
                "qa",
                "documenter",
                name="agentrole",
            ),
            nullable=False,
        ),
        sa.Column(
            "team",
            sa.Enum("backend", "frontend", "ux_ui", "board", name="team"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.Enum("active", "idle", "offline", name="agentstatus"),
            nullable=False,
            server_default="offline",
        ),
        sa.Column("current_task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("model_config", postgresql.JSON(), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("capabilities", postgresql.ARRAY(sa.String()), server_default="{}"),
        sa.Column("permissions", postgresql.JSON(), server_default="{}"),
        sa.Column("metrics", postgresql.JSON(), server_default="{}"),
        sa.Column("journal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    # ==========================================================================
    # TASKS TABLE
    # ==========================================================================
    op.create_table(
        "tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("acceptance_criteria", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "claimed",
                "in_progress",
                "blocked",
                "paused",
                "verifying",
                "needs_revision",
                "awaiting_qa",
                "awaiting_documentation",
                "completed",
                "cancelled",
                name="taskstatus",
            ),
            nullable=False,
            server_default="pending",
            index=True,
        ),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="2"),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id"),
            nullable=False,
        ),
        sa.Column(
            "assigned_to",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "team",
            sa.Enum(
                "backend", "frontend", "ux_ui", "board", name="team", create_type=False
            ),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "parent_task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "dependency_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            server_default="{}",
        ),
        sa.Column(
            "blocker_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            server_default="{}",
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("target_date", sa.DateTime(), nullable=True),
        sa.Column("plan", postgresql.JSON(), nullable=True),
        sa.Column(
            "estimated_complexity",
            sa.Enum("low", "medium", "high", name="complexity"),
            nullable=False,
            server_default="medium",
        ),
        sa.Column("execution_log", postgresql.JSON(), server_default="{}"),
        sa.Column("checkpoints", postgresql.JSON(), server_default="[]"),
        sa.Column("progress_updates", postgresql.JSON(), server_default="[]"),
        sa.Column("commits", postgresql.JSON(), server_default="[]"),
        sa.Column("documents", postgresql.JSON(), server_default="[]"),
        sa.Column("outputs", postgresql.JSON(), server_default="[]"),
        sa.Column("dev_notes", sa.Text(), nullable=True),
        sa.Column("qa_notes", sa.Text(), nullable=True),
        sa.Column("auditor_notes", sa.Text(), nullable=True),
        sa.Column("self_verified", sa.Boolean(), server_default="false"),
        sa.Column("qa_verified", sa.Boolean(), nullable=True),
        sa.Column("quick_context", sa.Text(), nullable=True),
    )

    # Add foreign key for agents.current_task_id after tasks table exists
    op.create_foreign_key(
        "fk_agents_current_task",
        "agents",
        "tasks",
        ["current_task_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # ==========================================================================
    # CHANNELS TABLE
    # ==========================================================================
    op.create_table(
        "channels",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("slug", sa.String(50), nullable=False, unique=True, index=True),
        sa.Column(
            "type",
            sa.Enum("cell", "cross_cell", "management", "special", name="channeltype"),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("topic", sa.String(500), nullable=True),
        sa.Column(
            "members",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            server_default="{}",
        ),
        sa.Column(
            "writers",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            server_default="{}",
        ),
        sa.Column(
            "silent_observers",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            server_default="{}",
        ),
        sa.Column("is_archived", sa.Boolean(), server_default="false"),
        sa.Column("is_private", sa.Boolean(), server_default="false"),
        sa.Column("allow_threads", sa.Boolean(), server_default="true"),
        sa.Column("allow_reactions", sa.Boolean(), server_default="true"),
        sa.Column(
            "message_retention_days", sa.Integer(), nullable=True, server_default="90"
        ),
        sa.Column("max_message_length", sa.Integer(), server_default="10000"),
        sa.Column("message_count", sa.Integer(), server_default="0"),
        sa.Column("group_count", sa.Integer(), server_default="0"),
        sa.Column("last_activity", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    # ==========================================================================
    # GROUPS TABLE
    # ==========================================================================
    op.create_table(
        "groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("allowed_roles", postgresql.ARRAY(sa.String()), server_default="{}"),
        sa.Column("hierarchy_level", sa.Integer(), server_default="4"),
        sa.Column(
            "members",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            server_default="{}",
        ),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column("active_session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("default_session_config", postgresql.JSON(), server_default="{}"),
        sa.Column("total_sessions", sa.Integer(), server_default="0"),
        sa.Column("total_messages", sa.Integer(), server_default="0"),
        sa.Column("last_activity", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    # ==========================================================================
    # SESSIONS TABLE
    # ==========================================================================
    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "group_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("max_time_window", sa.Interval(), nullable=True),
        sa.Column(
            "max_message_count", sa.Integer(), nullable=True, server_default="100"
        ),
        sa.Column(
            "max_content_length", sa.Integer(), nullable=True, server_default="50000"
        ),
        sa.Column("timeout_seconds", sa.Integer(), server_default="300"),
        sa.Column(
            "status",
            sa.Enum("active", "closed", "timed_out", name="sessionstatus"),
            nullable=False,
            server_default="active",
            index=True,
        ),
        sa.Column(
            "scope",
            sa.Enum("initiative", "cell", "task", name="sessionscope"),
            nullable=False,
            server_default="task",
            index=True,
        ),
        sa.Column(
            "started_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "last_activity_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("message_count", sa.Integer(), server_default="0"),
        sa.Column("total_content_length", sa.Integer(), server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
    )

    # ==========================================================================
    # SESSION_TASKS TABLE (Many-to-Many Junction)
    # ==========================================================================
    op.create_table(
        "session_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "relationship_type",
            sa.String(50),
            nullable=False,
            server_default="discussion",
        ),
        sa.Column(
            "added_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "added_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint("session_id", "task_id", name="uq_session_task"),
    )

    # Partial unique index: only one primary session per task
    op.execute(
        """
        CREATE UNIQUE INDEX ix_session_tasks_primary_per_task
        ON session_tasks (task_id)
        WHERE is_primary = true
        """
    )

    # ==========================================================================
    # MESSAGES TABLE
    # ==========================================================================
    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "group_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "type",
            sa.Enum(
                "reasoning",
                "dialogue",
                "decision",
                "action",
                "blocker",
                "technical",
                name="messagetype",
            ),
            nullable=False,
            index=True,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_length", sa.Integer(), nullable=False),
        sa.Column("is_reply", sa.Boolean(), server_default="false"),
        sa.Column(
            "reply_to",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "mentions",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            server_default="{}",
        ),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("commit_ref", sa.String(40), nullable=True),
        sa.Column(
            "timestamp",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            index=True,
        ),
        sa.Column("confidence", sa.Float(), server_default="1.0"),
        sa.Column("raw_excerpt", sa.Text(), nullable=True),
        sa.Column("edited_at", sa.DateTime(), nullable=True),
        sa.Column("edit_history", postgresql.JSON(), server_default="[]"),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
    )

    # ==========================================================================
    # NOTIFICATIONS TABLE
    # ==========================================================================
    op.create_table(
        "notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "type",
            sa.Enum(
                "task_assignment",
                "priority_change",
                "blocker_escalation",
                "review_request",
                "documentation_request",
                "alert",
                "broadcast",
                name="notificationtype",
            ),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "priority",
            sa.Enum("normal", "high", "urgent", name="notificationpriority"),
            nullable=False,
            server_default="normal",
        ),
        sa.Column(
            "from_agent",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id"),
            nullable=False,
        ),
        sa.Column(
            "to_agents", postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=False
        ),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("requires_ack", sa.Boolean(), server_default="true"),
        sa.Column(
            "acked_by",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            server_default="{}",
        ),
        sa.Column("acked_at", postgresql.JSON(), server_default="{}"),
        sa.Column(
            "related_task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "related_message_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            server_default="{}",
        ),
        sa.Column(
            "timestamp",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            index=True,
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column(
            "read_by",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            server_default="{}",
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
    )

    # ==========================================================================
    # JOURNALS TABLE
    # ==========================================================================
    op.create_table(
        "journals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("total_entries", sa.Integer(), server_default="0"),
        sa.Column("last_entry_at", sa.DateTime(), nullable=True),
        sa.Column("latest_summary", sa.Text(), nullable=True),
        sa.Column("summary_updated_at", sa.DateTime(), nullable=True),
        sa.Column("entries_by_type", postgresql.JSON(), server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    # ==========================================================================
    # JOURNAL ENTRIES TABLE
    # ==========================================================================
    op.create_table(
        "journal_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "journal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("journals.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "type",
            sa.Enum(
                "task_reflection",
                "decision_log",
                "learning",
                "struggle",
                "general",
                name="journalentrytype",
            ),
            nullable=False,
            index=True,
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "timestamp",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            index=True,
        ),
        sa.Column("tags", postgresql.ARRAY(sa.String()), server_default="{}"),
        sa.Column("sentiment", sa.String(50), nullable=True),
        sa.Column("is_private", sa.Boolean(), server_default="false"),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    # ==========================================================================
    # HANDOFFS TABLE
    # ==========================================================================
    op.create_table(
        "handoffs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "new_functionality", postgresql.ARRAY(sa.String()), server_default="{}"
        ),
        sa.Column(
            "modified_behavior", postgresql.ARRAY(sa.String()), server_default="{}"
        ),
        sa.Column(
            "breaking_changes", postgresql.ARRAY(sa.String()), server_default="{}"
        ),
        sa.Column("required_docs", postgresql.JSON(), server_default="[]"),
        sa.Column("optional_docs", postgresql.JSON(), server_default="[]"),
        sa.Column("commits", postgresql.JSON(), server_default="[]"),
        sa.Column("new_files", postgresql.JSON(), server_default="[]"),
        sa.Column("modified_files", postgresql.JSON(), server_default="[]"),
        sa.Column("key_conversations", postgresql.JSON(), server_default="[]"),
        sa.Column("code_samples", postgresql.JSON(), server_default="[]"),
        sa.Column("gotchas", postgresql.JSON(), server_default="[]"),
        sa.Column("related_docs", postgresql.ARRAY(sa.String()), server_default="{}"),
        sa.Column("changelog_entry", sa.Text(), nullable=True),
        sa.Column("key_learnings", postgresql.ARRAY(sa.String()), server_default="{}"),
        sa.Column("key_decisions", postgresql.JSON(), server_default="[]"),
        sa.Column("questions", postgresql.ARRAY(sa.String()), server_default="{}"),
        sa.Column("dev_notes_location", sa.String(500), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "claimed", "in_progress", "completed", name="handoffstatus"
            ),
            nullable=False,
            server_default="pending",
            index=True,
        ),
        sa.Column(
            "assigned_to",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("documenter_notes", sa.Text(), nullable=True),
        sa.UniqueConstraint("task_id", name="uq_handoffs_task_id"),
    )

    # ==========================================================================
    # PERFORMANCE INDEXES
    # ==========================================================================

    # Composite indexes for common queries
    op.create_index(
        "ix_tasks_team_status",
        "tasks",
        ["team", "status"],
    )
    op.create_index(
        "ix_tasks_assigned_status",
        "tasks",
        ["assigned_to", "status"],
    )
    op.create_index(
        "ix_messages_channel_timestamp",
        "messages",
        ["channel_id", sa.text("timestamp DESC")],
    )
    op.create_index(
        "ix_notifications_to_timestamp",
        "notifications",
        [sa.text("timestamp DESC")],
    )


def downgrade() -> None:
    # Drop indexes
    op.drop_index("ix_notifications_to_timestamp", table_name="notifications")
    op.drop_index("ix_messages_channel_timestamp", table_name="messages")
    op.drop_index("ix_tasks_assigned_status", table_name="tasks")
    op.drop_index("ix_tasks_team_status", table_name="tasks")

    # Drop tables in reverse order
    op.drop_table("handoffs")
    op.drop_table("journal_entries")
    op.drop_table("journals")
    op.drop_table("notifications")
    op.drop_table("messages")
    op.drop_index("ix_session_tasks_primary_per_task", table_name="session_tasks")
    op.drop_table("session_tasks")
    op.drop_table("sessions")
    op.drop_table("groups")
    op.drop_table("channels")

    # Drop FK before dropping tasks
    op.drop_constraint("fk_agents_current_task", "agents", type_="foreignkey")

    op.drop_table("tasks")
    op.drop_table("agents")

    # Drop enums
    op.execute("DROP TYPE IF EXISTS handoffstatus")
    op.execute("DROP TYPE IF EXISTS journalentrytype")
    op.execute("DROP TYPE IF EXISTS notificationpriority")
    op.execute("DROP TYPE IF EXISTS notificationtype")
    op.execute("DROP TYPE IF EXISTS messagetype")
    op.execute("DROP TYPE IF EXISTS sessionstatus")
    op.execute("DROP TYPE IF EXISTS sessionscope")
    op.execute("DROP TYPE IF EXISTS channeltype")
    op.execute("DROP TYPE IF EXISTS complexity")
    op.execute("DROP TYPE IF EXISTS taskstatus")
    op.execute("DROP TYPE IF EXISTS agentstatus")
    op.execute("DROP TYPE IF EXISTS team")
    op.execute("DROP TYPE IF EXISTS agentrole")
