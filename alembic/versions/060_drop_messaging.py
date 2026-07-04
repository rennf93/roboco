"""Drop the channels/groups/sessions/session_tasks/messages subsystem.

Channels, groups, discussion-sessions, and messages are retired — A2A is
now the single directed-message channel agents read, and coordination
rests on the task state machine + task details (see
docs/internal/specs/2026-07-03-comms-teardown-trace.md). All backend code
that read/wrote these tables was removed first (roboco commits preceding
this one); this migration is the last step, once nothing touches them.

Revision ID: 060_drop_messaging
Revises: 059_x_credentials
Create Date: 2026-07-03
"""

from __future__ import annotations

from alembic import op

revision = "060_drop_messaging"
down_revision = "059_x_credentials"
branch_labels: dict[str, str] | None = None
depends_on: dict[str, str] | None = None


def upgrade() -> None:
    # Dropping the column drops its FK automatically, regardless of the
    # constraint's actual name — sidesteps having to hardcode it. (The
    # naming-convention name would be fk_journal_entries_session_id_sessions
    # per db/base.py's convention, and that IS what a live DB shows, but
    # 001_initial_schema.py created it via a raw op.create_table() against
    # a throwaway MetaData(), so the convention isn't guaranteed to apply —
    # op.drop_column is correct either way.)
    op.drop_column("journal_entries", "session_id")

    # Drop order follows the FK chain: messages -> session_tasks -> sessions
    # -> groups -> channels.
    op.drop_table("messages")
    op.drop_table("session_tasks")
    op.drop_table("sessions")
    op.drop_table("groups")
    op.drop_table("channels")

    # The CONVERSATIONS RAG chunk table is runtime-provisioned (CREATE TABLE
    # IF NOT EXISTS, migration 030's index plugin) — not alembic-managed, so
    # it survives the model deletion unless dropped explicitly here.
    op.execute("DROP TABLE IF EXISTS chunks_conversations")

    # messagetype is dropped because MessageTable (the only column that used
    # it) is gone, but the MessageType Python enum stays — ExtractedMessage
    # (kept, extraction pipeline) still uses it, and was never itself
    # persisted to messages/MessageTable. The DB type and the Python enum
    # are independent; dropping the now-unused DB type is safe.
    for t in ("messagetype", "sessionstatus", "sessionscope", "channeltype"):
        op.execute(f"DROP TYPE IF EXISTS {t}")


def downgrade() -> None:
    # One-way removal — recreating the dropped tables/types/column would
    # need the full original schema (channels/groups/sessions/session_tasks/
    # messages + 4 enum types), and nothing depends on restoring it.
    raise NotImplementedError("060_drop_messaging is a one-way removal")
