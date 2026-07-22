"""Add notifications.reescalation_count/_delivered_count + last_reescalated_at.

`sweep_expired_notifications` used to re-escalate EVERY ack-required
notification past `expires_at` on every ~1min sweep tick, forever — a static
pile of stale notifications produced a fresh blocker_escalation row (+
Telegram DM) per row per tick. These three columns back a per-notification
exponential backoff (first re-escalation at expiry, then doubling from
`notification_reescalation_base_seconds`, capped at 24h, hard-stopped past
`notification_max_reescalations`): `reescalation_count` is the attempt
counter (claimed via compare-and-set even when delivery then fails),
`reescalation_delivered_count` is how many of those attempts actually
reached a recipient, `last_reescalated_at` anchors the backoff interval.
Additive and default-`0`/`NULL`: existing rows read as `count=0`, preserving
today's first-fire semantics.

Revision ID: 079_notification_backoff
Revises: 078_project_codegen_command
Create Date: 2026-07-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "079_notification_backoff"
down_revision = "078_project_codegen_command"
branch_labels: dict[str, str] | None = None
depends_on: dict[str, str] | None = None


def upgrade() -> None:
    op.add_column(
        "notifications",
        sa.Column(
            "reescalation_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "notifications",
        sa.Column(
            "reescalation_delivered_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "notifications",
        sa.Column(
            "last_reescalated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("notifications", "last_reescalated_at")
    op.drop_column("notifications", "reescalation_delivered_count")
    op.drop_column("notifications", "reescalation_count")
