"""Add the secretary_directives table — the Secretary's command audit + gate queue.

Every action the Secretary takes on the CEO's behalf is recorded here. Direct
(low-risk) directives are written already-executed; gated (high-impact)
directives are written ``pending`` and wait for the CEO's explicit confirmation
before they run. Forward-only enum-ish status stored as a string.

Revision ID: 035_secretary_directives
Revises: 034_agentrole_secretary
Create Date: 2026-06-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "035_secretary_directives"
down_revision = "034_agentrole_secretary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "secretary_directives",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column(
            "payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")
        ),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="pending"
        ),
        sa.Column("requested_by", sa.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("decided_by", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_secretary_directives_status", "secretary_directives", ["status"]
    )


def downgrade() -> None:
    op.drop_index("ix_secretary_directives_status", table_name="secretary_directives")
    op.drop_table("secretary_directives")
