"""Persist ``skill`` on a2a_messages.

Directed A2A ``send()`` accepts a ``skill=`` (the capability the sender is
exercising/requests of the receiver, e.g. ``code_review``) and the gateway
callers (qa / doc / pr_gate) pass it expecting the receiver to learn which
capability the message is about. Until now ``send_chat_message`` never read it
from options, so it was silently dropped — the receiver's inbox showed a bare
message with no skill context. This adds a nullable ``skill`` column so the
capability rides on the row and surfaces in the inbox model.

Revision ID: 054_a2a_message_skill
Revises: 053_playbook_archived_attr
Create Date: 2026-06-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "054_a2a_message_skill"
down_revision = "053_playbook_archived_attr"
branch_labels: dict[str, str] | None = None
depends_on: dict[str, str] | None = None


def upgrade() -> None:
    op.add_column("a2a_messages", sa.Column("skill", sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column("a2a_messages", "skill")
