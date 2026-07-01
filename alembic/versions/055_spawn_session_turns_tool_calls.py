"""Add ``turns`` + ``tool_calls`` to agent_spawn_sessions.

Per-stint LLM iterations (unique assistant messages) and tool invocations,
captured at session finalize from the SDK ``/usage/status`` (``turns`` also has
a Claude-transcript fallback). They power the granular per-member performance
metrics — distinguishing real effort/iterations from wall-clock. ``DEFAULT 0``
so historical rows (and Grok agents, which have no Claude transcript) read 0,
surfaced as "n/a" in the UI rather than a misleading "0 iterations".

Revision ID: 055_spawn_session_turns
Revises: 054_a2a_message_skill
Create Date: 2026-07-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "055_spawn_session_turns"
down_revision = "054_a2a_message_skill"
branch_labels: dict[str, str] | None = None
depends_on: dict[str, str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_spawn_sessions",
        sa.Column("turns", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "agent_spawn_sessions",
        sa.Column("tool_calls", sa.BigInteger(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("agent_spawn_sessions", "tool_calls")
    op.drop_column("agent_spawn_sessions", "turns")
