"""Add system_settings — runtime-editable, panel-tunable settings.

A key-value store for operator settings that must persist across restarts and be
editable from the panel. First user: ``transcript_retention_days``, the window
for the agent-transcript prune sweep. Seeded to 14 so the prune has a value
before anyone touches the panel; code defaults in ``roboco.config`` are the
fallback when a key is absent.

Revision ID: 027_system_settings
Revises: 026_token_usage_tables
Create Date: 2026-06-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "027_system_settings"
down_revision = "026_token_usage_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(length=100), primary_key=True, nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # Seed the retention window so the prune sweep has a value pre-panel.
    op.execute(
        "INSERT INTO system_settings (key, value, updated_at) "
        "VALUES ('transcript_retention_days', '14', now())"
    )


def downgrade() -> None:
    op.drop_table("system_settings")
